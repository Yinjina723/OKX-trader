import os
import pandas as pd
import numpy as np
import ccxt
from datetime import datetime, timedelta

# ========== 配置参数 ==========
SYMBOL = 'BTC/USDT'
TIMEFRAME = '1h'
LOOKBACK_DAYS = 60
ATR_PERIOD = 14
STOP_LOSS_ATR = 2.0
TAKE_PROFIT_ATR = 3.0

# 因子权重（可调整）
WEIGHTS = {
    'ema': 1.0,
    'macd': 1.5,
    'rsi': 1.0,
    'stoch': 0.8,
    'volume': 0.5,
    'bb': 0.7
}

# 信号强度阈值（综合分数绝对值达到多少判定为strong/medium/weak）
STRONG_THRESHOLD = 3.0
MEDIUM_THRESHOLD = 1.5


# ==============================

def fetch_ohlcv(symbol, timeframe, limit):
    exchange = ccxt.binance()
    since = exchange.parse8601((datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)).isoformat())
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df


def calculate_atr(df, period):
    high, low, close = df['high'], df['low'], df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr


def compute_indicators(df):
    """计算所有技术指标"""
    # EMA
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()

    # MACD
    exp12 = df['close'].ewm(span=12, adjust=False).mean()
    exp26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp12 - exp26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']

    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # 随机指标
    low14 = df['low'].rolling(window=14).min()
    high14 = df['high'].rolling(window=14).max()
    df['stoch_k'] = 100 * (df['close'] - low14) / (high14 - low14)
    df['stoch_d'] = df['stoch_k'].rolling(window=3).mean()

    # 成交量均线
    df['volume_ma20'] = df['volume'].rolling(window=20).mean()

    # 布林带
    df['bb_mid'] = df['close'].rolling(window=20).mean()
    bb_std = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['bb_mid'] + 2 * bb_std
    df['bb_lower'] = df['bb_mid'] - 2 * bb_std

    # ATR
    df['atr'] = calculate_atr(df, ATR_PERIOD)

    return df


def score_ema(row):
    """EMA交叉趋势打分"""
    if pd.isna(row['ema20']) or pd.isna(row['ema50']):
        return 0
    if row['ema20'] > row['ema50']:
        # 看多趋势
        return 0.5 if row['close'] > row['ema20'] else 0.2
    else:
        # 看空趋势
        return -0.5 if row['close'] < row['ema20'] else -0.2


def score_macd(row):
    """MACD信号打分"""
    if pd.isna(row['macd']) or pd.isna(row['macd_signal']):
        return 0
    # 金叉死叉：当前柱状图与前一期比较
    # 这里简化：用macd与信号线的差值
    diff = row['macd'] - row['macd_signal']
    if diff > 0 and diff > row.get('prev_macd_diff', 0):
        return 1.5  # 金叉增强
    elif diff < 0 and diff < row.get('prev_macd_diff', 0):
        return -1.5  # 死叉增强
    elif diff > 0:
        return 0.5
    elif diff < 0:
        return -0.5
    else:
        return 0


def score_rsi(row):
    """RSI超买超卖打分"""
    if pd.isna(row['rsi']):
        return 0
    if row['rsi'] < 30:
        return 1.0
    elif row['rsi'] > 70:
        return -1.0
    else:
        return 0


def score_stoch(row):
    """随机指标交叉打分"""
    if pd.isna(row['stoch_k']) or pd.isna(row['stoch_d']):
        return 0
    # K线上穿D线且低于20 → 买入
    if row['stoch_k'] > row['stoch_d'] and row['stoch_k'] < 20:
        return 1.0
    # K线下穿D线且高于80 → 卖出
    elif row['stoch_k'] < row['stoch_d'] and row['stoch_k'] > 80:
        return -1.0
    else:
        return 0


def score_volume(row):
    """成交量异常打分"""
    if pd.isna(row['volume']) or pd.isna(row['volume_ma20']) or row['volume_ma20'] == 0:
        return 0
    vol_ratio = row['volume'] / row['volume_ma20']
    if vol_ratio > 2.0:
        # 放量，配合价格方向
        if row['close'] > row['open']:  # 阳线
            return 0.5
        else:
            return -0.5
    elif vol_ratio < 0.5:
        # 缩量，可能趋势减弱
        return 0
    else:
        return 0


def score_bb(row):
    """布林带位置打分"""
    if pd.isna(row['bb_upper']) or pd.isna(row['bb_lower']):
        return 0
    if row['close'] > row['bb_upper']:
        return -0.5  # 突破上轨，可能回调
    elif row['close'] < row['bb_lower']:
        return 0.5  # 突破下轨，可能反弹
    else:
        return 0


def generate_signals(df):
    # 计算所有指标
    df = compute_indicators(df)

    # 初始化信号列
    df['ai_direction'] = 'neutral'
    df['ai_strength'] = None
    df['ai_stop_loss'] = np.nan
    df['ai_take_profit1'] = np.nan
    df['total_score'] = 0.0

    # 预计算前一个macd_diff用于判断金叉死叉的变化
    df['prev_macd_diff'] = df['macd'].shift(1) - df['macd_signal'].shift(1)

    # 遍历每一行（从第50行开始，保证所有指标有效）
    for i in range(50, len(df)):
        row = df.iloc[i]
        # 计算各因子分数
        scores = {}
        scores['ema'] = score_ema(row) * WEIGHTS['ema']
        scores['macd'] = score_macd(row) * WEIGHTS['macd']
        scores['rsi'] = score_rsi(row) * WEIGHTS['rsi']
        scores['stoch'] = score_stoch(row) * WEIGHTS['stoch']
        scores['volume'] = score_volume(row) * WEIGHTS['volume']
        scores['bb'] = score_bb(row) * WEIGHTS['bb']

        total = sum(scores.values())
        df.loc[df.index[i], 'total_score'] = total

        # 根据总分决定方向
        if total > MEDIUM_THRESHOLD:
            direction = 'long'
            if total > STRONG_THRESHOLD:
                strength = 'strong'
            else:
                strength = 'medium'
        elif total < -MEDIUM_THRESHOLD:
            direction = 'short'
            if total < -STRONG_THRESHOLD:
                strength = 'strong'
            else:
                strength = 'medium'
        else:
            # 中性，不产生信号
            continue

        # 记录信号
        df.loc[df.index[i], 'ai_direction'] = direction
        df.loc[df.index[i], 'ai_strength'] = strength

        # 计算止损止盈（基于当前收盘价和ATR）
        atr = row['atr']
        close = row['close']
        if direction == 'long':
            sl = close - STOP_LOSS_ATR * atr
            tp = close + TAKE_PROFIT_ATR * atr
        else:
            sl = close + STOP_LOSS_ATR * atr
            tp = close - TAKE_PROFIT_ATR * atr
        df.loc[df.index[i], 'ai_stop_loss'] = sl
        df.loc[df.index[i], 'ai_take_profit1'] = tp

    # 添加回测所需字段
    df['current_price'] = df['close']
    df['symbol'] = SYMBOL
    return df


def main():
    df = fetch_ohlcv(SYMBOL, TIMEFRAME, limit=1000)
    print(f"获取到 {len(df)} 条K线数据")

    df_signals = generate_signals(df)

    # 保存信号
    output_dir = 'output'
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, 'signals_history.csv')
    cols = ['timestamp', 'symbol', 'current_price',
            'ai_direction', 'ai_strength',
            'ai_stop_loss', 'ai_take_profit1']
    # 只保留有信号的记录（可选，也可以保留所有）
    df_signals_filtered = df_signals[df_signals['ai_direction'] != 'neutral']
    df_signals_filtered[cols].to_csv(output_file, index=False)
    print(f"信号已保存至 {output_file}，共 {len(df_signals_filtered)} 条信号")


if __name__ == '__main__':
    main()