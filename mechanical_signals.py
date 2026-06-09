"""1H 级别机械信号生成器 —— AI定方向 + 技术指标定入场

架构: AI日线判断方向(long/short/neutral) → 1H K线技术指标自动产生入场信号 → 固定TP/SL执行
"""

import numpy as np
import pandas as pd
from typing import List, Dict


def generate_signals_1h(
    df_1h: pd.DataFrame,
    ai_direction: str,
    tp_pct: float = 0.008,
    sl_pct: float = 0.005,
    max_signals_per_day: int = 10,
) -> List[Dict]:
    """
    在 1H DataFrame 上产生固定 TP/SL 的交易信号。

    Args:
        df_1h: 1H K线 DataFrame（含计算好的指标列）
        ai_direction: AI日线方向 "long" / "short" / "neutral"
        tp_pct: 止盈百分比
        sl_pct: 止损百分比
        max_signals_per_day: 每天最多产生的信号数

    Returns:
        信号列表 [{entry_price, stop_loss, take_profit, direction, confidence, trigger, timestamp}, ...]
    """
    if ai_direction == "neutral":
        return []

    signals = []
    n = len(df_1h)

    # ── 每天信号计数器 ──
    daily_count: Dict[str, int] = {}

    for i in range(30, n):  # 从第30根开始（指标需要预热）
        row = df_1h.iloc[i]
        prev_row = df_1h.iloc[i - 1]

        close = float(row["close"])
        vol = float(row.get("vol", 0))
        timestamp = row.get("timestamp")

        # 提取日期
        if hasattr(timestamp, "strftime"):
            day_str = timestamp.strftime("%Y-%m-%d")
        else:
            day_str = str(timestamp)[:10]

        # ── 每天信号上限 ──
        daily_count.setdefault(day_str, 0)
        if daily_count[day_str] >= max_signals_per_day:
            continue

        # ── 指标提取 ──
        rsi = float(row.get("RSI14", 50))
        rsi_prev = float(prev_row.get("RSI14", 50))
        macd_h = float(row.get("MACD_Hist", 0))
        macd_h_prev = float(prev_row.get("MACD_Hist", 0))
        bb_pos = float(row.get("BB_position", 0.5))
        bb_pos_prev = float(prev_row.get("BB_position", 0.5))

        # ── 量能过滤：放量时才入场 ──
        vol_series = df_1h["vol"].iloc[max(0, i - 19):i + 1].astype(float)
        vol_ma_20 = float(np.mean(vol_series))
        vol_ok = vol > vol_ma_20 * 0.7

        # ═══════════════════════════════════
        #  做多信号（AI 偏多 + 回调买入）
        # ═══════════════════════════════════
        if ai_direction == "long":
            # 条件组合（满足任意一组即可）：
            group_a = (
                rsi < 40 and rsi > rsi_prev and          # RSI 超卖区反弹
                macd_h > macd_h_prev and                  # MACD柱开始回升
                bb_pos < 0.3                              # 在布林下轨附近
            )
            group_b = (
                40 <= rsi < 50 and rsi > rsi_prev and    # RSI 弱势区反弹
                macd_h > 0 and macd_h > macd_h_prev and   # MACD柱正值且扩张
                bb_pos_prev < 0.2 and bb_pos > bb_pos_prev  # 刚离开下轨
            )
            group_c = (
                rsi < 35 and                               # 深度超卖
                bb_pos < 0.1 and                           # 极度接近下轨
                close > float(prev_row["close"])            # 收阳线
            )

            if vol_ok and (group_a or group_b or group_c):
                entry = close
                signals.append({
                    "entry_price": round(entry, 6),
                    "stop_loss": round(entry * (1 - sl_pct), 6),
                    "take_profit": round(entry * (1 + tp_pct), 6),
                    "direction": "long",
                    "confidence": "high" if group_c else "medium",
                    "trigger": "rsi_bounce" if group_a else ("bb_recovery" if group_b else "oversold_bounce"),
                    "timestamp": str(timestamp)[:19] if timestamp else None,
                })
                daily_count[day_str] += 1

        # ═══════════════════════════════════
        #  做空信号（AI 偏空 + 反弹做空）
        # ═══════════════════════════════════
        elif ai_direction == "short":
            group_a = (
                rsi > 60 and rsi < rsi_prev and           # RSI 超买区回落
                macd_h < macd_h_prev and                   # MACD柱开始缩量
                bb_pos > 0.7                               # 在布林上轨附近
            )
            group_b = (
                50 <= rsi < 60 and rsi < rsi_prev and     # RSI 强势区回落
                macd_h < 0 and macd_h < macd_h_prev and    # MACD柱负值且扩张
                bb_pos_prev > 0.8 and bb_pos < bb_pos_prev  # 刚离开上轨
            )
            group_c = (
                rsi > 65 and                               # 深度超买
                bb_pos > 0.9 and                           # 极度接近上轨
                close < float(prev_row["close"])            # 收阴线
            )

            if vol_ok and (group_a or group_b or group_c):
                entry = close
                signals.append({
                    "entry_price": round(entry, 6),
                    "stop_loss": round(entry * (1 + sl_pct), 6),
                    "take_profit": round(entry * (1 - tp_pct), 6),
                    "direction": "short",
                    "confidence": "high" if group_c else "medium",
                    "trigger": "rsi_fade" if group_a else ("bb_rejection" if group_b else "overbought_fade"),
                    "timestamp": str(timestamp)[:19] if timestamp else None,
                })
                daily_count[day_str] += 1

    return signals


def calculate_indicators_1h(df: pd.DataFrame) -> pd.DataFrame:
    """
    对 1H DataFrame 计算技术指标。
    复用 indicators.py 的 calculate_all。
    """
    from indicators import calculate_all
    return calculate_all(df.copy(), advanced=True)
