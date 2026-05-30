# rule_strategy.py
"""
规则策略：基于 RSI、布林带、均线、资金费率与多空比等规则生成交易信号。

不调用 AI，仅根据阈值与多空条件给出 direction、entry、stop_loss、take_profit1/2、strength、market_state。
止损止盈由 ATR 倍数推导；可与 AI 信号一并传入 AI 作为「规则建议」参考。
"""
import pandas as pd
from typing import Dict
from config import Config


def generate_rule_signal(
    df: pd.DataFrame,
    current_price: float,
    config: Config,
    funding_rate: float = 0,
    ls_ratio: float = 0,
    elite_ratio: float = 0,
    oi: float = 0,
    oi_change: float = 0,
    net_taker: float = 0,
    premium: float = 0,
    bid_ask_ratio: float = 0,
    long_trend: str = "未知",
    long_ma60: float = 0
) -> Dict:
    """
    根据规则生成交易信号。
    返回字典，包含 direction, entry, stop_loss, take_profit1, take_profit2, strength, market_state。
    若无信号，direction="neutral"。
    """
    # 获取最新数据行
    latest = df.iloc[-1]
    rsi = latest.get('RSI14', 50)
    bb_pos = latest.get('BB_position', 0.5)
    atr = latest.get('ATR', 0)
    close = latest['close']
    ma5 = latest.get('MA5', close)
    ma30 = latest.get('MA30', close)

    # 初始化信号
    signal = {
        "direction": "neutral",
        "entry": current_price,
        "stop_loss": None,
        "take_profit1": None,
        "take_profit2": None,
        "strength": "weak",
        "market_state": "未知"
    }

    # 从配置读取阈值
    rsi_oversold = getattr(config, 'RSI_OVERSOLD', 30)
    rsi_overbought = getattr(config, 'RSI_OVERBOUGHT', 70)
    bb_threshold = getattr(config, 'BB_POSITION_THRESHOLD', 0.2)
    funding_threshold = getattr(config, 'FUNDING_RATE_THRESHOLD', 0.0001)
    ls_threshold = getattr(config, 'LS_RATIO_THRESHOLD', 2.0)

    # 子规则1：RSI+布林带反转
    if rsi < rsi_oversold and bb_pos < bb_threshold:
        signal['direction'] = 'long'
        signal['market_state'] = '超卖'
        if rsi < 20:
            signal['strength'] = 'strong'
        elif rsi < 30:
            signal['strength'] = 'medium'
        else:
            signal['strength'] = 'weak'
    elif rsi > rsi_overbought and bb_pos > (1 - bb_threshold):
        signal['direction'] = 'short'
        signal['market_state'] = '超买'
        if rsi > 80:
            signal['strength'] = 'strong'
        elif rsi > 70:
            signal['strength'] = 'medium'
        else:
            signal['strength'] = 'weak'

    # 子规则2：均线趋势（如果规则1未触发）
    if signal['direction'] == 'neutral' and ma5 > ma30 * 1.01:
        signal['direction'] = 'long'
        signal['market_state'] = '多头趋势'
        signal['strength'] = 'medium'
    elif signal['direction'] == 'neutral' and ma5 < ma30 * 0.99:
        signal['direction'] = 'short'
        signal['market_state'] = '空头趋势'
        signal['strength'] = 'medium'

    # 如果规则1或2有方向，计算止损止盈（基于ATR）
    if signal['direction'] != 'neutral' and atr > 0:
        if signal['direction'] == 'long':
            signal['stop_loss'] = current_price - 2 * atr
            signal['take_profit1'] = current_price + 2 * atr
            signal['take_profit2'] = current_price + 4 * atr
        else:
            signal['stop_loss'] = current_price + 2 * atr
            signal['take_profit1'] = current_price - 2 * atr
            signal['take_profit2'] = current_price - 4 * atr

    # 子规则3：情绪过滤
    if funding_rate > funding_threshold and ls_ratio > ls_threshold:
        if signal['direction'] == 'long':
            signal['strength'] = 'weak'  # 多头过热，降低强度
        elif signal['direction'] == 'neutral':
            signal['direction'] = 'short'
            signal['market_state'] = '情绪过热'
            signal['strength'] = 'weak'
    elif funding_rate < -funding_threshold and ls_ratio < 1/ls_threshold:
        if signal['direction'] == 'short':
            signal['strength'] = 'weak'
        elif signal['direction'] == 'neutral':
            signal['direction'] = 'long'
            signal['market_state'] = '情绪过冷'
            signal['strength'] = 'weak'

    return signal