# indicators.py
"""日线技术指标计算 —— 在 DataFrame 上新增 MA / RSI / MACD / KDJ / BB / ATR / VWAP 列"""

from typing import Optional

import numpy as np
import pandas as pd


def calculate_all(df: pd.DataFrame, advanced: bool = True) -> pd.DataFrame:
    """
    在 df 上原地计算所有技术指标列。
    返回修改后的 df（同时也已原地修改）。

    必需列: open, high, low, close, vol
    """
    c = df["close"].values.astype(float)
    h = df["high"].values.astype(float)
    l_vals = df["low"].values.astype(float)
    v = df["vol"].values.astype(float)

    # ── 均线 ──
    df["MA5"]  = _sma(c, 5)
    df["MA10"] = _sma(c, 10)
    df["MA30"] = _sma(c, 30)
    df["MA60"] = _sma(c, 60)

    # ── RSI(14) ──
    df["RSI14"] = _rsi(c, 14)

    # ── MACD(12,26,9) ──
    ema12 = _ema(c, 12)
    ema26 = _ema(c, 26)
    macd_line = ema12 - ema26
    signal_line = _ema(macd_line, 9)
    df["MACD"]       = macd_line
    df["MACD_Signal"] = signal_line
    df["MACD_Hist"]  = macd_line - signal_line

    # ── KDJ(9,3,3) ──
    df["K"], df["D"], df["J"] = _kdj(h, l_vals, c, 9, 3, 3)

    if advanced:
        # ── 布林带(20,2) ──
        bb_mid, bb_upper, bb_lower, bb_width, bb_pos = _bollinger(c, 20, 2)
        df["BB_upper"]   = bb_upper
        df["BB_lower"]   = bb_lower
        df["BB_mid"]     = bb_mid
        df["BB_width"]   = bb_width
        df["BB_position"] = bb_pos

        # ── ATR(14) ──
        df["ATR"] = _atr(h, l_vals, c, 14)

        # ── VWAP ──
        typical = (h + l_vals + c) / 3.0
        df["VWAP"] = _vwap(typical, v)

    return df


# ══════════════ 基础函数 ══════════════

def _sma(arr: np.ndarray, period: int) -> np.ndarray:
    result = np.full(len(arr), np.nan)
    for i in range(period - 1, len(arr)):
        result[i] = np.mean(arr[i - period + 1 : i + 1])
    return result


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    result = np.full(len(arr), np.nan)
    multiplier = 2 / (period + 1)
    # 第一个有效值用 SMA
    if len(arr) >= period:
        result[period - 1] = np.mean(arr[:period])
        for i in range(period, len(arr)):
            result[i] = (arr[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.full(len(close), np.nan)
    avg_loss = np.full(len(close), np.nan)
    if len(close) > period:
        avg_gain[period] = np.mean(gain[1 : period + 1])
        avg_loss[period] = np.mean(loss[1 : period + 1])
        for i in range(period + 1, len(close)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period
    rs = np.divide(avg_gain, avg_loss, out=np.full_like(avg_gain, np.nan), where=avg_loss != 0)
    return 100 - (100 / (1 + rs))


def _kdj(high: np.ndarray, low: np.ndarray, close: np.ndarray,
         n: int = 9, m1: int = 3, m2: int = 3):
    """返回 (K, D, J) 三个数组"""
    n_len = len(close)
    k_arr = np.full(n_len, np.nan)
    d_arr = np.full(n_len, np.nan)
    j_arr = np.full(n_len, np.nan)

    if n_len < n:
        return k_arr, d_arr, j_arr

    # 初始 K=50
    k_arr[n - 1] = 50.0
    d_arr[n - 1] = 50.0

    for i in range(n, n_len):
        hh = np.max(high[i - n + 1 : i + 1])
        ll = np.min(low[i - n + 1 : i + 1])
        rsv = (close[i] - ll) / (hh - ll) * 100 if hh != ll else 50.0
        k_arr[i] = (m1 - 1) / m1 * k_arr[i - 1] + 1 / m1 * rsv
        d_arr[i] = (m2 - 1) / m2 * d_arr[i - 1] + 1 / m2 * k_arr[i]
        j_arr[i] = 3 * k_arr[i] - 2 * d_arr[i]

    return k_arr, d_arr, j_arr


def _bollinger(close: np.ndarray, period: int = 20, std_mult: float = 2.0):
    mid = _sma(close, period)
    upper = np.full(len(close), np.nan)
    lower = np.full(len(close), np.nan)
    width = np.full(len(close), np.nan)
    position = np.full(len(close), np.nan)
    for i in range(period - 1, len(close)):
        std = np.std(close[i - period + 1 : i + 1])
        upper[i] = mid[i] + std_mult * std
        lower[i] = mid[i] - std_mult * std
        width[i] = (upper[i] - lower[i]) / mid[i] if mid[i] > 0 else np.nan
        band_range = upper[i] - lower[i]
        position[i] = (close[i] - lower[i]) / band_range if band_range > 0 else 0.5
    return mid, upper, lower, width, position


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    tr = np.full(n, np.nan)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr_arr = _sma(tr, period)
    # 用 EMA 替代 SMA 做 ATR（更标准）
    atr_arr = np.full(n, np.nan)
    if n > period:
        atr_arr[period] = np.mean(tr[1 : period + 1])
        for i in range(period + 1, n):
            atr_arr[i] = (atr_arr[i - 1] * (period - 1) + tr[i]) / period
    return atr_arr


def _vwap(typical_price: np.ndarray, volume: np.ndarray) -> np.ndarray:
    cumulative_pv = np.cumsum(typical_price * volume)
    cumulative_vol = np.cumsum(volume)
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = cumulative_pv / cumulative_vol
        vwap[cumulative_vol == 0] = np.nan
    return vwap
