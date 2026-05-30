# data_utils.py
"""
数据与指标模块：历史 K 线加载、重采样、增量拉取、合并、技术指标计算。

- load_history_data: 从 HISTORY_DIR 下 CSV 加载指定交易对历史，支持多文件合并
- resample_ohlcv: 将 1m 等原始周期重采样为目标周期（如 15m、1H）
- fetch_target_klines: 按目标周期从 OKX 增量拉取 K 线（可基于已有 DataFrame 的末时间）
- parse_kline_df: 统一的 K线原始数据 → DataFrame 转换（消除多处重复代码）
- merge_data / calculate_indicators: 合并后计算 MA/RSI/MACD/KDJ 及可选布林带、ATR、VWAP
- get_or_update_kline: K线数据内存缓存，首次全量加载，后续增量更新
"""
import os
import time
import math
import pandas as pd
import numpy as np
import logging
from datetime import datetime
from typing import List, Dict, Tuple, Optional
from threading import Lock

from config import Config
from okx_client import OKXClient

logger = logging.getLogger(__name__)

# ======== P1: K线数据内存缓存 ========
_kline_cache: Dict[str, pd.DataFrame] = {}
_kline_cache_lock = Lock()


def parse_kline_df(raw: List[List], symbol: str = "", sort: bool = True) -> pd.DataFrame:
    """
    统一的 K线原始数据 → DataFrame 转换函数（P2优化）。
    
    消除 main.py / multi_tf_analysis.py / okx_client.py 中重复的 DataFrame 构造逻辑。
    
    参数:
        raw: OKX API 返回的 K线列表 [[ts, o, h, l, c, vol, vol_ccy, vol_quote, confirm], ...]
        symbol: 可选，插入 instrument_name 列
        sort: 是否按 open_time 排序
    返回:
        标准化的 DataFrame，列: [open_time, open, high, low, close, vol, vol_ccy, vol_quote, confirm]
    """
    if not raw or len(raw) == 0:
        return pd.DataFrame()
    
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close",
        "vol", "vol_ccy", "vol_quote", "confirm"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"].astype(int), unit="ms")
    for col in ["open", "high", "low", "close", "vol", "vol_ccy", "vol_quote"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    if sort:
        df = df.sort_values("open_time").reset_index(drop=True)
    
    if symbol:
        df.insert(0, "instrument_name", symbol)
    
    return df


def get_or_update_kline(
    config: Config,
    client: OKXClient,
    symbol: str,
    bar: str,
    limit: int = 300,
    cache_key: str = None,
) -> pd.DataFrame:
    """
    P1: K线数据内存缓存 — 首次全量加载，后续仅增量拉取新K线追加到缓存。
    
    显著减少文件 I/O 和 API 调用次数。
    
    参数:
        config: 配置对象
        client: OKX API 客户端
        symbol: 交易对
        bar: OKX bar 格式 (如 '15min', '1H')
        limit: 拉取的最大K线数量
        cache_key: 缓存键，默认使用 f"{symbol}_{bar}"
    返回:
        完整的历史+最新K线 DataFrame
    """
    if cache_key is None:
        cache_key = f"{symbol}_{bar}"
    
    with _kline_cache_lock:
        cached = _kline_cache.get(cache_key)
    
    if cached is not None and not cached.empty:
        # 增量更新：只拉取比缓存最新时间更新的K线
        last_ts = cached['open_time'].max()
        after_ts = int(last_ts.timestamp() * 1000)
        
        new_raw = client.get_klines(symbol, bar, limit=limit, after=str(after_ts))
        if not new_raw:
            return cached  # 无新数据
        
        new_df = parse_kline_df(new_raw, symbol=symbol)
        if new_df.empty:
            return cached
        
        # 合并去重
        combined = pd.concat([cached, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["open_time"], keep="last")
        combined = combined.sort_values("open_time").reset_index(drop=True)
        
        # 保持缓存大小限制
        if len(combined) > limit * 3:
            combined = combined.tail(limit * 2)
        
        with _kline_cache_lock:
            _kline_cache[cache_key] = combined
        
        return combined
    
    # 首次加载：从历史文件 + API
    # 注意: bar 是 OKX API 格式 (如 '15m', '1H')，pandas resample 需要 '15min' 格式
    pandas_freq = bar.replace('m', 'min') if bar.endswith('m') and not bar.endswith('min') else bar
    try:
        hist_raw = load_history_data(config.HISTORY_DIR, symbol)
    except Exception:
        hist_raw = pd.DataFrame()
    
    # 从 API 拉取最新数据
    raw = client.get_klines(symbol, bar, limit=limit)
    api_df = parse_kline_df(raw, symbol=symbol) if raw else pd.DataFrame()
    
    if hist_raw.empty and api_df.empty:
        return pd.DataFrame()
    
    if not hist_raw.empty:
        # 重采样到目标周期（使用 pandas 兼容的频率字符串）
        hist_resampled = resample_ohlcv(hist_raw, pandas_freq, symbol)
        combined = pd.concat([hist_resampled, api_df], ignore_index=True)
    else:
        combined = api_df
    
    combined = combined.drop_duplicates(subset=["open_time"], keep="last")
    combined = combined.sort_values("open_time").reset_index(drop=True)
    
    with _kline_cache_lock:
        _kline_cache[cache_key] = combined
    
    return combined


def clear_kline_cache(*keys: str):
    """清除指定或全部K线缓存。"""
    global _kline_cache
    with _kline_cache_lock:
        if not keys:
            _kline_cache.clear()
        else:
            for k in keys:
                _kline_cache.pop(k, None)


def load_history_data(history_dir: str, symbol: str) -> pd.DataFrame:
    """从 history_dir 下所有 CSV 中筛选并合并当前 symbol 的 K 线，去重按时间排序。"""
    all_files = [f for f in os.listdir(history_dir) if f.endswith('.csv')]
    if not all_files:
        raise FileNotFoundError(f"在 {history_dir} 中未找到CSV文件")
    df_list = []
    for file in all_files:
        filepath = os.path.join(history_dir, file)
        try:
            df = pd.read_csv(filepath)
            # 基础必备列（不含 instrument_name）
            base_cols = ['open', 'high', 'low', 'close', 'vol', 'vol_ccy', 'vol_quote', 'open_time', 'confirm']

            if 'instrument_name' in df.columns:
                # 旧文件：有 instrument_name 列，筛选当前交易对
                df = df[df['instrument_name'] == symbol]
                if df.empty:
                    continue
                # 检查基础列是否存在
                if not all(col in df.columns for col in base_cols):
                    logger.warning(f"文件 {file} 缺少必要列，跳过")
                    continue
            else:
                # 新文件：无 instrument_name 列，检查基础列
                if not all(col in df.columns for col in base_cols):
                    logger.warning(f"文件 {file} 列名不匹配（缺少基础列），跳过")
                    continue
                # 添加 instrument_name 列，值为当前交易对
                df['instrument_name'] = symbol

            df_list.append(df)
        except Exception as e:
            logger.warning(f"读取文件 {file} 失败: {e}")

    if not df_list:
        raise ValueError(f"未能从历史数据中读取到交易对 {symbol} 的数据")

    combined = pd.concat(df_list, ignore_index=True)
    combined['open_time'] = pd.to_datetime(combined['open_time'], unit='ms')
    combined = combined.drop_duplicates(subset=['open_time']).sort_values('open_time').reset_index(drop=True)
    logger.info(
        f"历史数据加载完成，总条数: {len(combined)}，时间范围: {combined['open_time'].min()} 至 {combined['open_time'].max()}")
    return combined

def resample_ohlcv(df: pd.DataFrame, target_freq: str, symbol: str) -> pd.DataFrame:
    """将 DataFrame 按 target_freq（如 15min、1H）重采样为 OHLCV，并保留 symbol 列。"""
    df = df.set_index('open_time')
    num_cols = ['open', 'high', 'low', 'close', 'vol', 'vol_ccy', 'vol_quote']
    for col in num_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    ohlc_dict = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'vol': 'sum',
        'vol_ccy': 'sum',
        'vol_quote': 'sum'
    }
    resampled = df.resample(target_freq).agg(ohlc_dict).dropna(how='all')
    resampled = resampled.reset_index()
    resampled['instrument_name'] = symbol
    resampled['confirm'] = 1
    column_order = ['instrument_name', 'open', 'high', 'low', 'close', 'vol', 'vol_ccy', 'vol_quote', 'open_time', 'confirm']
    resampled = resampled[column_order]
    logger.info(f"重采样完成，生成 {len(resampled)} 条 {target_freq} K线")
    return resampled

def fetch_target_klines(config: Config, client: OKXClient, symbol: str, existing_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    下载目标周期的K线数据，支持增量更新。
    symbol: 交易对，如 "BTC-USDT-SWAP"
    existing_df: 已有的历史数据（已重采样为目标周期），用于确定需要下载的时间范围。
    """
    bar = config.TARGET_TIMEFRAME.replace('min', 'm')
    end_ts = int(time.time() * 1000)

    # 确定开始时间戳
    if existing_df is not None and not existing_df.empty:
        # 已有数据的最大时间戳（毫秒）
        last_time = existing_df['open_time'].max()
        start_ts = int(last_time.timestamp() * 1000) + 1  # 从下一条开始
    else:
        # 无历史数据，下载 config.DAYS 天
        start_ts = end_ts - config.DAYS * 24 * 60 * 60 * 1000

    logger.info(f"开始下载 {symbol} {bar} 数据，时间范围: {datetime.fromtimestamp(start_ts/1000)} 至 {datetime.fromtimestamp(end_ts/1000)}")

    all_data = []
    after_ts = None
    while True:
        batch = client.get_klines(symbol, bar, limit=300, after=after_ts)
        if not batch:
            break
        all_data.extend(batch)
        batch_oldest_ts = int(batch[-1][0])
        if batch_oldest_ts <= start_ts:
            break
        after_ts = batch[-1][0]
        time.sleep(0.2)

    if not all_data:
        logger.info("没有新数据需要下载")
        return pd.DataFrame()  # 返回空DataFrame

    df = parse_kline_df(all_data, symbol=symbol)
    df = df[df["open_time"] >= pd.to_datetime(start_ts, unit="ms")].reset_index(drop=True)

    logger.info(f"下载到 {len(df)} 条新K线数据")
    return df

def merge_data(hist_df: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([hist_df, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["open_time"], keep="last")
    combined = combined.sort_values("open_time").reset_index(drop=True)
    logger.info(f"合并后总数据量: {len(combined)} 条")
    return combined


# 高级指标计算函数
def add_advanced_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """增加布林带、ATR、成交量加权平均价等指标"""
    df = df.copy()

    # 布林带 (20,2)
    df['BB_middle'] = df['close'].rolling(window=20).mean()
    df['BB_std'] = df['close'].rolling(window=20).std()
    df['BB_upper'] = df['BB_middle'] + 2 * df['BB_std']
    df['BB_lower'] = df['BB_middle'] - 2 * df['BB_std']
    df['BB_width'] = (df['BB_upper'] - df['BB_lower']) / df['BB_middle']
    df['BB_position'] = (df['close'] - df['BB_lower']) / (df['BB_upper'] - df['BB_lower'])  # 价格在布林带中的位置

    # ATR (14)
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=14).mean()

    # 成交量加权平均价 VWAP (按日计算，这里简化使用整个数据窗口)
    df['VWAP'] = (df['vol_quote'] / df['vol']).fillna(df['close'])  # 注意 vol_quote 是成交额，vol 是成交量

    # 如果存在持仓量数据（需要从外部传入合并），可计算变化率
    # 这里假设 df 中可能包含 'oi' 列（持仓量）
    if 'oi' in df.columns:
        df['oi_change'] = df['oi'].pct_change() * 100  # 持仓量变化百分比

    return df


def calculate_indicators(df: pd.DataFrame, config: Config) -> pd.DataFrame:
    """
    在 K 线 DataFrame 上计算技术指标。必算：MA5/10/30、RSI14、MACD、KDJ；
    若 config.ADVANCED_INDICATORS 为 True，则增加布林带、ATR、VWAP 等。
    """
    df = df.copy()
    df.set_index('open_time', inplace=True)

    # ---- 基础指标 ----
    # 移动平均线
    df['MA5'] = df['close'].rolling(window=config.MA_FAST).mean()
    df['MA10'] = df['close'].rolling(window=config.MA_PERIOD).mean()
    df['MA30'] = df['close'].rolling(window=config.MA_SLOW).mean()

    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=config.RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=config.RSI_PERIOD).mean()
    rs = gain / loss
    df['RSI14'] = 100 - (100 / (1 + rs))

    # MACD
    exp12 = df['close'].ewm(span=12, adjust=False).mean()
    exp26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp12 - exp26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['Signal']

    # KDJ
    low_min = df['low'].rolling(window=9).min()
    high_max = df['high'].rolling(window=9).max()
    rsv = (df['close'] - low_min) / (high_max - low_min) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(alpha=1/3, adjust=False).mean()
    d = k.ewm(alpha=1/3, adjust=False).mean()
    j = 3 * k - 2 * d
    df['K'] = k
    df['D'] = d
    df['J'] = j

    # ---- 高级指标（如果启用） ----
    if config.ADVANCED_INDICATORS:
        df = add_advanced_indicators(df)

    df.reset_index(inplace=True)
    return df