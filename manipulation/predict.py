# manipulation/predict.py
"""下一步预测 + 综合点位计算"""

from typing import Dict, List, Optional

import pandas as pd


def predict_next_move_enhanced(
    direction: str,
    phase_cn: str,
    current_price: float,
    atr: float,
    synthesis: Dict,
    wyckoff_events: List = None,
) -> Dict:
    """
    增强版下一步预测 — 优先使用博弈推断的方向
    """
    result = {
        "next_action": "观望", "direction": direction,
        "target_price": current_price, "stop_price": current_price,
        "time_frame": "短期(1-3根K线)",
        "reasoning": "", "risk_scenario": "",
    }

    if atr <= 0:
        atr = current_price * 0.02

    bull_w = synthesis.get("bull_weight", 0)
    bear_w = synthesis.get("bear_weight", 0)
    reasoning = synthesis.get("reasoning_chain", [])

    if direction == "long":
        if bull_w >= 0.8:
            result["next_action"] = "庄家大概率拉升，可做多跟进"
            result["target_price"] = round(current_price + 4 * atr, 6)
            result["stop_price"] = round(current_price - 1.5 * atr, 6)
            result["time_frame"] = "短期(1-5根K线)"
        elif bull_w >= 0.4:
            result["next_action"] = "庄家有吸筹迹象，等待洗盘后做多"
            result["target_price"] = round(current_price + 3 * atr, 6)
            result["stop_price"] = round(current_price - 2 * atr, 6)
            result["time_frame"] = "中期(5-10根K线)"
        else:
            result["next_action"] = "多头信号偏弱，轻仓试多或观望"
            result["target_price"] = round(current_price + 2 * atr, 6)
            result["stop_price"] = round(current_price - 1 * atr, 6)
        result["reasoning"] = "【数据博弈分析】\n" + "\n".join(f"  {r}" for r in reasoning)
        result["risk_scenario"] = f"若价格跌破{result['stop_price']:.4f}或OI骤降，多单止损离场"

    elif direction == "short":
        if bear_w >= 0.8:
            result["next_action"] = "庄家大概率砸盘，可做空跟进"
            result["target_price"] = round(current_price - 4 * atr, 6)
            result["stop_price"] = round(current_price + 1.5 * atr, 6)
            result["time_frame"] = "短期(1-5根K线)"
        elif bear_w >= 0.4:
            result["next_action"] = "庄家有派发迹象，可分批做空"
            result["target_price"] = round(current_price - 3 * atr, 6)
            result["stop_price"] = round(current_price + 2 * atr, 6)
            result["time_frame"] = "中期(5-10根K线)"
        else:
            result["next_action"] = "空头信号偏弱，轻仓试空或观望"
            result["target_price"] = round(current_price - 2 * atr, 6)
            result["stop_price"] = round(current_price + 1 * atr, 6)
        result["reasoning"] = "【数据博弈分析】\n" + "\n".join(f"  {r}" for r in reasoning)
        result["risk_scenario"] = f"若价格突破{result['stop_price']:.4f}或OI骤增，空单止损离场"

    else:
        result["next_action"] = "方向不明，观望等待"
        result["direction"] = "neutral"
        result["reasoning"] = "【数据博弈分析】\n" + "\n".join(f"  {r}" for r in reasoning)
        result["reasoning"] += "\n\n当前多空博弈暂时均衡，无明确方向优势"
        result["risk_scenario"] = "等待关键数据维度出现明确信号后再参与"

    # 威科夫事件修正
    wyckoff_str = str(wyckoff_events or [])
    if "Spring(弹簧)" in wyckoff_str and result["direction"] == "short":
        result["direction"] = "long"
        result["reasoning"] += "\n\n⚠ 威科夫修正: 检测到Spring(弹簧)看涨反转，原空头信号被覆盖"
    if "Upthrust(上冲回落)" in wyckoff_str and result["direction"] == "long":
        result["direction"] = "short"
        result["reasoning"] += "\n\n⚠ 威科夫修正: 检测到Upthrust(上冲回落)看跌，原多头信号被覆盖"

    return result


def calculate_manipulation_target(
    df: pd.DataFrame,
    current_price: float,
    atr: float,
    phase_result: Dict,
    next_move: Dict,
    ai_signal: Dict = None,
) -> Dict:
    """综合计算预测目标点位"""
    targets = []
    weights = []

    manip_target = next_move.get("target_price", current_price)
    manip_dir = next_move.get("direction", "neutral")
    if manip_dir != "neutral":
        targets.append(manip_target)
        weights.append(0.50)

    if ai_signal and ai_signal.get("direction") != "neutral":
        ai_target = ai_signal.get("take_profit1") or ai_signal.get("entry")
        if ai_target:
            targets.append(ai_target)
            weights.append(0.30)

    if not targets:
        if manip_dir == "long":
            targets.append(current_price + 2 * atr)
        elif manip_dir == "short":
            targets.append(current_price - 2 * atr)
        else:
            targets.append(current_price)
        weights.append(0.20)

    if targets and any(w > 0 for w in weights):
        total_w = sum(weights)
        weighted_target = sum(t * w for t, w in zip(targets, weights)) / total_w
    else:
        weighted_target = current_price
        total_w = 0

    if weighted_target > current_price * 1.005:
        ensemble_direction = "long"
    elif weighted_target < current_price * 0.995:
        ensemble_direction = "short"
    else:
        ensemble_direction = "neutral"

    return {
        "ensemble_target": round(weighted_target, 6),
        "ensemble_direction": ensemble_direction,
        "target_range_low": round(min(targets) if targets else current_price, 6),
        "target_range_high": round(max(targets) if targets else current_price, 6),
        "distance_pct": round((weighted_target - current_price) / current_price * 100, 2),
        "confidence": round(min(total_w, 1.0), 2),
        "sources": {
            "manipulation_target": manip_target,
            "ai_target": ai_signal.get("take_profit1") if ai_signal else None,
        },
        "detail": (
            f"综合预测: {weighted_target:.4f} (偏向: {ensemble_direction}), "
            f"距当前价偏移{abs(weighted_target-current_price)/current_price*100:.2f}%, "
            f"置信度: {min(total_w,1.0):.0%}"
        ),
    }
