# manipulation/wyckoff.py
"""威科夫模式识别"""

import pandas as pd
from typing import Dict


def simplified_wyckoff(df: pd.DataFrame, current_price: float) -> Dict:
    """精简版威科夫模式识别"""
    if df.empty or len(df) < 50:
        return {"schematic": "none", "current_phase": "unknown", "events": [], "detail": "数据不足"}

    recent_50 = df.tail(50)
    recent_20 = df.tail(20)

    high_50 = recent_50['high'].max()
    low_50 = recent_50['low'].min()
    high_20 = recent_20['high'].max()
    low_20 = recent_20['low'].min()

    range_50 = high_50 - low_50
    range_pct = range_50 / current_price if current_price > 0 else 0

    events = []
    detail_parts = []

    if range_pct < 0.03:
        return {"schematic": "none", "current_phase": "unknown", "events": [],
                "detail": f"区间过窄({range_pct*100:.1f}%), 无明显结构"}

    # Spring
    low_5 = recent_20.tail(5)['low'].min()
    close_now = recent_20['close'].iloc[-1]
    if low_5 < low_20 * 0.99 and close_now > low_20:
        events.append("Spring(弹簧)--看涨反转信号")
        detail_parts.append(f"价格下穿{low_20:.4f}后收回，形成Spring")

    # Upthrust
    high_5 = recent_20.tail(5)['high'].max()
    if high_5 > high_20 * 1.01 and close_now < high_20:
        events.append("Upthrust(上冲回落)--看跌反转信号")
        detail_parts.append(f"价格上破{high_20:.4f}后回落，形成Upthrust")

    # SOS / SOW
    avg_vol_10 = recent_20['vol'].tail(10).mean()
    avg_vol_50 = recent_50['vol'].mean()
    if avg_vol_10 > avg_vol_50 * 1.5:
        if close_now > recent_20['close'].mean():
            events.append("SOS(强势信号)--放量上攻")
            detail_parts.append("放量突破，强势信号(SOS)")
        else:
            events.append("SOW(弱势信号)--放量下跌")
            detail_parts.append("放量下跌，弱势信号(SOW)")

    bullish_ev = [e for e in events if "看涨" in e or "SOS" in e]
    bearish_ev = [e for e in events if "看跌" in e or "SOW" in e]

    position_in_range = (current_price - low_50) / range_50 if range_50 > 0 else 0.5

    if bullish_ev and not bearish_ev:
        schematic = "accumulation"
        current_phase = "Phase_C" if any("Spring" in e for e in bullish_ev) else "Phase_B"
    elif bearish_ev and not bullish_ev:
        schematic = "distribution"
        current_phase = "Phase_C" if any("Upthrust" in e for e in bearish_ev) else "Phase_B"
    elif position_in_range < 0.3:
        schematic = "accumulation"; current_phase = "Phase_B"
    elif position_in_range > 0.7:
        schematic = "distribution"; current_phase = "Phase_B"
    else:
        schematic = "none"; current_phase = "unknown"

    return {
        "schematic": schematic,
        "current_phase": current_phase,
        "events": events,
        "detail": "; ".join(detail_parts) if detail_parts else "无明显威科夫结构事件",
    }
