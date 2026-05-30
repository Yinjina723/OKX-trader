# manipulation/synthesis.py
"""7维加权合成 + 阶段与方向综合判定"""

from typing import Dict, List, Optional

from detectors import BehaviorTag, DetectionResult


def synthesize_direction(
    crowd: Dict,
    elite: Dict,
    oi_flow: Dict,
    taker: Dict,
    funding: Dict,
    mark_deviation: float = 0,
    index_price: float = 0,
    current_price: float = 0,
    elite_trend: Dict = None,
    basis_tf: Dict = None,
    atr: float = 0.0,
    config = None,
) -> Dict:
    """
    7维度加权投票 → 庄家下一步方向 + 推理链

    权重分配（可通过 config.MANIPULATION_WEIGHTS 覆盖）:
      精英vs散户背离: 30% (最可靠)
      精英多周期趋向: 15%
      散户拥挤度:     20%
      OI持仓流向:     15%
      Taker买卖:      10%
      资金费率:        5%
      基差多周期:      5%
    """
    # 🆕 可配置权重（从 config 读取，有默认值兜底）
    if config is not None:
        w = getattr(config, 'MANIPULATION_WEIGHTS', {})
    else:
        w = {}
    weights = {
        "elite_divergence": w.get("elite_divergence", 0.30),
        "elite_multi_tf": w.get("elite_multi_tf", 0.15),
        "crowd": w.get("crowd", 0.20),
        "oi_flow": w.get("oi_flow", 0.15),
        "taker": w.get("taker", 0.10),
        "funding": w.get("funding", 0.05),
        "basis": w.get("basis", 0.05),
    }

    bull_weight = 0.0
    bear_weight = 0.0
    reasoning_chain = []

    dimensions = [
        ("精英vs散户背离", elite.get("score", 0), weights["elite_divergence"]),
        ("精英多周期趋向", elite_trend.get("score", 0) if elite_trend else 0, weights["elite_multi_tf"]),
        ("散户拥挤度(反向)", crowd.get("score", 0), weights["crowd"]),
        ("OI持仓流向", oi_flow.get("score", 0), weights["oi_flow"]),
        ("Taker主动买卖", taker.get("score", 0), weights["taker"]),
        ("资金费率", funding.get("score", 0), weights["funding"]),
        ("基差多周期", basis_tf.get("score", 0) if basis_tf else 0, weights["basis"]),
    ]

    for name, score, weight in dimensions:
        if score > 0:
            bull_weight += abs(score) * weight
            reasoning_chain.append(f"[{name}] 偏多 (得分{score:+.1f}, 权重{weight:.0%})")
        elif score < 0:
            bear_weight += abs(score) * weight
            reasoning_chain.append(f"[{name}] 偏空 (得分{score:+.1f}, 权重{weight:.0%})")
        else:
            reasoning_chain.append(f"[{name}] 中性")

    # 标记价偏离微调
    if abs(mark_deviation) > 0.005:
        if mark_deviation > 0.01:
            bear_weight += 0.15
            reasoning_chain.append(f"[标记价] 大幅折价({mark_deviation*100:.1f}%)，偏向空")
        elif mark_deviation < -0.01:
            bull_weight += 0.15
            reasoning_chain.append(f"[标记价] 大幅溢价({abs(mark_deviation)*100:.1f}%)，偏向多")

    # 期货-现货基差微调
    if index_price > 0 and current_price > 0:
        basis = (current_price - index_price) / index_price
        if basis > 0.03:
            bear_weight += 0.1
            reasoning_chain.append(f"[基差] 期货溢价{basis*100:.1f}%，偏向空头回归")
        elif basis < -0.03:
            bull_weight += 0.1
            reasoning_chain.append(f"[基差] 期货折价{abs(basis)*100:.1f}%，偏向多头回归")

    # 🆕 判定方向 — 波动率自适应阈值
    net = bull_weight - bear_weight
    total = bull_weight + bear_weight

    # 根据 ATR/价格 比例动态调整阈值
    vol_ratio = atr / current_price if (atr > 0 and current_price > 0) else 0.005
    adaptive = getattr(config, 'VOLATILITY_ADAPTIVE_THRESHOLD', True)
    if adaptive:
        # 高波动 → 提高阈值，低波动 → 降低阈值
        base_threshold = 0.25 + max(0, (vol_ratio - 0.005) * 50)
        base_threshold = min(base_threshold, 0.45)  # 上限 0.45
        base_threshold = max(base_threshold, 0.18)  # 下限 0.18
    else:
        base_threshold = 0.30

    if net >= base_threshold:
        direction = "long"
    elif net <= -base_threshold:
        direction = "short"
    elif net > base_threshold * 0.4:
        direction = "long"
        reasoning_chain.append(f"⚠ 信号偏弱(净分{net:.3f}<阈值{base_threshold:.2f})，建议轻仓")
    elif net < -base_threshold * 0.4:
        direction = "short"
        reasoning_chain.append(f"⚠ 信号偏弱(净分{abs(net):.3f}<阈值{base_threshold:.2f})，建议轻仓")
    else:
        direction = "neutral"

    if adaptive:
        reasoning_chain.insert(0, f"[自适应阈值] ATR/Price={vol_ratio*100:.2f}% → 判定阈值={base_threshold:.2f}")

    confidence = min(total / 1.5, 0.95) if total > 0 else 0.05

    return {
        "direction": direction,
        "confidence": round(confidence, 2),
        "bull_weight": round(bull_weight, 3),
        "bear_weight": round(bear_weight, 3),
        "net_score": round(net, 3),
        "reasoning_chain": reasoning_chain,
        "threshold": round(base_threshold, 2),
        "volatility_ratio": round(vol_ratio, 4),
        "dimension_scores": {
            "精英背离": elite.get("score", 0),
            "精英多周期": elite_trend.get("score", 0) if elite_trend else 0,
            "散户拥挤度": crowd.get("score", 0),
            "OI流向": oi_flow.get("score", 0),
            "Taker": taker.get("score", 0),
            "资金费率": funding.get("score", 0),
            "基差多周期": basis_tf.get("score", 0) if basis_tf else 0,
        }
    }


def determine_phase_and_direction(
    synthesis: Dict,
    position: Optional[BehaviorTag],
    all_detections: List[DetectionResult],
) -> tuple:
    """
    综合博弈推断结果 + K线检测 → 确定阶段 + 方向
    
    返回: (phase_old, phase_cn, direction, confidence, summary_signals)
    """
    direction = synthesis["direction"]
    confidence = synthesis["confidence"]
    bull_w = synthesis["bull_weight"]
    bear_w = synthesis["bear_weight"]

    tags = [d.tag for d in all_detections]

    # 收集中文信号描述
    summary_signals = []
    for d in all_detections:
        if d.tag not in (BehaviorTag.POSITION_LOW, BehaviorTag.POSITION_MID, BehaviorTag.POSITION_HIGH):
            summary_signals.append(f"[{d.tag.value}] {d.detail[:50]}")

    # 博弈信号优先
    if direction == "long":
        if bull_w >= 0.8:
            phase_old, phase_cn = "markup", "拉升期"
        elif bull_w >= 0.4:
            phase_old, phase_cn = "accumulation", "吸筹期"
        else:
            phase_old, phase_cn = "accumulation", "吸筹期(弱信号)"

        if BehaviorTag.WICK_SHAKE_OUT in tags:
            phase_old, phase_cn = "shakeout", "洗盘震仓(即将拉升)"

    elif direction == "short":
        if bear_w >= 0.8:
            phase_old, phase_cn = "markdown", "砸盘期"
        elif bear_w >= 0.4:
            phase_old, phase_cn = "distribution", "派发期"
        else:
            phase_old, phase_cn = "distribution", "派发期(弱信号)"

        if BehaviorTag.STAGNATION_DISTRIBUTION in tags:
            phase_old, phase_cn = "distribution", "派发期(高位滞涨)"

        if BehaviorTag.WICK_UP_DISTRIBUTION in tags:
            phase_old, phase_cn = "distribution", "派发期(诱多出货)"

    else:
        # direction == neutral → 用 K 线标签判断
        if position == BehaviorTag.POSITION_LOW:
            phase_old, phase_cn = "accumulation", "吸筹期(盘整待突破)"
            if bull_w > bear_w:
                direction = "long"
        elif position == BehaviorTag.POSITION_HIGH:
            phase_old, phase_cn = "distribution", "派发期(盘整待选择)"
            if bear_w > bull_w:
                direction = "short"
        elif BehaviorTag.VOL_DILIANG in tags:
            phase_old, phase_cn = "unknown", "盘整(地量待变)"
        else:
            phase_old, phase_cn = "unknown", "方向不明"

            # 即使 neutral，也给出倾向
            if bull_w > bear_w + 0.1:
                direction = "long"
                phase_old, phase_cn = "accumulation", "吸筹迹象(弱)"
                summary_signals.append("[博弈] 多空维度中多头略占优")
            elif bear_w > bull_w + 0.1:
                direction = "short"
                phase_old, phase_cn = "distribution", "派发迹象(弱)"
                summary_signals.append("[博弈] 多空维度中空头略占优")

    return phase_old, phase_cn, direction, confidence, summary_signals
