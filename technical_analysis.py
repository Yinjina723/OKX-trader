# technical_analysis.py
"""
高级技术分析模块（纯特征提取，不做过滤决策）：

- detect_rsi_divergence:       RSI 背离检测（顶背离→看跌，底背离→看涨）
- detect_macd_divergence:      MACD 背离检测
- detect_candlestick_patterns: K 线形态识别（吞没、锤子线、十字星、三兵等）
- calculate_orderbook_features: 订单簿微观特征（多深度不平衡度、斜率、挂单墙）
- run_technical_batch:         综合批量分析入口

所有信号过滤逻辑已迁移到 signal_utils.py，各分析模块只做特征提取。
"""
import logging
import pandas as pd
import numpy as np
from typing import Dict, List

logger = logging.getLogger(__name__)


# ===================== 背离检测 =====================

def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 20) -> Dict:
    """
    检测 RSI 背离：
    - 顶背离（bearish divergence）：价格创新高但 RSI 下降 → 看跌
    - 底背离（bullish divergence）：价格创新低但 RSI 上升 → 看涨
    返回: {type: bull/bear/none, strength: strong/weak, detail: str}
    """
    if df.empty or len(df) < lookback:
        return {"type": "none", "strength": "weak", "detail": "数据不足"}

    recent = df.tail(lookback)
    mid = lookback // 2
    first_half = recent.iloc[:mid]
    second_half = recent.iloc[mid:]

    price_highs_1 = first_half['high'].max()
    price_highs_2 = second_half['high'].max()
    price_lows_1 = first_half['low'].min()
    price_lows_2 = second_half['low'].min()
    rsi_max_1 = first_half['RSI14'].max() if 'RSI14' in first_half.columns else 0
    rsi_max_2 = second_half['RSI14'].max() if 'RSI14' in second_half.columns else 0
    rsi_min_1 = first_half['RSI14'].min() if 'RSI14' in first_half.columns else 0
    rsi_min_2 = second_half['RSI14'].min() if 'RSI14' in second_half.columns else 0

    # 顶背离：后段价格更高但 RSI 最高值更低
    if price_highs_2 > price_highs_1 * 1.005 and rsi_max_2 < rsi_max_1 - 5:
        return {
            "type": "bearish_divergence",
            "strength": "strong" if rsi_max_2 < rsi_max_1 - 10 else "weak",
            "detail": f"价格新高({price_highs_2:.4f}>{price_highs_1:.4f})但RSI下降({rsi_max_2:.1f}<{rsi_max_1:.1f})"
        }

    # 底背离：后段价格更低但 RSI 最低值更高
    if price_lows_2 < price_lows_1 * 0.995 and rsi_min_2 > rsi_min_1 + 5:
        return {
            "type": "bullish_divergence",
            "strength": "strong" if rsi_min_2 > rsi_min_1 + 10 else "weak",
            "detail": f"价格新低({price_lows_2:.4f}<{price_lows_1:.4f})但RSI上升({rsi_min_2:.1f}>{rsi_min_1:.1f})"
        }

    return {"type": "none", "strength": "weak", "detail": "无背离"}


def detect_macd_divergence(df: pd.DataFrame, lookback: int = 20) -> Dict:
    """
    检测 MACD 柱状图背离：
    - 顶背离：价格创新高但 MACD Hist 下降
    - 底背离：价格创新低但 MACD Hist 上升
    """
    if df.empty or len(df) < lookback or 'MACD_Hist' not in df.columns:
        return {"type": "none", "detail": "数据不足"}

    recent = df.tail(lookback)
    mid = lookback // 2
    first_half = recent.iloc[:mid]
    second_half = recent.iloc[mid:]

    ph1, ph2 = first_half['high'].max(), second_half['high'].max()
    pl1, pl2 = first_half['low'].min(), second_half['low'].min()
    mh1, mh2 = first_half['MACD_Hist'].max(), second_half['MACD_Hist'].max()
    ml1, ml2 = first_half['MACD_Hist'].min(), second_half['MACD_Hist'].min()

    if ph2 > ph1 * 1.005 and mh2 < mh1 * 0.7:
        return {"type": "bearish_divergence", "detail": f"MACD顶背离 ({ph2:.4f}>{ph1:.4f}, Hist {mh2:.4f}<{mh1:.4f})"}
    if pl2 < pl1 * 0.995 and ml2 > ml1 * 1.3:
        return {"type": "bullish_divergence", "detail": f"MACD底背离 ({pl2:.4f}<{pl1:.4f}, Hist {ml2:.4f}>{ml1:.4f})"}

    return {"type": "none", "detail": "无背离"}


# ===================== K 线形态识别 =====================

def detect_candlestick_patterns(df: pd.DataFrame) -> List[Dict]:
    """
    识别最近几根 K 线的经典形态。
    返回形态列表: [{name: str, at: idx, direction: bull/bear, detail: str}]
    """
    if df.empty or len(df) < 3:
        return []

    patterns = []
    latest = df.iloc[-3:]  # 最近 3 根
    c1 = latest.iloc[-3]  # T-2
    c2 = latest.iloc[-2]  # T-1
    c3 = latest.iloc[-1]  # T（当前）

    o1, c = float(c1['open']), float(c1['close'])
    o2, cl2 = float(c2['open']), float(c2['close'])
    o3, cl3 = float(c3['open']), float(c3['close'])
    h3, l3 = float(c3['high']), float(c3['low'])
    h2, l2 = float(c2['high']), float(c2['low'])

    body2 = abs(cl2 - o2)
    body3 = abs(cl3 - o3)
    total3 = h3 - l3
    upper_shadow3 = h3 - max(o3, cl3)
    lower_shadow3 = min(o3, cl3) - l3

    # 吞没形态
    if cl2 < o2 and cl3 > o3:  # 前阴后阳
        if o3 <= cl2 and cl3 >= o2 and body3 > body2 * 1.2:
            patterns.append({"name": "bullish_engulfing", "at": str(c3.name),
                             "direction": "bull", "detail": "看涨吞没"})
    elif cl2 > o2 and cl3 < o3:  # 前阳后阴
        if o3 >= cl2 and cl3 <= o2 and body3 > body2 * 1.2:
            patterns.append({"name": "bearish_engulfing", "at": str(c3.name),
                             "direction": "bear", "detail": "看跌吞没"})

    # 锤子线 / 上吊线
    if body3 > 0 and total3 > 0:
        if lower_shadow3 > body3 * 2 and upper_shadow3 < body3 * 0.3:
            name = "hammer"  # 锤子线
            patterns.append({"name": name, "at": str(c3.name),
                             "direction": "bull", "detail": "锤子线(底部反转)"})
        elif upper_shadow3 > body3 * 2 and lower_shadow3 < body3 * 0.3:
            name = "shooting_star"
            patterns.append({"name": name, "at": str(c3.name),
                             "direction": "bear", "detail": "射击之星(顶部反转)"})

    # 十字星 (Doji)
    if body3 < total3 * 0.1 and body3 > 0:
        patterns.append({"name": "doji", "at": str(c3.name),
                         "direction": "neutral", "detail": "十字星(变盘信号)"})

    # 三白兵 / 三乌鸦 (需要 3 根同向大阳/大阴)
    if len(latest) >= 3 and body3 > 0:
        b1 = abs(float(c1['close']) - o1)
        b2 = body2
        b3 = body3
        if (cl3 > o3 and cl2 > o2 and float(c1['close']) > o1 and
            b3 > b2 > 0 and b2 > b1 > 0 and
            o2 >= o1 and o3 >= o2):
            patterns.append({"name": "three_white_soldiers", "at": str(c3.name),
                             "direction": "bull", "detail": "三白兵"})
        elif (cl3 < o3 and cl2 < o2 and float(c1['close']) < o1 and
              b3 > b2 > 0 and b2 > b1 > 0 and
              o2 <= o1 and o3 <= o2):
            patterns.append({"name": "three_black_crows", "at": str(c3.name),
                             "direction": "bear", "detail": "三乌鸦"})

    return patterns


# ===================== 订单簿微观特征 =====================

def calculate_orderbook_features(orderbook: Dict, depth: int = 5) -> Dict:
    """
    从订单簿提取微观结构特征。
    orderbook: OKX 返回的原始格式，含 bids/asks 列表。
    """
    bids = orderbook.get('bids', []) if orderbook else []
    asks = orderbook.get('asks', []) if orderbook else []

    if len(bids) < depth or len(asks) < depth:
        logger.debug(f"订单簿深度不足 (bids:{len(bids)} asks:{len(asks)})，需要 {depth}")
        return {}

    features = {}

    # 1. 各深度累计不平衡度
    for d in range(1, depth + 1):
        bid_cum = sum(float(b[1]) for b in bids[:d])
        ask_cum = sum(float(a[1]) for a in asks[:d])
        total = bid_cum + ask_cum
        features[f'ob_imbalance_{d}'] = round((bid_cum - ask_cum) / total, 4) if total > 0 else 0

    # 2. 订单簿斜率（挂单量在深度上的衰减速度）
    if depth >= 3:
        bid_slope = float(bids[0][1]) - float(bids[min(depth - 1, len(bids) - 1)][1])
        ask_slope = float(asks[0][1]) - float(asks[min(depth - 1, len(asks) - 1)][1])
        features['ob_slope_ratio'] = round(bid_slope / ask_slope, 4) if ask_slope != 0 else 0

    # 3. 挂单墙检测（某个价位有异常大的挂单）
    avg_bid = sum(float(b[1]) for b in bids[:depth]) / depth
    avg_ask = sum(float(a[1]) for a in asks[:depth]) / depth
    features['bid_wall'] = round(max(float(b[1]) for b in bids[:depth]) / avg_bid, 4) if avg_bid > 0 else 1
    features['ask_wall'] = round(max(float(a[1]) for a in asks[:depth]) / avg_ask, 4) if avg_ask > 0 else 1

    # 4. 买卖价差
    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    spread = best_ask - best_bid
    mid_price = (best_ask + best_bid) / 2
    features['spread'] = round(spread, 8)
    features['spread_pct'] = round(spread / mid_price * 100, 6) if mid_price > 0 else 0

    return features


# ===================== 综合批量分析 =====================

def run_technical_batch(df: pd.DataFrame, orderbook: Dict = None) -> Dict:
    """
    对最新数据运行全部技术分析，返回整合结果。
    供 main.py 在信号生成时使用，结果可追加到 AI prompt 中。
    """
    result = {}

    # 背离
    result['rsi_divergence'] = detect_rsi_divergence(df)
    result['macd_divergence'] = detect_macd_divergence(df)

    # K 线形态
    result['candlestick_patterns'] = detect_candlestick_patterns(df)

    # 订单簿特征（如果提供）
    if orderbook:
        result['orderbook_features'] = calculate_orderbook_features(orderbook)
    else:
        result['orderbook_features'] = {}

    return result
