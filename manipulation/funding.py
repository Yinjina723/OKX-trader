# manipulation/funding.py
"""维度⑤: 资金费率辅助"""

import numpy as np
from typing import Dict, List


def analyze_funding(funding_rate: float, funding_rate_history: List = None) -> Dict:
    """资金费率极端值 → 拥挤方向预警"""
    score = 0.0
    signals = []
    details = []

    abs_rate = abs(funding_rate)
    if abs_rate < 0.0005:
        return {"score": 0, "signal": "资金费率中性", "detail": f"当前{funding_rate:.4%}"}

    if funding_rate > 0.003:
        score = -0.6
        signals.append(f"资金费率极高({funding_rate:.4%})—多头拥挤需支付高额费率")
        details.append("持有多单成本极高，多头可能被迫平仓，利空")
    elif funding_rate > 0.0015:
        score = -0.3
        signals.append(f"资金费率偏高({funding_rate:.4%})—多头偏拥挤")
    elif funding_rate < -0.003:
        score = 0.6
        signals.append(f"资金费率极负({funding_rate:.4%})—空头拥挤需支付高额费率")
        details.append("持有空单成本极高，空头可能被迫平仓，利多")
    elif funding_rate < -0.0015:
        score = 0.3
        signals.append(f"资金费率偏低({funding_rate:.4%})—空头偏拥挤")

    # 历史趋势
    if funding_rate_history and len(funding_rate_history) >= 12:
        try:
            rates = []
            for item in funding_rate_history[:12]:
                if isinstance(item, dict):
                    rates.append(float(item.get("fundingRate", 0)))
                elif isinstance(item, list) and len(item) >= 2:
                    rates.append(float(item[1]))
            if rates:
                rates.reverse()
                prev_avg = np.mean(rates[:6])
                curr_avg = np.mean(rates[-6:])
                if curr_avg > prev_avg * 2 and curr_avg > 0:
                    details.append("费率近期持续走高，多头拥挤加剧")
                elif curr_avg < prev_avg * 2 and curr_avg < 0:
                    details.append("费率近期持续走低，空头拥挤加剧")
        except Exception:
            pass

    return {
        "score": round(score, 2),
        "signal": " | ".join(signals),
        "detail": "；".join(details),
    }
