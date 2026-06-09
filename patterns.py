# patterns.py
"""日线 K 线形态识别 + RSI/MACD 背离检测"""

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ══════════════ K 线形态 ══════════════

def detect_candlestick_patterns(df: pd.DataFrame, lookback: int = 5) -> List[Dict]:
    """
    在最近 lookback 根 K 线中识别经典形态。
    """
    if len(df) < 5:
        return []

    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l_arr = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    v = df["vol"].values.astype(float)
    results: List[Dict] = []

    n = len(df)
    start = max(0, n - lookback)

    for i in range(start, n):
        body = abs(c[i] - o[i])
        total_range = h[i] - l_arr[i]
        if total_range <= 0 or body == 0:
            continue

        upper_shadow = h[i] - max(o[i], c[i])
        lower_shadow = min(o[i], c[i]) - l_arr[i]
        is_bull = c[i] > o[i]
        is_bear = o[i] > c[i]

        # ── 1. 吞没形态 ──
        if i >= 1:
            prev_body = abs(c[i - 1] - o[i - 1])
            if prev_body > 0:
                # 看涨吞没
                if (is_bull and o[i - 1] > c[i - 1] and
                        c[i] > o[i - 1] and o[i] < c[i - 1] and
                        body > prev_body * 1.1):
                    results.append({
                        "name": "看涨吞没", "direction": "bullish",
                        "candle_index": i, "confidence": min(0.9, 0.6 + body / prev_body * 0.1),
                    })
                # 看跌吞没
                if (is_bear and c[i - 1] > o[i - 1] and
                        o[i] > c[i - 1] and c[i] < o[i - 1] and
                        body > prev_body * 1.1):
                    results.append({
                        "name": "看跌吞没", "direction": "bearish",
                        "candle_index": i, "confidence": min(0.9, 0.6 + body / prev_body * 0.1),
                    })

        # ── 2. 锤子线 / 上吊线 ──
        body_ratio = body / total_range
        middle = (h[i] + l_arr[i]) / 2
        if body_ratio < 0.35 and lower_shadow >= body * 2 and upper_shadow < body * 0.6:
            # 位置判断
            pos_percentile = _position_percentile(df, i)
            if pos_percentile < 0.35:
                results.append({
                    "name": "锤子线", "direction": "bullish",
                    "candle_index": i, "confidence": 0.75,
                })
            elif pos_percentile > 0.65:
                results.append({
                    "name": "上吊线", "direction": "bearish",
                    "candle_index": i, "confidence": 0.70,
                })
            else:
                results.append({
                    "name": "锤子线(中位)", "direction": "neutral",
                    "candle_index": i, "confidence": 0.55,
                })

        # ── 3. 倒锤子 / 流星线 ──
        if body_ratio < 0.35 and upper_shadow >= body * 2 and lower_shadow < body * 0.6:
            pos_percentile = _position_percentile(df, i)
            if pos_percentile > 0.65:
                results.append({
                    "name": "流星线", "direction": "bearish",
                    "candle_index": i, "confidence": 0.75,
                })
            elif pos_percentile < 0.35:
                results.append({
                    "name": "倒锤子", "direction": "bullish",
                    "candle_index": i, "confidence": 0.60,
                })

        # ── 4. 十字星 ──
        if body_ratio < 0.1:
            shadow_ratio = upper_shadow / lower_shadow if lower_shadow > 0 else 999
            if shadow_ratio > 2:
                results.append({
                    "name": "墓碑十字", "direction": "bearish",
                    "candle_index": i, "confidence": 0.65,
                })
            elif shadow_ratio < 0.5:
                results.append({
                    "name": "蜻蜓十字", "direction": "bullish",
                    "candle_index": i, "confidence": 0.65,
                })
            else:
                results.append({
                    "name": "十字星", "direction": "neutral",
                    "candle_index": i, "confidence": 0.55,
                })

        # ── 5. 大阳/大阴线 ──
        avg_body = np.mean([abs(c[j] - o[j]) for j in range(max(0, i - 10), i) if abs(c[j] - o[j]) > 0]) if i > 0 else body
        if avg_body > 0:
            if body > avg_body * 2.5 and is_bull:
                results.append({
                    "name": "大阳线", "direction": "bullish",
                    "candle_index": i, "confidence": 0.70,
                })
            elif body > avg_body * 2.5 and is_bear:
                results.append({
                    "name": "大阴线", "direction": "bearish",
                    "candle_index": i, "confidence": 0.70,
                })

    # ── 6. 三白兵 / 三黑鸦 ──
    for i in range(start + 2, n):
        b0 = c[i - 2] > o[i - 2]
        b1 = c[i - 1] > o[i - 1]
        b2 = c[i] > o[i]
        if b0 and b1 and b2:
            if (c[i - 1] > c[i - 2] and c[i] > c[i - 1] and
                    o[i - 1] > o[i - 2] and o[i] > o[i - 1]):
                results.append({
                    "name": "三白兵", "direction": "bullish",
                    "candle_index": i, "confidence": 0.80,
                })
        if (not b0) and (not b1) and (not b2):
            if (c[i - 1] < c[i - 2] and c[i] < c[i - 1] and
                    o[i - 1] < o[i - 2] and o[i] < o[i - 1]):
                results.append({
                    "name": "三黑鸦", "direction": "bearish",
                    "candle_index": i, "confidence": 0.80,
                })

    # ── 7. 晨星 / 黄昏星 ──
    for i in range(start + 2, n):
        b0_body = abs(c[i - 2] - o[i - 2])
        b1_body = abs(c[i - 1] - o[i - 1])
        b2_body = abs(c[i] - o[i])
        avg_b = np.mean([abs(c[j] - o[j]) for j in range(max(0, i - 7), i) if abs(c[j] - o[j]) > 0]) or b0_body
        # 晨星: 大阴 + 小实体 + 大阳
        if (o[i - 2] > c[i - 2] and b0_body > avg_b * 1.5 and
                b1_body < avg_b * 0.3 and
                c[i] > o[i] and b2_body > avg_b * 1.2 and
                c[i] > (o[i - 2] + c[i - 2]) / 2):
            results.append({
                "name": "晨星", "direction": "bullish",
                "candle_index": i, "confidence": 0.85,
            })
        # 黄昏星: 大阳 + 小实体 + 大阴
        if (c[i - 2] > o[i - 2] and b0_body > avg_b * 1.5 and
                b1_body < avg_b * 0.3 and
                o[i] > c[i] and b2_body > avg_b * 1.2 and
                c[i] < (o[i - 2] + c[i - 2]) / 2):
            results.append({
                "name": "黄昏星", "direction": "bearish",
                "candle_index": i, "confidence": 0.85,
            })

    return results


def _position_percentile(df: pd.DataFrame, idx: int) -> float:
    """计算当前 K 线在近 30 根 K 线区间中的位置 (0~1)。"""
    low_n = max(0, idx - 30)
    window_h = df["high"].iloc[low_n: idx + 1].max()
    window_l = df["low"].iloc[low_n: idx + 1].min()
    if window_h == window_l:
        return 0.5
    return (df["close"].iloc[idx] - window_l) / (window_h - window_l)


# ══════════════ 背离检测 ══════════════

def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 20) -> Dict:
    """
    RSI 背离检测：
    - bullish: 价格新低 RSI 未创新低 → 底背离
    - bearish: 价格新高 RSI 未创新高 → 顶背离
    """
    if len(df) < lookback + 5 or "RSI14" not in df.columns:
        return {"type": "none", "detail": ""}

    recent = df.iloc[-lookback:]
    price = recent["close"].values.astype(float)
    rsi = recent["RSI14"].values.astype(float)

    # 找近期的局部极值
    result = {"type": "none", "detail": ""}

    # 底背离：价格创新低但 RSI 走高
    for window in [5, 10, 15]:
        if len(price) < window + 3:
            continue
        p_low_idx = np.argmin(price[-window:]) + (len(price) - window)
        p_prev_low_idx = np.argmin(price[-2*window:-window]) + (len(price) - 2*window)
        if p_low_idx < len(price) and p_prev_low_idx > 0:
            if (price[p_low_idx] < price[p_prev_low_idx] * 0.995 and
                    rsi[p_low_idx] > rsi[p_prev_low_idx]):
                result["type"] = "bullish"
                result["detail"] = (f"RSI底背离: 价格新低{price[p_low_idx]:.4f} "
                                    f"RSI{p_low_idx - (len(price)-lookback):.0f}={rsi[p_low_idx]:.1f} > "
                                    f"前低RSI={rsi[p_prev_low_idx]:.1f}")
                return result

    # 顶背离：价格创新高但 RSI 走低
    for window in [5, 10, 15]:
        if len(price) < window + 3:
            continue
        p_high_idx = np.argmax(price[-window:]) + (len(price) - window)
        p_prev_high_idx = np.argmax(price[-2*window:-window]) + (len(price) - 2*window)
        if p_high_idx < len(price) and p_prev_high_idx > 0:
            if (price[p_high_idx] > price[p_prev_high_idx] * 1.005 and
                    rsi[p_high_idx] < rsi[p_prev_high_idx]):
                result["type"] = "bearish"
                result["detail"] = (f"RSI顶背离: 价格新高{price[p_high_idx]:.4f} "
                                    f"RSI{rsi[p_high_idx]:.1f} < "
                                    f"前高RSI={rsi[p_prev_high_idx]:.1f}")
                return result

    return result


def detect_macd_divergence(df: pd.DataFrame, lookback: int = 30) -> Dict:
    """
    MACD 背离检测：
    - bullish: 价格新低 MACD_Hist 未创新低
    - bearish: 价格新高 MACD_Hist 未创新高
    """
    if len(df) < lookback + 5 or "MACD_Hist" not in df.columns:
        return {"type": "none", "detail": ""}

    recent = df.iloc[-lookback:]
    price = recent["close"].values.astype(float)
    macd_h = recent["MACD_Hist"].values.astype(float)

    result = {"type": "none", "detail": ""}

    # 底背离
    for window in [8, 15, 25]:
        if len(price) < window + 3:
            continue
        p_low_idx = np.argmin(price[-window:]) + (len(price) - window)
        p_prev_low_idx = np.argmin(price[-2*window:-window]) + (len(price) - 2*window)
        if p_low_idx < len(price) and p_prev_low_idx > 0:
            if (price[p_low_idx] < price[p_prev_low_idx] * 0.995 and
                    macd_h[p_low_idx] > macd_h[p_prev_low_idx]):
                result["type"] = "bullish"
                result["detail"] = (f"MACD底背离: 价格新低{price[p_low_idx]:.4f} "
                                    f"MACD柱={macd_h[p_low_idx]:.6f} > "
                                    f"前低MACD柱={macd_h[p_prev_low_idx]:.6f}")
                return result

    # 顶背离
    for window in [8, 15, 25]:
        if len(price) < window + 3:
            continue
        p_high_idx = np.argmax(price[-window:]) + (len(price) - window)
        p_prev_high_idx = np.argmax(price[-2*window:-window]) + (len(price) - 2*window)
        if p_high_idx < len(price) and p_prev_high_idx > 0:
            if (price[p_high_idx] > price[p_prev_high_idx] * 1.005 and
                    macd_h[p_high_idx] < macd_h[p_prev_high_idx]):
                result["type"] = "bearish"
                result["detail"] = (f"MACD顶背离: 价格新高{price[p_high_idx]:.4f} "
                                    f"MACD柱={macd_h[p_high_idx]:.6f} < "
                                    f"前高MACD柱={macd_h[p_prev_high_idx]:.6f}")
                return result

    return result


def detect_ma_alignment(df: pd.DataFrame) -> Dict:
    """检测均线排列（多头/空头/交叉）。"""
    if "MA5" not in df.columns or "MA10" not in df.columns:
        return {"alignment": "unknown", "detail": ""}

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None

    ma5, ma10, ma30, ma60 = (
        latest.get("MA5", np.nan), latest.get("MA10", np.nan),
        latest.get("MA30", np.nan), latest.get("MA60", np.nan),
    )

    # 多头排列
    if (not np.isnan(ma5) and not np.isnan(ma10) and
            not np.isnan(ma30) and ma5 > ma10 > ma30):
        return {"alignment": "bullish", "detail": f"多头排列 MA5({ma5:.4f})>MA10({ma10:.4f})>MA30({ma30:.4f})"}

    # 空头排列
    if (not np.isnan(ma5) and not np.isnan(ma10) and
            not np.isnan(ma30) and ma5 < ma10 < ma30):
        return {"alignment": "bearish", "detail": f"空头排列 MA5({ma5:.4f})<MA10({ma10:.4f})<MA30({ma30:.4f})"}

    # 金叉/死叉
    if prev is not None:
        prev_ma5, prev_ma10 = prev.get("MA5"), prev.get("MA10")
        if (not np.isnan(prev_ma5) and not np.isnan(prev_ma10) and
                not np.isnan(ma5) and not np.isnan(ma10)):
            if prev_ma5 <= prev_ma10 and ma5 > ma10:
                return {"alignment": "golden_cross", "detail": f"MA5({ma5:.4f})上穿MA10({ma10:.4f}) 金叉"}
            if prev_ma5 >= prev_ma10 and ma5 < ma10:
                return {"alignment": "dead_cross", "detail": f"MA5({ma5:.4f})下穿MA10({ma10:.4f}) 死叉"}

    return {"alignment": "mixed", "detail": "均线交织无明确信号"}
