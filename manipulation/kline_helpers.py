# manipulation/kline_helpers.py
"""K线辅助检测：位置判断 / 成交量异常 / 插针 / 放量滞涨"""

import time
import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional

from detectors import BehaviorTag, DetectionResult

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


def detect_wick(df: pd.DataFrame, symbol: str, wick_shadow_ratio: float = 3.0) -> List[DetectionResult]:
    """插针检测（向下洗盘插针 + 向上诱多插针）"""
    results = []
    if len(df) < 10: return results
    open_arr = df['open'].values.astype(float)
    high_arr = df['high'].values.astype(float)
    low_arr = df['low'].values.astype(float)
    close_arr = df['close'].values.astype(float)
    for i in range(max(0, len(df) - 5), len(df)):
        body = abs(close_arr[i] - open_arr[i])
        # ── 向下插针（洗盘震仓）──
        lower_shadow = min(open_arr[i], close_arr[i]) - low_arr[i]
        if body > 0 and lower_shadow > body * wick_shadow_ratio:
            prev_lows = low_arr[max(0, i - 5):i]
            support = min(prev_lows) if len(prev_lows) > 0 else low_arr[i]
            if (support - low_arr[i]) / support > 0.01 and close_arr[i] > support:
                shadow_ratio = lower_shadow / body
                results.append(DetectionResult(tag=BehaviorTag.WICK_SHAKE_OUT, symbol=symbol, timestamp=time.time(),
                    confidence=0.75, detail=f"洗盘插针: 最低{low_arr[i]:.4f}跌破{support:.4f}后收回 (影体比{shadow_ratio:.1f}x)"))
        # ── 向上插针（诱多出货）──
        upper_shadow = high_arr[i] - max(open_arr[i], close_arr[i])
        if body > 0 and upper_shadow > body * wick_shadow_ratio:
            prev_highs = high_arr[max(0, i - 5):i]
            resistance = max(prev_highs) if len(prev_highs) > 0 else high_arr[i]
            if (high_arr[i] - resistance) / resistance > 0.01 and close_arr[i] < resistance:
                shadow_ratio = upper_shadow / body
                results.append(DetectionResult(tag=BehaviorTag.WICK_UP_DISTRIBUTION, symbol=symbol, timestamp=time.time(),
                    confidence=0.75, detail=f"诱多插针: 最高{high_arr[i]:.4f}突破{resistance:.4f}后回落 (影体比{shadow_ratio:.1f}x)"))
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
