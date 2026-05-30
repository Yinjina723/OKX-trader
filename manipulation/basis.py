# manipulation/basis.py
"""维度⑦: 多周期基差分析"""

import logging
import numpy as np
from typing import Dict, List

logger = logging.getLogger(__name__)


def analyze_multi_tf_basis(
    current_price: float,
    index_candles_5m: List,
    index_candles_1H: List,
) -> Dict:
    """
    合约基差多周期分析 — 期货 vs 指数价格的偏离
    基差持续扩大 → 投机过热 → 回归风险
    """
    if not index_candles_5m and not index_candles_1H:
        return {"score": 0, "signal": "基差数据不足", "detail": ""}

    score = 0.0
    signals = []
    details = []

    for tf_name, candles in [("5m", index_candles_5m), ("1H", index_candles_1H)]:
        if not candles or len(candles) < 4:
            continue

        try:
            # 指数K线格式: [ts, o, h, l, c, confirm]
            closes = [float(c[4]) for c in candles if len(c) >= 5]
            if len(closes) < 4:
                continue

            avg_idx = np.mean(closes)
            if avg_idx <= 0 or current_price <= 0:
                continue

            basis = (current_price - avg_idx) / avg_idx

            if basis > 0.03:
                signals.append(f"⚠ {tf_name}基差极高(+{basis*100:.1f}%)")
                details.append(f"{tf_name}周期期货溢价{basis*100:.1f}%—逼空情绪极端")
                if score == 0:
                    score = -0.4
                else:
                    score = max(score - 0.2, -1.0)
            elif basis > 0.015:
                details.append(f"{tf_name}周期期货溢价{basis*100:.1f}%—偏多头")
            elif basis < -0.03:
                signals.append(f"⚠ {tf_name}基差极低({basis*100:.1f}%)")
                details.append(f"{tf_name}周期期货折价{abs(basis)*100:.1f}%—恐慌情绪极端")
                if score == 0:
                    score = 0.4
                else:
                    score = min(score + 0.2, 1.0)
            elif basis < -0.015:
                details.append(f"{tf_name}周期期货折价{abs(basis)*100:.1f}%—偏空头")

        except (ValueError, IndexError, TypeError) as e:
            logger.debug(f"基差{tf_name}分析异常: {e}")

    return {
        "score": round(score, 2),
        "signal": " | ".join(signals) if signals else "基差正常范围",
        "detail": "；".join(details),
    }
