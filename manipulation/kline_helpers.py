# manipulation/kline_helpers.py
"""K线辅助检测：位置判断 / 成交量异常 / 插针 / 放量滞涨"""

import time
import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional

from .types import BehaviorTag, DetectionResult

logger = logging.getLogger(__name__)


def detect_position(df: pd.DataFrame, current_price: float) -> Optional[BehaviorTag]:
    """位置判断"""
    if len(df) < 20: return None
    recent = df.tail(30)
    range_high = recent['high'].max()
    range_low = recent['low'].min()
    price_range = range_high - range_low
    if price_range <= 0 or current_price <= 0: return None
    percentile = (current_price - range_low) / price_range
    if percentile >= 0.80: return BehaviorTag.POSITION_HIGH
    elif percentile <= 0.20: return BehaviorTag.POSITION_LOW
    else: return BehaviorTag.POSITION_MID


def detect_volume_anomaly(df: pd.DataFrame, symbol: str) -> List[DetectionResult]:
    """成交量异常检测"""
    results = []
    if len(df) < 22: return results
    vol_arr = df['vol'].values.astype(float)
    cur_vol = vol_arr[-1]
    avg_vol = np.mean(vol_arr[-21:-1])
    if avg_vol <= 0: return results
    vol_ratio = cur_vol / avg_vol
    if vol_ratio < 0.5:
        results.append(DetectionResult(tag=BehaviorTag.VOL_DILIANG, symbol=symbol, timestamp=time.time(), confidence=0.85,
            detail=f"地量: {cur_vol:.1f} < 均量{avg_vol:.1f}*0.5"))
    if vol_ratio >= 2.0:
        tag = BehaviorTag.VOL_TIANLIANG if vol_ratio >= 4.0 else BehaviorTag.VOL_BEILIANG
        open_p = df['open'].values.astype(float)[-1]
        amp = (df['high'].values.astype(float)[-1] - df['low'].values.astype(float)[-1]) / open_p if open_p > 0 else 0
        results.append(DetectionResult(tag=tag, symbol=symbol, timestamp=time.time(), confidence=0.8,
            detail=f"{tag.value}: 量比{vol_ratio:.1f}x"))
        if amp < 0.003:
            results.append(DetectionResult(tag=BehaviorTag.VOL_DUIDAO, symbol=symbol, timestamp=time.time(), confidence=0.7,
                detail=f"疑似对倒: 放量{vol_ratio:.1f}x但振幅仅{amp:.3%}"))
    return results


def detect_wick(df: pd.DataFrame, symbol: str, wick_shadow_ratio: float = 3.0,
                scan_all: bool = True) -> List[DetectionResult]:
    """
    插针检测（向下洗盘插针 + 向上诱多插针）

    判断逻辑:
      - 向下插针: 下影线 > 实体 × wick_shadow_ratio，且最低价跌破前5根K线支撑
        （跌破后收回 → 洗盘震仓，庄家故意砸出低位吸筹）
      - 向上插针: 上影线 > 实体 × wick_shadow_ratio，且最高价突破前5根K线阻力
        （突破后回落 → 诱多出货，庄家拉高骗人接盘）

    参数:
      wick_shadow_ratio: 影线/实体阈值倍数，默认3.0
      scan_all: True=扫描全部历史, False=仅最近5根
    """
    results = []
    if len(df) < 10:
        return results

    open_arr  = df['open'].values.astype(float)
    high_arr  = df['high'].values.astype(float)
    low_arr   = df['low'].values.astype(float)
    close_arr = df['close'].values.astype(float)

    if hasattr(df, 'timestamp') or 'timestamp' in df.columns:
        ts_col = df['timestamp'].values if hasattr(df['timestamp'], 'values') else df['timestamp']
    elif df.index.name == 'timestamp' or 'timestamp' in str(df.index.name):
        ts_col = df.index
    else:
        ts_col = None

    n = len(df)
    start = 10 if scan_all else max(10, n - 5)  # 前10根用于计算支撑/阻力

    for i in range(start, n):
        body = abs(close_arr[i] - open_arr[i])
        if body <= 0:
            continue

        total_range = high_arr[i] - low_arr[i]
        if total_range <= 0:
            continue

        date_str = ""
        if ts_col is not None:
            try:
                ts = ts_col[i] if hasattr(ts_col[i], 'strftime') else ts_col.iloc[i]
                date_str = str(ts)[:10] + " "
            except Exception:
                pass

        # ═══ 向下插针（洗盘震仓）═══
        lower_shadow = min(open_arr[i], close_arr[i]) - low_arr[i]
        lower_ratio = lower_shadow / body if body > 0 else 0

        if lower_ratio >= wick_shadow_ratio:
            # 该K线最低价是否跌破前5根支撑
            prev_lows = low_arr[max(0, i - 5):i]
            support = float(min(prev_lows)) if len(prev_lows) > 0 else low_arr[i]
            break_support = (support - low_arr[i]) / support if support > 0 else 0
            # 收回确认：收盘价回到支撑上方
            recovered = close_arr[i] > support

            if break_support > 0.01 and recovered:
                confidence = min(0.95, 0.6 + lower_ratio * 0.08)
                results.append(DetectionResult(
                    tag=BehaviorTag.WICK_SHAKE_OUT, symbol=symbol,
                    timestamp=time.time(), confidence=round(confidence, 2),
                    detail=f"{date_str}向下洗盘针 | 开{open_arr[i]:.4f}→收{close_arr[i]:.4f} | 最低{low_arr[i]:.4f} "
                           f"跌破支撑{support:.4f}({break_support*100:.1f}%)"
                           f"→收回 | 影体比{lower_ratio:.1f}x | "
                           f"振幅{(total_range/close_arr[i]*100):.1f}%"))

        # ═══ 向上插针（诱多出货）═══
        upper_shadow = high_arr[i] - max(open_arr[i], close_arr[i])
        upper_ratio = upper_shadow / body if body > 0 else 0

        if upper_ratio >= wick_shadow_ratio:
            prev_highs = high_arr[max(0, i - 5):i]
            resistance = float(max(prev_highs)) if len(prev_highs) > 0 else high_arr[i]
            break_resistance = (high_arr[i] - resistance) / resistance if resistance > 0 else 0
            # 回落确认：收盘价回到阻力下方
            fallen_back = close_arr[i] < resistance

            if break_resistance > 0.01 and fallen_back:
                confidence = min(0.95, 0.6 + upper_ratio * 0.08)
                results.append(DetectionResult(
                    tag=BehaviorTag.WICK_UP_DISTRIBUTION, symbol=symbol,
                    timestamp=time.time(), confidence=round(confidence, 2),
                    detail=f"{date_str}向上诱多针 | 开{open_arr[i]:.4f}→收{close_arr[i]:.4f} | 最高{high_arr[i]:.4f} "
                           f"突破阻力{resistance:.4f}({break_resistance*100:.1f}%)"
                           f"→回落 | 影体比{upper_ratio:.1f}x | "
                           f"振幅{(total_range/close_arr[i]*100):.1f}%"))

    return results


def detect_stagnation(df: pd.DataFrame, position: Optional[BehaviorTag], symbol: str) -> List[DetectionResult]:
    """放量滞涨"""
    results = []
    if position != BehaviorTag.POSITION_HIGH or len(df) < 22: return results
    open_arr = df['open'].values.astype(float); high_arr = df['high'].values.astype(float)
    low_arr = df['low'].values.astype(float); close_arr = df['close'].values.astype(float)
    vol_arr = df['vol'].values.astype(float)
    avg_vol = np.mean(vol_arr[-21:-1])
    for i in range(max(0, len(df) - 3), len(df)):
        if close_arr[i] <= open_arr[i]: continue
        vol_ratio = vol_arr[i] / avg_vol if avg_vol > 0 else 0
        if vol_ratio < 2.0: continue
        total_range = high_arr[i] - low_arr[i]
        if total_range <= 0: continue
        body_ratio = (close_arr[i] - open_arr[i]) / total_range
        shadow_ratio = (high_arr[i] - close_arr[i]) / total_range
        if body_ratio < 0.3 and shadow_ratio > 0.5:
            results.append(DetectionResult(tag=BehaviorTag.STAGNATION_DISTRIBUTION, symbol=symbol, timestamp=time.time(),
                confidence=0.8, detail=f"高位派发: 放量{vol_ratio:.1f}x, 实体{body_ratio:.1%}, 上影{shadow_ratio:.1%}"))
    return results
