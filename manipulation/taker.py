# manipulation/taker.py
"""维度④: Taker 主动买卖量趋势"""

import logging
import pandas as pd
from typing import Dict, List

logger = logging.getLogger(__name__)


def analyze_taker_flow(taker_vol: List, df: pd.DataFrame) -> Dict:
    """
    Taker 主动买卖量分析

    - 近 N 个周期中，主动买入占比趋势
    - 价格下跌但主动买入占优 → 聪明钱在接货
    - 价格上涨但主动卖出占优 → 聪明钱在出货
    """
    if not taker_vol or len(taker_vol) < 3:
        return {"score": 0, "signal": "Taker数据不足", "detail": ""}

    try:
        n = min(len(taker_vol), 24)
        net_list = []
        buy_total = 0
        sell_total = 0

        for i in range(n):
            if len(taker_vol[i]) >= 3:
                sell = float(taker_vol[i][1])
                buy = float(taker_vol[i][2])
                net_list.append(buy - sell)
                buy_total += buy
                sell_total += sell

        net_list.reverse()

        if buy_total + sell_total == 0:
            return {"score": 0, "signal": "Taker数据为空", "detail": ""}

        buy_ratio = buy_total / (buy_total + sell_total)

        # 近6周期趋势
        recent_net = net_list[-6:] if len(net_list) >= 6 else net_list
        pos_ratio = sum(1 for v in recent_net if v > 0) / len(recent_net) if recent_net else 0

        # 价格变化
        close_arr = df['close'].values.astype(float)
        price_change = 0
        if len(close_arr) >= 6:
            price_change = (close_arr[-1] - close_arr[-6]) / close_arr[-6]

        score = 0.0
        signals = []
        details = []

        # 核心逻辑：价跌但 Taker 净买入 → 聪明钱在吸筹
        if price_change < -0.005 and buy_ratio > 0.55:
            score = 1.2
            signals.append(f"💪 价跌{abs(price_change)*100:.1f}%但Taker净买入 — 聪明钱逆势吸筹")
            details.append(f"Taker主动买入占比{buy_ratio:.1%}，{len(recent_net)}周期中{int(pos_ratio*len(recent_net))}周期净买入")
            details.append("价格在跌但有人在大量吃货，庄家在底部收集筹码")

        elif price_change < -0.005 and buy_ratio > 0.50:
            score = 0.6
            signals.append(f"价跌中Taker买卖均衡 — 抛压不重")
            details.append(f"Taker主动买入占比{buy_ratio:.1%}")

        elif price_change < -0.005 and buy_ratio < 0.40:
            score = -0.8
            signals.append(f"价跌Taker净卖出 — 真实抛压")
            details.append(f"Taker主动卖出占比{1-buy_ratio:.1%}")

        # 价涨但 Taker 净卖出 → 拉高出货
        elif price_change > 0.005 and buy_ratio < 0.45:
            score = -1.0
            signals.append(f"⚠ 价涨{price_change*100:.1f}%但Taker净卖出 — 拉高出货嫌疑")
            details.append(f"Taker主动卖出占比{1-buy_ratio:.1%}，庄家借拉升出货")

        elif price_change > 0.005 and buy_ratio > 0.55:
            score = 0.8
            signals.append(f"价涨Taker净买入 — 真实拉升")
            details.append(f"Taker主动买入占比{buy_ratio:.1%}，真金白银在推升")

        else:
            details.append(f"Taker买入占比{buy_ratio:.1%}，{len(recent_net)}周期中{int(pos_ratio*len(recent_net))}净买入")
            if pos_ratio > 0.7:
                signals.append("Taker持续净买入趋势")
                score = 0.4
            elif pos_ratio < 0.3:
                signals.append("Taker持续净卖出趋势")
                score = -0.4

        net_taker = net_list[-1] if net_list else 0

        return {
            "score": round(score, 2),
            "signal": " | ".join(signals) if signals else "Taker方向不显著",
            "detail": "；".join(details),
            "buy_ratio": round(buy_ratio, 3),
            "pos_ratio": round(pos_ratio, 3),
            "net_taker": round(net_taker, 2),
        }

    except (ValueError, IndexError, TypeError) as e:
        logger.debug(f"Taker分析异常: {e}")
        return {"score": 0, "signal": "Taker分析失败", "detail": ""}
