# manipulation/crowd.py
"""维度①: 散户拥挤度（反向指标）"""

from typing import Dict


def analyze_crowd_crowding(ls_ratio: float, funding_rate: float) -> Dict:
    """
    散户多空比分析 — 散户拥挤的方向 = 庄家收割的方向

    返回: {"score": -1~+1, "signal": str, "detail": str}
      +1 表示散户拥挤做空(逼空机会) → 应做多
      -1 表示散户拥挤做多(砸盘风险) → 应做空
       0 表示中性
    """
    score = 0.0
    signals = []
    details = []

    if ls_ratio <= 0:
        return {"score": 0, "signal": "多空比数据缺失", "detail": ""}

    # 极端拥挤做多 → 庄家砸盘（做空信号）
    if ls_ratio >= 3.5:
        score = -1.0
        signals.append(f"🔥 散户极度拥挤做多 (ls={ls_ratio:.1f}:1)")
        details.append(f"多空比={ls_ratio:.1f}:1")
        details.append("散户几乎全部做多，庄家大概率砸盘清洗多头")
    elif ls_ratio >= 2.5:
        score = -0.7
        signals.append(f"⚠ 散户拥挤做多 (ls={ls_ratio:.1f}:1)")
        details.append(f"多空比={ls_ratio:.1f}:1")
        details.append("散户偏多拥挤，庄家有动机打压价格逼多头离场")
    elif ls_ratio >= 1.8:
        score = -0.3
        signals.append(f"散户偏多 (ls={ls_ratio:.1f}:1)")
        details.append(f"多空比={ls_ratio:.1f}:1，散户偏多但未极端")

    # 极端拥挤做空 → 庄家逼空（做多信号）
    elif ls_ratio <= 0.3:
        score = 1.0
        signals.append(f"🔥 散户极度拥挤做空 (ls={ls_ratio:.2f}:1)")
        details.append(f"多空比={ls_ratio:.2f}:1")
        details.append("散户几乎全部做空，庄家大概率逼空拉升")
    elif ls_ratio <= 0.5:
        score = 0.7
        signals.append(f"⚠ 散户拥挤做空 (ls={ls_ratio:.2f}:1)")
        details.append(f"多空比={ls_ratio:.2f}:1")
        details.append("散户偏空拥挤，庄家有动机拉升逼空头止损")
    elif ls_ratio <= 0.7:
        score = 0.3
        signals.append(f"散户偏空 (ls={ls_ratio:.2f}:1)")
        details.append(f"多空比={ls_ratio:.2f}:1，散户偏空但未极端")
    else:
        signals.append(f"散户多空均衡 (ls={ls_ratio:.2f}:1)")
        details.append(f"多空比={ls_ratio:.2f}:1，散户方向不拥挤")

    # 资金费率辅助验证
    if abs(funding_rate) > 0.001:
        if funding_rate > 0.002 and score < 0:
            details.append(f"资金费率={funding_rate:.4%}(极正)，进一步确认多头拥挤")
            score = max(score, -0.8)  # 强化空头信号
        elif funding_rate < -0.002 and score > 0:
            details.append(f"资金费率={funding_rate:.4%}(极负)，进一步确认空头拥挤")
            score = min(score, 0.8)  # 强化多头信号

    return {
        "score": score,
        "signal": " | ".join(signals),
        "detail": "；".join(details),
        "ls_ratio": ls_ratio,
        "funding_rate": funding_rate,
    }
