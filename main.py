# main.py
"""日线分析管道入口 —— 获取昨天日线 → 指标 → 形态/背离 → 操盘 → 情绪 → AI → 输出"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from config import Config
from logger import setup_logger
from okx_client import OKXClient
from indicators import calculate_all as calculate_indicators
from patterns import (
    detect_candlestick_patterns,
    detect_rsi_divergence,
    detect_macd_divergence,
    detect_ma_alignment,
)
from manipulation.daily_engine import run_daily_manipulation
from sentiment import analyze_sentiment
from ai_analysis import analyze_daily_with_ai
from liquidation_hunter import analyze_liquidation_hunt


def load_config() -> Config:
    return Config("config.json")


def _init_logging(cfg: Config):
    logger = setup_logger(cfg)
    logger.info("=" * 60)
    logger.info("  日线分析系统启动")
    logger.info("=" * 60)
    return logger


def _fetch_and_prepare(client: OKXClient, symbol: str, lookback: int) -> pd.DataFrame:
    """
    获取日线 K 线数据并转为 DataFrame（时间升序）。
    """
    log = logging.getLogger(__name__)
    log.info(f"获取 {symbol} 日线数据 (limit={lookback})...")
    raw = client.get_klines(symbol, bar="1D", limit=lookback)
    if not raw:
        log.error(f"未获取到 {symbol} 的日线数据")
        return pd.DataFrame()

    rows = client.parse_klines(raw)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=False)  # 保留为列
    log.info(f"获取到 {len(df)} 根日线 ({df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]})")
    return df


def analyze_daily(symbol: str = None, cfg: Config = None, client: OKXClient = None) -> Dict[str, Any]:
    """
    日线分析主入口。

    流程: 拉取日线 → 指标 → 形态/背离 → 操盘 → 情绪 → AI → 汇总输出

    返回完整的分析结果字典。
    """
    if cfg is None:
        cfg = load_config()
    _close_client = client is None
    if client is None:
        client = OKXClient(cfg)

    log = logging.getLogger(__name__)

    if symbol is None:
        symbol = cfg.SYMBOLS[0] if cfg.SYMBOLS else "LAB-USDT-SWAP"

    lookback = cfg.DAILY_LOOKBACK

    try:
        # ═══ 1. 获取日线 ═══
        df = _fetch_and_prepare(client, symbol, lookback)
        if df.empty:
            return {"error": f"无法获取{symbol}日线数据", "symbol": symbol}

        # ═══ 2. 计算技术指标 ═══
        log.info("计算技术指标...")
        df = calculate_indicators(df, advanced=cfg.ADVANCED_INDICATORS)

        # ═══ 3. 确定昨天位置 ═══
        last_date = df["timestamp"].iloc[-1]
        log.info(f"最新K线日期: {last_date}")

        # 昨天 = 倒数第2根（最新一根可能是今天未完成的）
        if len(df) >= 2:
            yesterday_idx = len(df) - 2
        else:
            yesterday_idx = len(df) - 1

        yesterday = df.iloc[yesterday_idx]
        yesterday_date = yesterday["timestamp"]
        yesterday_close = float(yesterday["close"])
        yesterday_open  = float(yesterday["open"])
        yesterday_high  = float(yesterday["high"])
        yesterday_low   = float(yesterday["low"])
        yesterday_vol   = float(yesterday["vol"])
        yesterday_change = (yesterday_close - yesterday_open) / yesterday_open * 100 if yesterday_open > 0 else 0

        log.info(f"分析目标: {yesterday_date} | O={yesterday_open:.6f} H={yesterday_high:.6f} "
                 f"L={yesterday_low:.6f} C={yesterday_close:.6f} 涨跌={yesterday_change:+.2f}%")

        # ═══ 4. K线形态 ═══
        log.info("检测K线形态...")
        patterns = detect_candlestick_patterns(df, lookback=10)

        # ═══ 5. 背离 ═══
        log.info("检测背离...")
        rsi_div = detect_rsi_divergence(df)
        macd_div = detect_macd_divergence(df)
        divergence = {
            "rsi": rsi_div,
            "macd": macd_div,
        }

        # ═══ 6. 均线排列 ═══
        ma_alignment = detect_ma_alignment(df)

        # ═══ 7. 操盘检测 ═══
        log.info("运行日线操盘检测...")
        manipulation = run_daily_manipulation(
            df, symbol=symbol, wick_shadow_ratio=cfg.WICK_SHADOW_RATIO,
        )

        # ═══ 7.5 市场情绪 ═══
        log.info("获取市场情绪数据...")
        sentiment = analyze_sentiment(client, symbol)

        # ═══ 7.6 猎杀爆仓分析 ═══
        liquidation_hunt = None
        if cfg.LIQUIDATION_HUNT_ENABLED:
            log.info("运行猎杀爆仓分析...")
            ls_data = sentiment.get("ls_ratio", {})
            ls_ratio = ls_data.get("current_ratio")
            vwap = float(yesterday.get("VWAP", 0)) if not np.isnan(yesterday.get("VWAP", np.nan)) else None
            current_price = float(df["close"].iloc[-1])

            liquidation_hunt = analyze_liquidation_hunt(
                ls_ratio=ls_ratio,
                vwap=vwap,
                current_price=current_price,
                leverage_levels=cfg.HUNT_LEVERAGE_LEVELS,
                ls_long_extreme=cfg.LS_LONG_EXTREME,
                ls_short_extreme=cfg.LS_SHORT_EXTREME,
            ).to_dict()

        # ═══ 8. AI 分析 ═══
        log.info("请求 AI 分析...")
        ai_result = analyze_daily_with_ai(
            cfg, symbol, df, yesterday_idx,
            manipulation=manipulation,
            patterns=patterns,
            divergence=divergence,
            ma_alignment=ma_alignment,
            sentiment=sentiment,
        )

        # ═══ 9. 组装结果 ═══
        result = {
            "symbol": symbol,
            "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "yesterday_date": str(yesterday_date),
            "yesterday": {
                "open": round(yesterday_open, 6),
                "high": round(yesterday_high, 6),
                "low": round(yesterday_low, 6),
                "close": round(yesterday_close, 6),
                "vol": round(yesterday_vol, 2),
                "change_pct": round(yesterday_change, 2),
            },
            "current_price": float(df["close"].iloc[-1]) if not df.empty else 0,
            "indicators": {
                "RSI14": round(float(yesterday.get("RSI14", np.nan)), 1) if not np.isnan(yesterday.get("RSI14", np.nan)) else None,
                "MA5": round(float(yesterday.get("MA5", np.nan)), 6) if not np.isnan(yesterday.get("MA5", np.nan)) else None,
                "MA10": round(float(yesterday.get("MA10", np.nan)), 6) if not np.isnan(yesterday.get("MA10", np.nan)) else None,
                "MA30": round(float(yesterday.get("MA30", np.nan)), 6) if not np.isnan(yesterday.get("MA30", np.nan)) else None,
                "MA60": round(float(yesterday.get("MA60", np.nan)), 6) if not np.isnan(yesterday.get("MA60", np.nan)) else None,
                "MACD_Hist": round(float(yesterday.get("MACD_Hist", 0)), 6),
                "K": round(float(yesterday.get("K", np.nan)), 1) if not np.isnan(yesterday.get("K", np.nan)) else None,
                "D": round(float(yesterday.get("D", np.nan)), 1) if not np.isnan(yesterday.get("D", np.nan)) else None,
                "J": round(float(yesterday.get("J", np.nan)), 1) if not np.isnan(yesterday.get("J", np.nan)) else None,
                "BB_position": round(float(yesterday.get("BB_position", np.nan)), 2) if not np.isnan(yesterday.get("BB_position", np.nan)) else None,
                "ATR": round(float(yesterday.get("ATR", np.nan)), 6) if not np.isnan(yesterday.get("ATR", np.nan)) else None,
                "VWAP": round(float(yesterday.get("VWAP", np.nan)), 6) if not np.isnan(yesterday.get("VWAP", np.nan)) else None,
            },
            "ma_alignment": ma_alignment,
            "patterns": patterns[-5:] if patterns else [],
            "divergence": divergence,
            "manipulation": manipulation,
            "sentiment": sentiment,
            "liquidation_hunt": liquidation_hunt,
            "ai_analysis": ai_result,
            # K线数据供前端画图（最近60根）
            "kline": _make_kline_for_chart(df.tail(60)),
        }

        # ═══ 10. 清理 NaN → 合法 JSON ═══
        result = _sanitize_for_json(result)

        # ═══ 11. 保存输出 ═══
        _save_result(result, cfg)
        _print_summary(result)

        return result

    except Exception as e:
        log.exception(f"日线分析异常: {e}")
        return {"error": str(e), "symbol": symbol}
    finally:
        if _close_client and hasattr(client, "_session"):
            try:
                client._session.close()
            except Exception:
                pass


def _sanitize_for_json(obj):
    """递归将 NaN / Infinity 替换为 None，确保可序列化为合法 JSON。"""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    return obj


def _make_kline_for_chart(df: pd.DataFrame) -> list:
    """将 DataFrame 转为前端 ECharts 所需的 K 线数据格式。"""
    rows = []
    for _, row in df.iterrows():
        ts = row.get("timestamp")
        if hasattr(ts, "strftime"):
            time_label = ts.strftime("%m-%d")
        else:
            time_label = str(ts)[:10]
        rows.append({
            "time": time_label,
            "open":  float(row["open"]),
            "close": float(row["close"]),
            "high":  float(row["high"]),
            "low":   float(row["low"]),
        })
    return rows


def _save_result(result: Dict, cfg: Config):
    """保存分析结果到 JSON 文件。"""
    out_dir = cfg.OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    date_str = str(result.get("yesterday_date", "")).replace(":", "").replace(" ", "_")[:10]
    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")
    symbol = result.get("symbol", "UNKNOWN").replace("-", "_")
    filename = os.path.join(out_dir, f"daily_{symbol}_{date_str}.json")
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        logging.getLogger(__name__).info(f"结果已保存: {filename}")
    except Exception as e:
        logging.getLogger(__name__).warning(f"保存结果失败: {e}")


def _print_summary(result: Dict):
    """控制台打印分析摘要。"""
    log = logging.getLogger(__name__)
    y = result.get("yesterday", {})
    ai = result.get("ai_analysis", {})
    manip = result.get("manipulation", {})
    phase = manip.get("phase_result", {})

    direction = ai.get("direction", "neutral")
    strength = ai.get("strength", "medium")
    dir_cn = {"long": "🟢做多", "short": "🔴做空", "neutral": "⚪观望"}.get(direction, "⚪观望")
    str_cn = {"strong": "强", "medium": "中等", "weak": "弱"}.get(strength, "中等")

    log.info("=" * 70)
    log.info(f"  📅 {result.get('yesterday_date','?')} | {result.get('symbol','')}")
    log.info(f"  📊 O:{y.get('open',0):.6f} H:{y.get('high',0):.6f} "
             f"L:{y.get('low',0):.6f} C:{y.get('close',0):.6f} "
             f"({y.get('change_pct',0):+.2f}%)")
    log.info(f"  🤖 AI: {dir_cn} | 强度:{str_cn} | 入场:{ai.get('entry') or 'N/A'} | "
             f"止损:{ai.get('stop_loss') or 'N/A'} | 止盈1:{ai.get('take_profit1') or 'N/A'}")
    log.info(f"  🎯 操盘: {phase.get('phase_cn','?')} | "
             f"多方:{phase.get('weighted_bull',0):.2f} 空方:{phase.get('weighted_bear',0):.2f}")

    sentiment = result.get("sentiment", {})
    if sentiment and sentiment.get("summary_text"):
        log.info(f"  💹 情绪: {sentiment.get('overall_bias','?')}")
        funding = sentiment.get("funding", {})
        oi = sentiment.get("oi", {})
        ls = sentiment.get("ls_ratio", {})
        if funding.get("current") is not None:
            ls_ratio_val = ls.get('current_ratio')
            ls_str = f"{ls_ratio_val:.2f}:1" if isinstance(ls_ratio_val, (int, float)) else "?:1"
            log.info(f"     费率 {funding['current']:.4%} ({funding.get('trend','?')}) "
                     f"| OI {oi.get('trend','?')} "
                     f"| 多空比 {ls_str}")

    patterns = result.get("patterns", [])
    if patterns:
        p_str = ", ".join(f"{p['name']}({p.get('direction','?')})" for p in patterns)
        log.info(f"  📈 形态: {p_str}")

    ma = result.get("ma_alignment", {})
    if ma and ma.get("detail"):
        log.info(f"  📏 {ma['detail']}")

    div = result.get("divergence", {})
    rsi_div = div.get("rsi", {}).get("detail", "")
    macd_div = div.get("macd", {}).get("detail", "")
    if rsi_div:
        log.info(f"  ⚡ RSI背离: {rsi_div}")
    if macd_div:
        log.info(f"  ⚡ MACD背离: {macd_div}")

    wyckoff = manip.get("wyckoff", {})
    if wyckoff.get("events"):
        log.info(f"  🏗 威科夫: {'; '.join(wyckoff['events'])}")

    wicks = manip.get("wicks", [])
    if wicks:
        log.info(f"  ⚡ 插针检测: 共 {len(wicks)} 次")
        for w in wicks[-3:]:
            log.info(f"     {w}")
    else:
        log.info(f"  ⚡ 插针检测: 未发现明显插针")

    # ── 猎杀爆仓信号 ──
    hunt = result.get("liquidation_hunt")
    if hunt and hunt.get("mode") != "none":
        log.info(f"  💀 猎杀爆仓: {hunt.get('mode_cn','?')}")
        log.info(f"     多空比 {hunt.get('ls_ratio',0):.2f}:1 | VWAP {hunt.get('vwap',0):.6f}")
        log.info(f"     推荐挂单: {hunt.get('recommended_entry',0):.6f} "
                 f"| 止损: {hunt.get('stop_loss',0):.6f} "
                 f"| 止盈: {hunt.get('take_profit',0):.6f} "
                 f"| 盈亏比 1:{hunt.get('risk_reward_ratio',0):.1f}")
        for z in hunt.get("liquidation_zones", []):
            log.info(f"     爆仓区: {z.get('description','?')}")

    log.info("=" * 70)


# ══════════════ CLI 入口 ══════════════

if __name__ == "__main__":
    cfg = load_config()
    _init_logging(cfg)
    log = logging.getLogger(__name__)

    symbol = sys.argv[1] if len(sys.argv) > 1 else cfg.SYMBOLS[0]
    log.info(f"手动运行日线分析: {symbol}")
    analyze_daily(symbol=symbol, cfg=cfg)
