# manipulation/oi_flow.py
"""维度③: OI 持仓流向分析（四象限）"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List

logger = logging.getLogger(__name__)


def analyze_oi_flow(oi_data: List, df: pd.DataFrame, current_price: float) -> Dict:
    """
    OI 四象限分析：价+OI 变化 → 资金方向

    价涨+OI增 → 多头主动建仓 (bullish +2)
    价涨+OI减 → 空头平仓推动 (weak bullish +0.5)
    价跌+OI增 → 空头主动建仓 (bearish -2)
    价跌+OI减 → 多头平仓下跌 (bearish -1, 但接近底部)
    """
    if not oi_data or len(oi_data) < 3:
        return {"score": 0, "signal": "OI数据不足", "detail": ""}

    try:
        oi_vals = []
        n = min(len(oi_data), 24)
        for i in range(n):
            if len(oi_data[i]) >= 2:
                oi_vals.append(float(oi_data[i][1]))

        if len(oi_vals) < 3:
            return {"score": 0, "signal": "OI数据不足", "detail": ""}

        oi_vals.reverse()
        oi_start = np.mean(oi_vals[:3])
        oi_end = np.mean(oi_vals[-3:])
        oi_change_pct = (oi_end - oi_start) / oi_start if oi_start > 0 else 0

        # 价格变化
        close_arr = df['close'].values.astype(float)
        price_start = close_arr[-len(oi_vals)] if len(close_arr) >= len(oi_vals) else close_arr[0]
        price_end = close_arr[-1]
        price_change_pct = (price_end - price_start) / price_start if price_start > 0 else 0

        score = 0.0
        signals = []
        details = []

        # 四象限
        if price_change_pct > 0.01 and oi_change_pct > 0.01:
            score = 1.5
            signals.append("📈 价涨OI增 — 多头主动建仓拉升")
            details.append(f"价格涨{price_change_pct*100:.1f}%，OI增{oi_change_pct*100:.1f}%")
            details.append("资金在主动买入，建仓拉升中，趋势偏多")
        elif price_change_pct > 0.01 and oi_change_pct < -0.01:
            score = 0.5
            signals.append("📈 价涨OI减 — 空头平仓推动上涨")
            details.append(f"价格涨{price_change_pct*100:.1f}%，OI减{abs(oi_change_pct)*100:.1f}%")
            details.append("上涨由空头平仓推动，非主动买入，力度有限")
        elif price_change_pct < -0.01 and oi_change_pct > 0.01:
            score = -1.5
            signals.append("📉 价跌OI增 — 空头主动建仓砸盘")
            details.append(f"价格跌{abs(price_change_pct)*100:.1f}%，OI增{oi_change_pct*100:.1f}%")
            details.append("资金在主动做空，建仓砸盘中，趋势偏空")
        elif price_change_pct < -0.01 and oi_change_pct < -0.01:
            score = -0.5
            signals.append("📉 价跌OI减 — 多头平仓引发下跌")
            details.append(f"价格跌{abs(price_change_pct)*100:.1f}%，OI减{abs(oi_change_pct)*100:.1f}%")
            details.append("多头平仓带动下跌，杀跌接近尾声时可能反弹")
        elif abs(price_change_pct) < 0.01 and abs(oi_change_pct) < 0.01:
            signals.append("➖ 价平OI平 — 方向不明，等待突破")
            details.append("价格和OI均无明显变化，多空僵持")
        else:
            signals.append(f"价变{price_change_pct*100:.1f}%, OI变{oi_change_pct*100:.1f}%")
            details.append("价量关系不显著")

        # OI 趋势斜率
        if len(oi_vals) >= 8:
            x = np.arange(len(oi_vals))
            slope = np.polyfit(x, oi_vals, 1)[0]
            if slope / oi_start > 0.002:
                details.append(f"OI趋势持续增长(日化+{slope/oi_start*len(oi_vals)*100:.1f}%)")
            elif slope / oi_start < -0.002:
                details.append(f"OI趋势持续下降(日化{slope/oi_start*len(oi_vals)*100:.1f}%)")

        return {
            "score": round(score, 2),
            "signal": " | ".join(signals),
            "detail": "；".join(details),
            "oi_change_pct": round(oi_change_pct, 4),
            "price_change_pct": round(price_change_pct, 4),
        }

    except (ValueError, IndexError, TypeError) as e:
        logger.debug(f"OI分析异常: {e}")
        return {"score": 0, "signal": "OI分析失败", "detail": str(e)}
