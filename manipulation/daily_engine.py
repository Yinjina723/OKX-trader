# manipulation/daily_engine.py
"""日线操盘检测引擎 —— 纯 K 线分析（不依赖实时数据源）"""

import logging
import time
from typing import Dict, List

import numpy as np
import pandas as pd

from .types import BehaviorTag, DetectionResult, ManipulationPhase
from .kline_helpers import detect_position, detect_volume_anomaly, detect_wick, detect_stagnation
from .wyckoff import simplified_wyckoff

logger = logging.getLogger(__name__)


def run_daily_manipulation(
    df: pd.DataFrame,
    symbol: str = "",
    wick_shadow_ratio: float = 3.0,
) -> Dict:
    """
    日线级别庄家行为检测 —— 仅基于 K 线数据。

    返回:
    {
        "phase_result":    {phase, phase_cn, score, confidence, signals, ...},
        "wyckoff":         {...},
        "next_move":       {next_action, direction, target_price, stop_price, ...},
        "predicted_point": {...},
    }
    """
    if df is None or df.empty or "close" not in df.columns:
        return _empty_result("数据无效")

    current_price = float(df["close"].iloc[-1])

    # ── K 线辅助检测 ──
    position = detect_position(df, current_price)
    all_detections: List[DetectionResult] = []
    all_detections.extend(detect_volume_anomaly(df, symbol))
    wick_detections = detect_wick(df, symbol, wick_shadow_ratio, scan_all=True)
    all_detections.extend(wick_detections)
    all_detections.extend(detect_stagnation(df, position, symbol))
    if position:
        all_detections.insert(0, DetectionResult(
            tag=position, symbol=symbol, timestamp=time.time(),
            confidence=0.9, detail=f"价格区间位置: {position.value}",
        ))

    # ── 趋势强度分析 ──
    trend_strength = _analyze_trend_strength(df)

    # ── 阶段判断 ──
    phase, phase_cn, direction, confidence, signals = _determine_phase(
        position, all_detections, trend_strength, df,
    )

    # ── 威科夫 ──
    wyckoff = simplified_wyckoff(df, current_price)

    # ── ATR ──
    atr = float(df["ATR"].iloc[-1]) if "ATR" in df.columns and not np.isnan(df["ATR"].iloc[-1]) else current_price * 0.03

    # ── 下一步预测 ──
    next_move = _predict_next(direction, phase_cn, current_price, atr, trend_strength, wyckoff)

    # ── 综合点位 ──
    predicted_point = _calculate_target(df, current_price, atr, next_move)

    # ── 构建结果 ──
    phase_result = {
        "phase": phase,
        "phase_cn": phase_cn,
        "score": int(trend_strength.get("net_score", 0) * 10),
        "confidence": round(confidence, 2),
        "signals": signals,
        "dimension_scores": {
            "K线趋势": round(trend_strength.get("trend_score", 0), 2),
            "成交量": round(trend_strength.get("volume_score", 0), 2),
            "K线形态": round(trend_strength.get("pattern_score", 0), 2),
        },
        "weighted_bull": trend_strength.get("bull_weight", 0),
        "weighted_bear": trend_strength.get("bear_weight", 0),
        "bull_score": int(trend_strength.get("bull_weight", 0) * 10),
        "bear_score": int(trend_strength.get("bear_weight", 0) * 10),
    }

    # ── 日志 ──
    logger.info("=" * 60)
    logger.info(f"  📊 日线操盘检测 | {symbol}")
    logger.info(f"  📍 阶段: {phase_cn}  方向: {direction}")
    logger.info(f"  🎯 预测: {next_move.get('next_action','')}")
    logger.info("=" * 60)

    return {
        "phase_result": phase_result,
        "wyckoff": wyckoff,
        "next_move": next_move,
        "predicted_point": predicted_point,
        "wicks": [w.detail for w in wick_detections],  # 独立插针列表
    }


# ══════════════ 内部函数 ══════════════

def _analyze_trend_strength(df: pd.DataFrame) -> Dict:
    """
    基于日线 K 线的趋势强度分析：
    - 均线排列、MACD、RSI、价格位置综合评分
    """
    if len(df) < 20:
        return {"trend_score": 0, "volume_score": 0, "pattern_score": 0,
                "bull_weight": 0, "bear_weight": 0, "net_score": 0}

    latest = df.iloc[-1]
    close = float(latest["close"])
    vol = float(latest["vol"])
    prev = df.iloc[-2]

    scores = {"trend": 0, "volume": 0, "pattern": 0}
    reasons: List[str] = []

    # ── 趋势评分 (MA + MACD) ──
    ma5  = latest.get("MA5")
    ma10 = latest.get("MA10")
    ma30 = latest.get("MA30")
    ma60 = latest.get("MA60")

    if not np.isnan(ma5) and not np.isnan(ma10):
        if ma5 > ma10:
            scores["trend"] += 0.3
            reasons.append("MA5>MA10 短多")
        else:
            scores["trend"] -= 0.3
            reasons.append("MA5<MA10 短空")

    if not np.isnan(ma5) and not np.isnan(ma30):
        if ma5 > ma30:
            scores["trend"] += 0.2
        else:
            scores["trend"] -= 0.2

    if not np.isnan(ma10) and not np.isnan(ma60):
        if ma10 > ma60:
            scores["trend"] += 0.3
            reasons.append("MA10>MA60 长多")
        else:
            scores["trend"] -= 0.3
            reasons.append("MA10<MA60 长空")

    macd_h = latest.get("MACD_Hist")
    prev_macd_h = prev.get("MACD_Hist")
    if not np.isnan(macd_h) and not np.isnan(prev_macd_h):
        if macd_h > 0:
            scores["trend"] += 0.2
            reasons.append("MACD柱>0")
        else:
            scores["trend"] -= 0.2
        if macd_h > prev_macd_h:
            scores["trend"] += 0.15
            reasons.append("MACD柱扩大")
        else:
            scores["trend"] -= 0.1

    scores["trend"] = max(-1, min(1, scores["trend"]))

    # ── 成交量评分 ──
    avg_vol_20 = float(df["vol"].iloc[-21:-1].mean()) if len(df) >= 21 else vol
    if avg_vol_20 > 0:
        vol_ratio = vol / avg_vol_20
        change_pct = (close - float(prev["close"])) / float(prev["close"])
        if vol_ratio > 1.5 and change_pct > 0:
            scores["volume"] = 0.5
            reasons.append("放量上涨")
        elif vol_ratio > 1.5 and change_pct < 0:
            scores["volume"] = -0.5
            reasons.append("放量下跌")
        elif vol_ratio < 0.5:
            scores["volume"] = 0.0 if abs(change_pct) < 0.02 else (0.1 if change_pct > 0 else -0.1)
            reasons.append("缩量")

    # ── K线形态评分 (从 RSI / 位置) ──
    rsi = latest.get("RSI14")
    if not np.isnan(rsi):
        if rsi < 30:
            scores["pattern"] += 0.4
            reasons.append(f"RSI超卖({rsi:.0f})")
        elif rsi > 70:
            scores["pattern"] -= 0.4
            reasons.append(f"RSI超买({rsi:.0f})")
        elif rsi > 50:
            scores["pattern"] += 0.1
        else:
            scores["pattern"] -= 0.1

    bb_pos = latest.get("BB_position")
    if not np.isnan(bb_pos):
        if bb_pos < 0.1:
            scores["pattern"] += 0.3
            reasons.append("BB下轨")
        elif bb_pos > 0.9:
            scores["pattern"] -= 0.3
            reasons.append("BB上轨")

    scores["pattern"] = max(-1, min(1, scores["pattern"]))

    # ── 综合 ──
    bull_w = (scores["trend"] * 0.4 + scores["volume"] * 0.25 + scores["pattern"] * 0.35)
    bull_w = (bull_w + 1) / 2
    bull_w = round(max(0.0, min(1.0, bull_w)), 4)
    bear_w = round(1.0 - bull_w, 4)
    net = round(bull_w - bear_w, 2)

    return {
        "trend_score": scores["trend"],
        "volume_score": scores["volume"],
        "pattern_score": scores["pattern"],
        "bull_weight": bull_w,
        "bear_weight": bear_w,
        "net_score": net,
        "reasons": reasons,
    }


def _determine_phase(
    position: BehaviorTag | None,
    detections: List[DetectionResult],
    trend: Dict,
    df: pd.DataFrame,
):
    """基于 K 线特征判断庄家阶段。"""
    signals: List[str] = []
    net = trend.get("net_score", 0)
    reasons = trend.get("reasons", [])

    # 收集检测信号
    for d in detections:
        signals.append(d.detail)

    # 阶段映射
    if position == BehaviorTag.POSITION_LOW:
        if any("地量" in s for s in signals) and net > -0.1:
            phase_cn = "低位吸筹"
            direction = "long"
            confidence = 0.65
        elif any("洗盘" in s for s in signals) or any("插针" in s for s in signals):
            phase_cn = "洗盘震仓"
            direction = "long"
            confidence = 0.70
        else:
            phase_cn = "低位盘整"
            direction = "neutral"
            confidence = 0.50
    elif position == BehaviorTag.POSITION_HIGH:
        if any("派发" in s for s in signals) or any("天量" in s for s in signals):
            phase_cn = "高位派发"
            direction = "short"
            confidence = 0.70
        elif net > 0.3:
            phase_cn = "高位拉升"
            direction = "long"
            confidence = 0.55
        else:
            phase_cn = "高位盘整"
            direction = "neutral"
            confidence = 0.50
    else:  # mid
        if net > 0.4:
            phase_cn = "中位偏强"
            direction = "long"
            confidence = 0.60
        elif net < -0.3:
            phase_cn = "中位偏弱"
            direction = "short"
            confidence = 0.60
        else:
            phase_cn = "区间震荡"
            direction = "neutral"
            confidence = 0.45

    # 信号补充
    for r in reasons:
        signals.insert(0, f"[趋势] {r}")

    return position.value if position else "unknown", phase_cn, direction, confidence, signals


def _predict_next(
    direction: str,
    phase_cn: str,
    current_price: float,
    atr: float,
    trend: Dict,
    wyckoff: Dict,
) -> Dict:
    """预测下一步动作。"""
    result = {
        "next_action": "观望",
        "direction": direction,
        "target_price": current_price,
        "stop_price": current_price,
        "time_frame": "短期(1-5根日线)",
        "reasoning": "",
        "risk_scenario": "",
    }

    wyckoff_events = wyckoff.get("events", [])
    wyckoff_str = str(wyckoff_events)

    if direction == "long":
        if trend.get("bull_weight", 0) >= 0.75:
            result["next_action"] = "多头强势，可做多跟进"
            result["target_price"] = round(current_price + 3 * atr, 6)
            result["stop_price"] = round(current_price - 1.5 * atr, 6)
        elif trend.get("bull_weight", 0) >= 0.55:
            result["next_action"] = "偏多，轻仓试多或等回调"
            result["target_price"] = round(current_price + 2 * atr, 6)
            result["stop_price"] = round(current_price - 2 * atr, 6)
        else:
            result["next_action"] = "弱多信号，建议观望"
            result["direction"] = "neutral"

        if "Spring(弹簧)" in wyckoff_str or "SOS" in wyckoff_str:
            result["next_action"] += " ⚡Spring/SOS确认"
            result["confidence"] = 0.8

        result["risk_scenario"] = f"跌破{result['stop_price']:.4f}或成交量萎缩，多单止损"

    elif direction == "short":
        if trend.get("bear_weight", 0) >= 0.75:
            result["next_action"] = "空头强势，可做空跟进"
            result["target_price"] = round(current_price - 3 * atr, 6)
            result["stop_price"] = round(current_price + 1.5 * atr, 6)
        elif trend.get("bear_weight", 0) >= 0.55:
            result["next_action"] = "偏空，轻仓试空或等反弹"
            result["target_price"] = round(current_price - 2 * atr, 6)
            result["stop_price"] = round(current_price + 2 * atr, 6)
        else:
            result["next_action"] = "弱空信号，建议观望"
            result["direction"] = "neutral"

        if "Upthrust(上冲回落)" in wyckoff_str or "SOW" in wyckoff_str:
            result["next_action"] += " ⚡Upthrust/SOW确认"

        result["risk_scenario"] = f"突破{result['stop_price']:.4f}或放量反弹，空单止损"

    else:
        result["next_action"] = "方向不明，观望"
        result["direction"] = "neutral"
        result["risk_scenario"] = "等待明确方向信号后再参与"

    result["reasoning"] = "; ".join(trend.get("reasons", []))
    return result


def _calculate_target(
    df: pd.DataFrame,
    current_price: float,
    atr: float,
    next_move: Dict,
) -> Dict:
    """计算综合目标点位。"""
    manip_target = next_move.get("target_price", current_price)
    manip_dir = next_move.get("direction", "neutral")

    if manip_dir == "long":
        ensemble_direction = "long" if manip_target > current_price * 1.005 else "neutral"
    elif manip_dir == "short":
        ensemble_direction = "short" if manip_target < current_price * 0.995 else "neutral"
    else:
        ensemble_direction = "neutral"

    return {
        "ensemble_target": round(manip_target, 6),
        "ensemble_direction": ensemble_direction,
        "distance_pct": round((manip_target - current_price) / current_price * 100, 2),
        "confidence": 0.5,
        "detail": f"基于K线预测: {manip_target:.4f}",
    }


def _empty_result(reason: str) -> Dict:
    return {
        "phase_result": {
            "phase": "unknown", "phase_cn": reason, "score": 0, "confidence": 0,
            "signals": [], "dimension_scores": {},
            "weighted_bull": 0, "weighted_bear": 0,
            "bull_score": 0, "bear_score": 0,
        },
        "wyckoff": {"schematic": "none", "current_phase": "unknown", "events": [], "detail": reason},
        "next_move": {
            "next_action": "无数据", "direction": "neutral",
            "target_price": 0, "stop_price": 0,
            "time_frame": "", "reasoning": reason, "risk_scenario": "",
        },
        "predicted_point": {
            "ensemble_target": 0, "ensemble_direction": "neutral",
            "distance_pct": 0, "confidence": 0, "detail": reason,
        },
    }
