# main.py
"""
主程序模块：OKX 永续合约交易助手核心流程。
P3优化：管道化拆分 + P1线程池复用 + K线内存缓存 + 长周期增量更新

管道阶段:
  Stage 1: _stage_load_data     — K线加载/合并/指标计算
  Stage 2: _stage_fetch_aux     — 并行拉取辅助数据（15+数据源）
  Stage 3: _stage_extract       — 预提取公共指标
  Stage 4: _stage_analyze       — 并行规则/技术/操盘分析
  Stage 5: _stage_post_process  — AI/共振/冲突/过滤/写入
"""
import csv
import os
import sys
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

from config import Config
from logger import setup_logger
from okx_client import OKXClient
from trade_client import TradeClient
from order_utils import calc_contracts_from_amount
from data_utils import (
    load_history_data,
    resample_ohlcv,
    fetch_target_klines,
    merge_data,
    calculate_indicators,
    parse_kline_df,
    get_or_update_kline,
)
from ai_analysis import ai_analysis
from grid_manager import GridManager
from signal_utils import normalize_signal, resolve_signal_conflicts, signal_post_filter
from multi_tf_analysis import multi_timeframe_confluence
from technical_analysis import run_technical_batch
from data_cache import fetch_or_cache

logger = logging.getLogger(__name__)

# ======== P1: 模块级线程池（生命周期内复用，避免每轮重建） ========
_data_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="data")
_analysis_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="analysis")

# ======== P1: 长周期趋势内存缓存 ========
_long_term_cache: Dict[str, pd.DataFrame] = {}
_long_term_lock = threading.Lock()

# --- 模块级常量 ---
FREQ_MAP = {
    '1m': '1min', '3m': '3min', '5m': '5min', '15m': '15min', '30m': '30min',
    '1H': '1H', '4H': '4H', '6H': '6H', '12H': '12H',
    '1D': '1D', '1W': '1W', '1M': '1M'
}

_file_lock = threading.Lock()


def get_signal_filepath(config: Config, symbol: str) -> str:
    clean_symbol = symbol.replace('/', '_')
    return os.path.join(config.OUTPUT_DIR, f"点位+网格+{clean_symbol}.txt")


def save_signal_to_txt(config: Config, symbol: str, signal: Dict) -> None:
    """将当前信号追加写入该交易对对应的「点位+网格」文本文件。"""
    filepath = get_signal_filepath(config, symbol)
    dirname = os.path.dirname(filepath)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _file_lock:
            with open(filepath, 'a', encoding='utf-8') as f:
                if not signal or signal.get("direction") == "neutral":
                    line = f"{timestamp} | 无信号\n"
                else:
                    direction = signal['direction']
                    dir_cn = '做多' if direction == 'long' else '做空'
                    line = (f"{timestamp} | {dir_cn} | "
                            f"入场:{signal['entry']:.4f} | "
                            f"止损:{signal['stop_loss']:.4f} | "
                            f"止盈1:{signal.get('take_profit1','N/A')} | "
                            f"止盈2:{signal.get('take_profit2','N/A')}\n")
                f.write(line)

                market_state = signal.get("market_state") if signal else None
                if market_state and market_state != "未知":
                    f.write(f"{timestamp} | 市场状态: {market_state}\n")

                manipulation = signal.get("manipulation") if signal else None
                if manipulation:
                    DIR_MAP = {"long": "做多", "short": "做空", "neutral": "观望"}
                    phase = manipulation.get("phase_result", {})
                    next_mv = manipulation.get("next_move", {})
                    wyckoff = manipulation.get("wyckoff", {})
                    predict = manipulation.get("predicted_point", {})

                    f.write(f"{timestamp} | 【操盘分析】\n")
                    if phase:
                        f.write(f"{timestamp} |   阶段: {phase.get('phase_cn','N/A')} (评分:{phase.get('score',0)} 置信:{phase.get('confidence',0)})\n")
                        if phase.get('signals'):
                            f.write(f"{timestamp} |   信号: {'; '.join(phase['signals'])}\n")
                    if wyckoff and wyckoff.get('schematic') != 'none':
                        f.write(f"{timestamp} |   威科夫: {wyckoff.get('schematic')} | {wyckoff.get('detail','')}\n")
                    if next_mv:
                        mv_dir = DIR_MAP.get(next_mv.get('direction',''), next_mv.get('direction',''))
                        f.write(f"{timestamp} |   下一步: {next_mv.get('next_action','N/A')} ({mv_dir})\n")
                        f.write(f"{timestamp} |   庄家目标价: {next_mv.get('target_price','N/A')} | 底线: {next_mv.get('stop_price','N/A')}\n")
                    if predict:
                        pd_dir = DIR_MAP.get(predict.get('ensemble_direction',''), predict.get('ensemble_direction',''))
                        f.write(f"{timestamp} |   综合预测点位: {predict.get('ensemble_target','N/A')} ({pd_dir} 置信:{predict.get('confidence',0):.0%})\n")
                        f.write(f"{timestamp} |   距当前价偏移: {predict.get('distance_pct','N/A')}%\n")
    except Exception as e:
        logger.error(f"写入点位文件失败: {e}")


def save_signals_history(
    config: Config, symbol: str, rule_signal: Dict, ai_signal: Dict, current_price: float
) -> None:
    """将规则信号与 AI 信号及当前价格追加写入 OUTPUT_DIR/signals_history.csv。"""
    filepath = os.path.join(config.OUTPUT_DIR, "signals_history.csv")
    file_exists = os.path.isfile(filepath)
    timestamp = datetime.now().isoformat()

    manip = ai_signal.get('manipulation', {}) if ai_signal else {}
    phase = manip.get('phase_result', {})
    next_mv = manip.get('next_move', {})
    predict = manip.get('predicted_point', {})

    row = {
        'timestamp': timestamp, 'symbol': symbol,
        'rule_direction': rule_signal.get('direction') if rule_signal else '',
        'rule_entry': rule_signal.get('entry') if rule_signal else '',
        'rule_stop_loss': rule_signal.get('stop_loss') if rule_signal else '',
        'rule_take_profit1': rule_signal.get('take_profit1') if rule_signal else '',
        'rule_take_profit2': rule_signal.get('take_profit2') if rule_signal else '',
        'rule_strength': rule_signal.get('strength') if rule_signal else '',
        'rule_market_state': rule_signal.get('market_state') if rule_signal else '',
        'ai_direction': ai_signal.get('direction') if ai_signal else '',
        'ai_entry': ai_signal.get('entry') if ai_signal else '',
        'ai_stop_loss': ai_signal.get('stop_loss') if ai_signal else '',
        'ai_take_profit1': ai_signal.get('take_profit1') if ai_signal else '',
        'ai_take_profit2': ai_signal.get('take_profit2') if ai_signal else '',
        'ai_strength': ai_signal.get('strength') if ai_signal else '',
        'ai_market_state': ai_signal.get('market_state') if ai_signal else '',
        'manip_phase': phase.get('phase_cn', ''),
        'manip_score': phase.get('score', ''),
        'manip_signals': '; '.join(phase.get('signals', [])),
        'manip_next_action': next_mv.get('next_action', ''),
        'manip_target': next_mv.get('target_price', ''),
        'manip_direction': next_mv.get('direction', ''),
        'predict_target': predict.get('ensemble_target', ''),
        'predict_direction': predict.get('ensemble_direction', ''),
        'predict_confidence': predict.get('confidence', ''),
        'current_price': current_price
    }

    with _file_lock:
        with open(filepath, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)


# ================================================================
# P3: 管道阶段拆分
# ================================================================

def _stage_load_data(config: Config, client: OKXClient, symbol: str) -> Optional[pd.DataFrame]:
    """
    Stage 1: K线数据加载（P1内存缓存优化）
    - 首次全量加载到内存缓存，后续仅增量更新
    """
    try:
        target_freq = FREQ_MAP.get(config.TARGET_TIMEFRAME)
        if not target_freq:
            logger.error(f"不支持的目标周期: {config.TARGET_TIMEFRAME}")
            return None

        bar = config.TARGET_TIMEFRAME.replace('min', 'm')
        df = get_or_update_kline(config, client, symbol, bar, limit=300,
                                 cache_key=f"{symbol}_{bar}_main")

        if df.empty:
            # 降级：传统加载方式
            logger.info(f"为 {symbol} 加载历史数据...")
            hist_raw = load_history_data(config.HISTORY_DIR, symbol)
            hist_resampled = resample_ohlcv(hist_raw, target_freq, symbol)
            logger.info("下载最新数据...")
            new_data = fetch_target_klines(config, client, symbol, existing_df=hist_resampled)
            df = merge_data(hist_resampled, new_data)
        else:
            logger.info(f"为 {symbol} 使用内存缓存K线 ({len(df)} 根)")

        df = calculate_indicators(df, config)
        return df
    except Exception as e:
        logger.error(f"Stage 1 数据加载失败: {e}")
        return None


def _stage_fetch_aux(config: Config, client: OKXClient, symbol: str) -> Dict:
    """
    Stage 2: 并行拉取辅助数据（15+数据源，复用模块级线程池）
    """
    futures_map = {}
    # 实时数据（不缓存，TTL=0）
    futures_map['orderbook'] = _data_executor.submit(fetch_or_cache, 'orderbook', client.get_orderbook, symbol, config.ORDERBOOK_DEPTH)
    futures_map['funding'] = _data_executor.submit(fetch_or_cache, 'funding', client.get_funding_rate, symbol)
    futures_map['mark'] = _data_executor.submit(fetch_or_cache, 'mark', client.get_mark_price, symbol)
    futures_map['limit'] = _data_executor.submit(fetch_or_cache, 'limit', client.get_price_limit, symbol)

    # 快速变化数据
    futures_map['oi_data'] = _data_executor.submit(fetch_or_cache, 'oi_data', client.get_open_interest_history, symbol, config.OPEN_INTEREST_PERIOD, limit=config.OI_LIMIT)
    futures_map['taker_vol'] = _data_executor.submit(fetch_or_cache, 'taker_vol', client.get_taker_volume_contract, symbol, config.BIGDATA_PERIOD, limit=config.BIGDATA_LIMIT)
    futures_map['long_short'] = _data_executor.submit(fetch_or_cache, 'long_short', client.get_long_short_account_ratio, symbol, config.BIGDATA_PERIOD, limit=config.BIGDATA_LIMIT)
    futures_map['elite'] = _data_executor.submit(fetch_or_cache, 'elite', client.get_top_trader_long_short_account_ratio, symbol, config.BIGDATA_PERIOD, limit=config.BIGDATA_LIMIT)

    # 多周期数据（P1: 300s TTL，减少API调用）
    if getattr(config, 'ENABLE_ELITE_TREND_MULTI_TF', True):
        for tf in ['15m', '1H', '4H']:
            futures_map[f'oi_data_{tf}'] = _data_executor.submit(fetch_or_cache, f'oi_data_{tf}', client.get_open_interest_history, symbol, tf, limit=config.OI_LIMIT)
            futures_map[f'taker_vol_{tf}'] = _data_executor.submit(fetch_or_cache, f'taker_vol_{tf}', client.get_taker_volume_contract, symbol, tf, limit=config.BIGDATA_LIMIT)
            futures_map[f'long_short_{tf}'] = _data_executor.submit(fetch_or_cache, f'long_short_{tf}', client.get_long_short_account_ratio, symbol, tf, limit=config.BIGDATA_LIMIT)

    # 慢速/可选数据
    inst_family = symbol.replace('-USDT-SWAP', '-USDT').replace('-USD-SWAP', '-USD').replace('-USDC-SWAP', '-USDC')
    futures_map['insurance'] = _data_executor.submit(fetch_or_cache, 'insurance', client.get_insurance_fund, "SWAP", inst_family, limit=5)
    if getattr(config, 'ENABLE_PREMIUM', False):
        futures_map['premium'] = _data_executor.submit(fetch_or_cache, 'premium', client.get_premium_history, symbol, limit=1)
    futures_map['funding_hist'] = _data_executor.submit(fetch_or_cache, 'funding_hist', client.get_funding_rate_history, symbol, limit=24)

    index_id = symbol.replace('-USDT-SWAP', '-USDT').replace('-USD-SWAP', '-USD')
    futures_map['index_tickers'] = _data_executor.submit(fetch_or_cache, 'index_tickers', client.get_index_tickers, index_id)
    if getattr(config, 'ENABLE_ELITE_TREND_MULTI_TF', True):
        futures_map['index_candles_5m'] = _data_executor.submit(fetch_or_cache, 'index_candles_5m', client.get_index_candles, index_id, '5m', 48)
        futures_map['index_candles_1H'] = _data_executor.submit(fetch_or_cache, 'index_candles_1H', client.get_index_candles, index_id, '1H', 24)
        futures_map['mark_candles_5m'] = _data_executor.submit(fetch_or_cache, 'mark_candles_5m', client.get_mark_price_candles, symbol, '5m', 48)

    futures_map['elite_pos'] = _data_executor.submit(fetch_or_cache, 'elite_pos', client.get_position_ratio_top_trader, symbol, config.BIGDATA_PERIOD, limit=config.BIGDATA_LIMIT)
    if getattr(config, 'ENABLE_ELITE_TREND_MULTI_TF', True):
        futures_map['elite_trend'] = _data_executor.submit(fetch_or_cache, 'elite_trend', client.get_elite_position_trend, symbol)

    base_ccy = symbol.split('-')[0] if '-' in symbol else ''
    if base_ccy in ('BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'LTC', 'BNB'):
        futures_map['option_oi_strike'] = _data_executor.submit(fetch_or_cache, 'option_oi_strike', client.get_option_oi_strike, base_ccy)
        futures_map['option_pcr'] = _data_executor.submit(fetch_or_cache, 'option_pcr', client.get_option_oi_ratio, base_ccy, period="1H")

    pos_tier_family = symbol.replace('-USDT-SWAP', '-USDT').replace('-USD-SWAP', '-USD')
    futures_map['position_tiers'] = _data_executor.submit(fetch_or_cache, 'position_tiers', client.get_position_tiers, "SWAP", pos_tier_family, "cross")

    # 收集结果
    results = {}
    for key, future in futures_map.items():
        try:
            results[key] = future.result(timeout=20)
        except Exception as e:
            logger.warning(f"获取 {key} 数据失败（已尝试缓存降级）: {e}")
            results[key] = None

    return results


def _stage_get_long_term(config: Config, client: OKXClient, symbol: str) -> tuple:
    """
    P1: 长周期趋势增量更新
    - 首次拉取300根1H K线并缓存，后续仅拉取增量追加
    """
    long_term_df = None
    long_trend = "未知"
    long_ma60 = 0.0

    long_tf = getattr(config, 'LONG_TERM_TIMEFRAME', '1H')
    if not long_tf:
        return long_term_df, long_trend, long_ma60

    cache_key = f"{symbol}_longterm"

    with _long_term_lock:
        cached = _long_term_cache.get(cache_key)

    long_bar = long_tf.replace('min', 'm')
    if cached is not None and not cached.empty:
        # 增量更新
        last_ts = cached['open_time'].max()
        after_ts = int(last_ts.timestamp() * 1000)
        new_raw = client.get_klines(symbol, long_bar, limit=10, after=str(after_ts))
        if new_raw:
            new_df = parse_kline_df(new_raw, symbol=symbol)
            if not new_df.empty:
                combined = pd.concat([cached, new_df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["open_time"], keep="last")
                combined = combined.sort_values("open_time").reset_index(drop=True)
                if len(combined) > 500:
                    combined = combined.tail(400)
                with _long_term_lock:
                    _long_term_cache[cache_key] = combined
                long_term_df = combined
            else:
                long_term_df = cached
        else:
            long_term_df = cached
    else:
        # 首次全量加载
        long_data = client.get_klines(symbol, long_bar, limit=300)
        if long_data:
            long_term_df = parse_kline_df(long_data, symbol=symbol)
            with _long_term_lock:
                _long_term_cache[cache_key] = long_term_df

    if long_term_df is not None and len(long_term_df) >= 60:
        long_ma60 = float(long_term_df['close'].rolling(60).mean().iloc[-1])
        cur_price = float(long_term_df['close'].iloc[-1])
        if pd.notna(long_ma60) and long_ma60 > 0:
            if cur_price > long_ma60 * 1.02:
                long_trend = "上升"
            elif cur_price < long_ma60 * 0.98:
                long_trend = "下降"
            else:
                long_trend = "震荡"

    return long_term_df, long_trend, long_ma60


def _stage_extract_metrics(results: Dict, config: Config) -> Dict:
    """
    Stage 3: 预提取公共指标（供分析模块共享）
    返回一个包含所有预提取值的字典。
    """
    orderbook = results.get('orderbook')
    oi_data = results.get('oi_data') or []
    funding = results.get('funding')
    taker_vol = results.get('taker_vol') or []
    long_short = results.get('long_short') or []
    elite = results.get('elite') or []
    mark = results.get('mark')
    limit = results.get('limit')
    premium = results.get('premium') or []
    insurance = results.get('insurance') or []
    funding_hist = results.get('funding_hist') or []
    index_tickers = results.get('index_tickers') or {}
    elite_pos = results.get('elite_pos') or []
    option_oi_strike = results.get('option_oi_strike') or []
    option_pcr = results.get('option_pcr') or []
    position_tiers = results.get('position_tiers') or []
    elite_trend = results.get('elite_trend') or {}

    # 多周期数据
    multi_tf_oi = {}
    multi_tf_taker = {}
    multi_tf_ls = {}
    for tf in ['15m', '1H', '4H']:
        multi_tf_oi[tf] = results.get(f'oi_data_{tf}') or []
        multi_tf_taker[tf] = results.get(f'taker_vol_{tf}') or []
        multi_tf_ls[tf] = results.get(f'long_short_{tf}') or []

    index_candles_5m = results.get('index_candles_5m') or []
    index_candles_1H = results.get('index_candles_1H') or []
    mark_candles_5m = results.get('mark_candles_5m') or []

    # 提取数值指标
    funding_rate = float(funding.get('fundingRate', 0)) if funding else 0
    ls_ratio = float(long_short[0][1]) if long_short and len(long_short) > 1 else 0
    elite_ratio = float(elite[0][1]) if elite and len(elite) > 1 else 0

    latest_oi = float(oi_data[0][1]) if oi_data and len(oi_data[0]) > 1 else 0
    oi_change = 0.0
    if oi_data and len(oi_data) >= 2:
        prev_oi = float(oi_data[1][1]) if len(oi_data[1]) > 1 else 0
        if prev_oi != 0:
            oi_change = (latest_oi - prev_oi) / prev_oi

    net_taker = 0.0
    if taker_vol and len(taker_vol) >= 1 and len(taker_vol[0]) >= 3:
        net_taker = float(taker_vol[0][2]) - float(taker_vol[0][1])

    premium_val = float(premium[0][1]) if premium and len(premium) > 0 else 0

    bid_ask_ratio = 0.0
    if orderbook and orderbook.get('bids') and orderbook.get('asks'):
        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])
        if bids and asks:
            bid_size = float(bids[0][1])
            ask_size = float(asks[0][1])
            bid_ask_ratio = bid_size / ask_size if ask_size > 0 else 0

    idx_price = float(index_tickers.get('idxPx', 0)) if index_tickers else 0

    # 标记价偏离度
    mark_deviation = 0.0
    if mark:
        try:
            mark_px = float(mark.get('markPx', 0))
            # current_price not available yet here, will compute after ticker
        except (ValueError, TypeError):
            pass

    # 情绪数据
    sentiment_data = None
    if getattr(config, 'SENTIMENT_ENABLED', True):
        try:
            from sentiment_analysis import compute_sentiment_fear_gauge
            sentiment_data = compute_sentiment_fear_gauge(
                funding_rate=funding_rate,
                funding_rate_history=funding_hist,
                option_pcr_data=option_pcr,
                ls_ratio=ls_ratio,
            )
        except Exception:
            pass

    return {
        "orderbook": orderbook, "oi_data": oi_data, "funding": funding,
        "taker_vol": taker_vol, "long_short": long_short, "elite": elite,
        "mark": mark, "limit": limit, "premium": premium, "insurance": insurance,
        "funding_hist": funding_hist, "index_tickers": index_tickers,
        "elite_pos": elite_pos, "option_oi_strike": option_oi_strike,
        "option_pcr": option_pcr, "position_tiers": position_tiers,
        "elite_trend": elite_trend, "sentiment_data": sentiment_data,
        "multi_tf_oi": multi_tf_oi, "multi_tf_taker": multi_tf_taker,
        "multi_tf_ls": multi_tf_ls,
        "index_candles_5m": index_candles_5m,
        "index_candles_1H": index_candles_1H,
        "mark_candles_5m": mark_candles_5m,
        # 提取的数值
        "funding_rate": funding_rate, "ls_ratio": ls_ratio,
        "elite_ratio": elite_ratio, "latest_oi": latest_oi,
        "oi_change": oi_change, "net_taker": net_taker,
        "premium_val": premium_val, "bid_ask_ratio": bid_ask_ratio,
        "idx_price": idx_price,
    }


def _stage_run_analysis(
    config: Config, df: pd.DataFrame, current_price: float,
    metrics: Dict, long_trend: str, long_ma60: float, symbol: str = "",
) -> Dict:
    """
    Stage 4: 并行规则策略 + 技术分析 + 操盘检测（复用模块级线程池）
    返回: {"rule": rule_signal, "tech": tech_batch, "manipulation": manipulation_result}
    """
    rule_signal = None
    tech_batch = None
    manipulation_result = None

    analysis_futures = {}

    # 1) 规则策略
    if getattr(config, 'RULE_STRATEGY_ENABLED', False):
        from rule_strategy import generate_rule_signal
        analysis_futures['rule'] = _analysis_executor.submit(
            generate_rule_signal,
            df=df, current_price=current_price, config=config,
            funding_rate=metrics["funding_rate"], ls_ratio=metrics["ls_ratio"],
            elite_ratio=metrics["elite_ratio"], oi=metrics["latest_oi"],
            oi_change=metrics["oi_change"], net_taker=metrics["net_taker"],
            premium=metrics["premium_val"], bid_ask_ratio=metrics["bid_ask_ratio"],
            long_trend=long_trend, long_ma60=long_ma60
        )

    # 2) 技术分析
    if getattr(config, 'TECHNICAL_BATCH_ENABLED', True):
        analysis_futures['tech'] = _analysis_executor.submit(
            run_technical_batch, df, metrics.get("orderbook")
        )

    # 3) 庄家操盘检测
    if getattr(config, 'MANIPULATION_ENABLED', True):
        from manipulation_v2 import run_manipulation_analysis
        # 计算标记价偏离度（需要 current_price）
        mark_deviation = 0.0
        mark = metrics.get("mark")
        if mark and current_price > 0:
            try:
                mark_px = float(mark.get('markPx', 0))
                if mark_px > 0:
                    mark_deviation = (current_price - mark_px) / mark_px
            except (ValueError, TypeError):
                pass

        analysis_futures['manipulation'] = _analysis_executor.submit(
            run_manipulation_analysis,
            df=df, current_price=current_price,
            oi_data=metrics["oi_data"], taker_vol=metrics["taker_vol"],
            funding_rate=metrics["funding_rate"], ls_ratio=metrics["ls_ratio"],
            elite_ratio=metrics["elite_ratio"],
            orderbook=metrics.get("orderbook"),
            insurance_data=metrics["insurance"],
            mark_deviation=mark_deviation,
            funding_rate_history=metrics["funding_hist"],
            index_price=metrics["idx_price"],
            elite_position_data=metrics["elite_pos"],
            option_oi_strike_data=metrics["option_oi_strike"],
            option_pcr_data=metrics["option_pcr"],
            position_tiers_data=metrics["position_tiers"],
            elite_trend_data=metrics["elite_trend"],
            sentiment_data=metrics["sentiment_data"],
            symbol=symbol,
            multi_tf_oi=metrics["multi_tf_oi"],
            multi_tf_taker=metrics["multi_tf_taker"],
            multi_tf_ls=metrics["multi_tf_ls"],
            index_candles_5m=metrics["index_candles_5m"],
            index_candles_1H=metrics["index_candles_1H"],
            mark_candles_5m=metrics["mark_candles_5m"],
            wick_shadow_ratio=getattr(config, 'WICK_SHADOW_RATIO', 3.0),
        )

    for key, future in analysis_futures.items():
        try:
            result = future.result(timeout=60)
            if key == 'rule':
                rule_signal = result
                logger.info(f"规则信号: {rule_signal}")
            elif key == 'tech':
                tech_batch = result
                logger.info(f"技术分析: 背离={tech_batch.get('rsi_divergence',{}).get('type')} "
                            f"形态={[p['name'] for p in tech_batch.get('candlestick_patterns',[])]}")
            elif key == 'manipulation':
                manipulation_result = result
                logger.info(f"操盘分析: 阶段={manipulation_result['phase_result']['phase_cn']} "
                            f"Score={manipulation_result['phase_result']['score']} | "
                            f"下一动作={manipulation_result['next_move']['next_action']} "
                            f"| 目标={manipulation_result['next_move']['target_price']:.4f}")
        except Exception as e:
            logger.warning(f"{key} 分析模块执行失败: {e}")

    return {
        "rule": rule_signal,
        "tech": tech_batch,
        "manipulation": manipulation_result,
    }


def _stage_post_process(
    config: Config, client: OKXClient, df: pd.DataFrame,
    analysis: Dict, current_price: float, symbol: str,
    long_term_df=None, long_trend="未知", long_ma60=0.0,
    metrics: Dict = None,
) -> Dict:
    """
    Stage 5: AI分析 → 多周期共振 → 冲突仲裁 → 后过滤 → 写入
    """
    metrics = metrics or {}
    rule_signal = analysis.get("rule")
    tech_batch = analysis.get("tech")
    manipulation_result = analysis.get("manipulation")

    # ── AI 分析 ──
    signal = ai_analysis(
        config, df, symbol,
        rule_signal=rule_signal,
        orderbook_data=metrics.get("orderbook"),
        oi_data=metrics.get("oi_data"),
        funding_data=metrics.get("funding"),
        taker_volume_data=metrics.get("taker_vol"),
        long_short_ratio_data=metrics.get("long_short"),
        elite_ratio_data=metrics.get("elite"),
        mark_price_data=metrics.get("mark"),
        price_limit_data=metrics.get("limit"),
        premium_history=metrics.get("premium"),
        long_term_df=long_term_df,
        long_trend=long_trend,
        long_ma60=long_ma60,
        tech_batch=tech_batch,
        manipulation=manipulation_result,
    )

    # ── 多周期共振 ──
    confluence_result = None
    if getattr(config, 'MTF_CONFLUENCE_ENABLED', True):
        try:
            confluence_result = multi_timeframe_confluence(config, client, symbol, main_df=df)
            logger.info(f"多周期共振: 方向={confluence_result['direction']} 共振级别={confluence_result['confluence']}/3")
            if signal:
                signal['confluence'] = confluence_result['confluence']
                signal['confluence_direction'] = confluence_result['direction']
                signal['confluence_detail'] = (
                    f"{confluence_result['trend_tf']}趋势={confluence_result['trend_direction']}, "
                    f"{confluence_result['structure_tf']}结构={confluence_result['structure_state']}, "
                    f"{confluence_result['entry_tf']}入场={confluence_result['entry_signal']}"
                )
        except Exception as e:
            logger.warning(f"多周期共振分析失败: {e}")

    # ── 更新操盘分析综合预测 ──
    if manipulation_result:
        try:
            from manipulation_v2 import calculate_manipulation_target
            atr_val = float(df['ATR'].iloc[-1]) if 'ATR' in df.columns else current_price * 0.02
            updated_predict = calculate_manipulation_target(
                df, current_price, atr_val,
                manipulation_result['phase_result'],
                manipulation_result['next_move'],
                ai_signal=signal if signal else None,
            )
            manipulation_result['predicted_point'] = updated_predict
            if signal:
                signal['manipulation'] = manipulation_result
                signal['manipulation_target'] = updated_predict['ensemble_target']
                signal['manipulation_direction'] = updated_predict['ensemble_direction']
        except Exception as e:
            logger.warning(f"更新预测点位失败: {e}")

    # ── 标准化 ──
    signal = normalize_signal(config, signal)

    # ── 信号冲突仲裁 ──
    if signal and signal.get("direction") != "neutral":
        try:
            signal = resolve_signal_conflicts(
                ai_signal=signal,
                rule_signal=rule_signal,
                manipulation_result=manipulation_result,
                confluence_result=confluence_result,
                tech_batch=tech_batch,
            )
            if signal.get('conflict'):
                logger.warning(f"⚠️ 信号冲突已强制 neutral: {signal.get('conflict_detail', {})}")
        except Exception as e:
            logger.warning(f"信号冲突仲裁失败: {e}")

    # ── 后处理过滤 ──
    if signal and signal.get("direction") != "neutral" and current_price > 0:
        try:
            signal = signal_post_filter(signal, df, current_price,
                                        funding_rate=metrics.get("funding_rate", 0),
                                        mark_deviation=manipulation_result.get("mark_deviation", 0) if manipulation_result else 0)
        except Exception as e:
            logger.warning(f"信号后过滤失败: {e}")

    # ── 添加 symbol 和 ATR ──
    if signal:
        signal["symbol"] = symbol
        latest_row = df.iloc[-1] if not df.empty else pd.Series()
        signal["atr"] = latest_row.get("ATR", 0)

    # ── 保存 ──
    if signal:
        save_signals_history(config, symbol, rule_signal, signal, current_price)

    return signal


# ================================================================
# 主入口（管道编排）
# ================================================================

def prepare_data_and_get_signal(config: Config, client: OKXClient, symbol: str) -> Dict:
    """
    P3: 管道化编排 — 按 Stage 1→5 顺序执行数据→分析→信号流程。
    兼容原有调用方式，返回标准化后的信号字典。
    """
    try:
        # Stage 1: K线数据加载
        df = _stage_load_data(config, client, symbol)
        if df is None or df.empty:
            return {}

        # Stage 2: 并行拉取辅助数据
        results = _stage_fetch_aux(config, client, symbol)

        # 获取当前价格
        ticker = client.get_ticker(symbol)
        current_price = float(ticker.get('last', 0)) if ticker else 0
        if current_price <= 0:
            logger.error(f"无法获取 {symbol} 当前价格")
            return {}

        # 长周期趋势（P1增量更新）
        long_term_df, long_trend, long_ma60 = _stage_get_long_term(config, client, symbol)

        # Stage 3: 预提取公共指标
        metrics = _stage_extract_metrics(results, config)

        # Stage 4: 并行分析模块
        analysis = _stage_run_analysis(
            config, df, current_price, metrics, long_trend, long_ma60, symbol
        )

        # Stage 5: 后处理
        signal = _stage_post_process(
            config, client, df, analysis, current_price, symbol,
            long_term_df=long_term_df, long_trend=long_trend, long_ma60=long_ma60,
            metrics=metrics,
        )

        return signal
    except Exception as e:
        logger.error(f"prepare_data_and_get_signal 出错: {e}", exc_info=True)
        return {}


def try_auto_trade(
    config: Config, client: OKXClient, symbol: str, signal: Dict, current_price: float
) -> tuple[bool, str | None]:
    """
    在开启自动下单且信号为多/空时，按配置执行一笔开仓（仅支持 *-SWAP 永续）。
    """
    if not getattr(config, "AUTO_TRADE_ENABLED", False):
        return False, "AUTO_TRADE_ENABLED 为 False，未开启自动下单"
    direction = signal.get("direction")
    if direction not in ("long", "short"):
        return False, "信号方向为观望或无效，跳过自动下单"
    if not symbol.endswith("-SWAP"):
        logger.warning("自动下单仅支持 SWAP 合约，跳过 %s", symbol)
        return False, "仅支持 *-SWAP 永续合约，当前合约不支持自动下单"

    try:
        inst = client.get_instrument_info(symbol)
        if not inst:
            logger.error("自动下单: 无法获取 %s 合约信息", symbol)
            return False, "无法获取合约信息，跳过自动下单"

        size_usdt = getattr(config, "AUTO_TRADE_SIZE_USDT", 10.0)
        try:
            sz = calc_contracts_from_amount(size_usdt, current_price, inst)
        except ValueError as e:
            logger.error("自动下单: %s，跳过 %s", e, symbol)
            return False, f"按金额换算张数失败：{e}"

        entry = signal.get("entry") or current_price
        tp = signal.get("take_profit1") or signal.get("take_profit2")
        sl = signal.get("stop_loss") or None

        # 手续费率
        fee_rate = getattr(config, "FEE_RATE", 0.001)
        try:
            fee_info = client.get_trade_fee(instType="SWAP", instId=symbol)
            data_list = fee_info.get("data") or []
            row = data_list[0] if data_list else fee_info
            maker = row.get("maker")
            taker = row.get("taker")
            maker_f = float(maker) if maker not in (None, "") else None
            taker_f = float(taker) if taker not in (None, "") else None
            ord_type_for_fee = getattr(config, "AUTO_TRADE_ORDER_TYPE", "market")
            if ord_type_for_fee == "limit" and maker_f is not None:
                fee_rate = maker_f
            elif taker_f is not None:
                fee_rate = taker_f
        except Exception:
            pass

        gross_pnl = 0.0
        if tp and entry:
            gross_pnl = size_usdt * ((tp - entry) / entry)
        fees = size_usdt * fee_rate * 2

        funding_cost = 0.0
        try:
            fr = client.get_funding_rate(symbol)
            frate = float(fr.get("fundingRate", 0.0) or 0.0)
            funding_cost = abs(size_usdt * frate)
        except Exception:
            funding_cost = 0.0

        est_net_pnl = gross_pnl - fees - funding_cost

        td_mode = getattr(config, "AUTO_TRADE_TD_MODE", "cross")
        est_liq = ""
        try:
            lev_info = client.get_leverage_info(instId=symbol, mgnMode=td_mode)
            lev_data = (lev_info.get("data") or [None])[0] or {}
            real_lever = lev_data.get("lever") or getattr(config, "DEFAULT_LEVERAGE", 2)
            info = client.get_adjust_leverage_info(
                instType="SWAP", mgnMode=td_mode, lever=real_lever, instId=symbol,
            )
            data = (info.get("data") or [None])[0] or {}
            est_liq = data.get("estLiqPx", "") or ""
        except Exception:
            est_liq = ""

        logger.info(
            "自动下单规划 %s: 金额=%.2f 预估毛利=%.4f 手续费≈%.4f 资金费≈%.4f 预估净利≈%.4f 强平价≈%s 止损=%s 止盈=%s",
            symbol, size_usdt, gross_pnl, fees, funding_cost, est_net_pnl,
            est_liq or "未知", f"{sl:.6f}" if sl else "未设置", f"{tp:.6f}" if tp else "未设置",
        )

        if tp and sl and entry:
            reward = abs(tp - entry)
            risk = abs(entry - sl)
            rr = reward / risk if risk > 0 else 0.0
        else:
            rr = 0.0

        if est_net_pnl <= 0:
            msg = f"预估净利 <= 0（{est_net_pnl:.4f}），不具备正期望"
            logger.warning("自动下单放弃 %s：%s。", symbol, msg)
            return False, msg

        min_rr = getattr(config, "MIN_REWARD_RISK", 1.5)
        if rr and rr < float(min_rr):
            msg = f"奖惩比={rr:.2f} 小于阈值 {float(min_rr):.2f}"
            logger.warning("自动下单放弃 %s：%s。", symbol, msg)
            return False, msg

        try:
            if est_liq and sl:
                liq = float(est_liq)
                if direction == "long" and liq >= sl:
                    msg = f"多单强平价({liq:.6f}) 高于/接近止损({sl:.6f})，风险过高"
                    logger.warning("自动下单放弃 %s：%s。", symbol, msg)
                    return False, msg
                if direction == "short" and liq <= sl:
                    msg = f"空单强平价({liq:.6f}) 低于/接近止损({sl:.6f})，风险过高"
                    logger.warning("自动下单放弃 %s：%s。", symbol, msg)
                    return False, msg
        except Exception:
            pass

        pos_mode = ""
        try:
            cfg_raw = client.get_account_config()
            acct_cfg = (cfg_raw.get("data") or [None])[0] or {}
            pos_mode = acct_cfg.get("posMode", "") or ""
        except Exception:
            pos_mode = ""

        trade = TradeClient(client)
        side = "buy" if direction == "long" else "sell"

        if pos_mode == "long_short_mode":
            pos_side = "long" if direction == "long" else "short"
        else:
            pos_side = None

        ord_type = getattr(config, "AUTO_TRADE_ORDER_TYPE", "market")
        px = None
        if ord_type == "limit":
            entry = signal.get("entry")
            if entry is not None and entry > 0:
                px = str(round(entry, 8))
            else:
                ord_type = "market"

        result = trade.place_order(
            instId=symbol, side=side, ordType=ord_type, sz=sz,
            tdMode=td_mode, px=px, posSide=pos_side,
        )
        data = result.get("data", [{}])
        order_id = data[0].get("ordId", "") if data else ""
        logger.info("自动下单成功 %s %s %s 张 ordId=%s", symbol, side, sz, order_id)
        return True, None
    except Exception as e:
        logger.exception("自动下单失败 %s: %s", symbol, e)
        return False, str(e)


def process_symbol(config: Config, client: OKXClient, grid_manager: GridManager, symbol: str) -> None:
    """为单个交易对生成信号、写入点位文件，若有方向则追加网格建议；若开启自动下单则执行开仓。"""
    try:
        signal = prepare_data_and_get_signal(config, client, symbol)
        save_signal_to_txt(config, symbol, signal)

        if signal and signal.get("direction") != "neutral":
            ticker = client.get_ticker(symbol)
            current_price = float(ticker.get("last", 0)) if ticker else 0
            if current_price > 0:
                grid_suggestion = grid_manager.get_grid_suggestions(current_price, signal)
                with open(get_signal_filepath(config, symbol), "a", encoding="utf-8") as f:
                    f.write(grid_suggestion)
                logger.info(f"网格建议已写入 {symbol} 点位文件")
                success, reason = try_auto_trade(config, client, symbol, signal, current_price)
                if not success and reason:
                    logger.info("自动下单未执行 %s：%s", symbol, reason)
            else:
                logger.error(f"无法获取 {symbol} 当前价格，跳过网格建议")
    except Exception as e:
        logger.error(f"为 {symbol} 生成信号失败: {e}")


def main_loop(config: Config) -> None:
    """主循环：首次全量生成信号，之后按间隔轮询各交易对。"""
    if not config.SYMBOLS:
        logger.error("配置文件中未指定任何交易对")
        sys.exit(1)

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    logger.info(f"输出目录: {config.OUTPUT_DIR}")

    client = OKXClient(config)
    grid_manager = GridManager(
        config=config,
        default_range_percent=getattr(config, "GRID_DEFAULT_RANGE_PERCENT", 0.2),
        default_grid_count=getattr(config, "GRID_DEFAULT_COUNT", 10),
    )
    last_signal_time = {s: datetime.min for s in config.SYMBOLS}

    for symbol in config.SYMBOLS:
        logger.info(f"首次启动，为 {symbol} 生成信号...")
        try:
            process_symbol(config, client, grid_manager, symbol)
            last_signal_time[symbol] = datetime.now()
        except Exception:
            pass

    logger.info("进入主循环，按配置间隔生成信号...")
    interval_seconds = config.INTERVAL_MINUTES * 60

    while True:
        now = datetime.now()
        for symbol in config.SYMBOLS:
            if (now - last_signal_time[symbol]).total_seconds() >= interval_seconds:
                logger.info(f"为 {symbol} 重新生成信号...")
                try:
                    process_symbol(config, client, grid_manager, symbol)
                    last_signal_time[symbol] = now
                except Exception:
                    last_signal_time[symbol] = now

        if now.second == 0 and now.minute % 5 == 0:
            logger.info(f"运行中 {now.strftime('%Y-%m-%d %H:%M:%S')}")

        time.sleep(10)


if __name__ == "__main__":
    config = Config("config.json")
    logger = setup_logger(config)

    if getattr(config, "BACKTEST_ENABLED", False):
        try:
            from backtest import run_backtest
            logger.info("进入回测模式")
            for symbol in config.SYMBOLS:
                run_backtest(config, symbol)
        except ImportError:
            logger.error("回测模块 backtest.py 未找到，请确保文件存在")
            sys.exit(1)
    else:
        try:
            main_loop(config)
        except KeyboardInterrupt:
            logger.info("用户手动中断程序，正在退出...")
            sys.exit(0)
