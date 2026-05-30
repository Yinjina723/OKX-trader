# backtest.py
"""
回测模块：读取 OUTPUT_DIR/signals_history.csv，按时间顺序回放 AI 信号并模拟开平仓。

Portfolio：模拟资金、手续费、滑点，记录开平仓与权益曲线。
run_backtest(config, symbol)：按 symbol 筛选信号，有 long/short 且无持仓时开仓，
有持仓时根据止损/止盈价判断平仓，最后输出总收益、最大回撤、夏普比率等。
"""
import os
import logging
from typing import Dict, List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class Portfolio:
    """模拟交易账户：资金、持仓、开平仓记录与权益曲线，用于回测。"""
    def __init__(self, initial_capital: float, fee_rate: float = 0.0005, slippage: float = 0.001):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.position = 0.0          # 正数为多头持仓，负数为空头持仓（以合约张数或币数计）
        self.entry_price = 0.0
        self.trades = []              # 记录每笔交易
        self.equity_curve = []         # 记录每个时间点的权益
        self.fee_rate = fee_rate
        self.slippage = slippage

    def open_position(self, direction: str, price: float, size: float, timestamp, signal: Dict = None):
        """开仓"""
        # 考虑滑点
        exec_price = price * (1 + self.slippage) if direction == 'long' else price * (1 - self.slippage)
        cost = size * exec_price
        fee = cost * self.fee_rate
        if cost + fee > self.capital:
            # 资金不足，按最大可开仓量
            size = self.capital / (exec_price * (1 + self.fee_rate))
            cost = size * exec_price
            fee = cost * self.fee_rate
        self.capital -= (cost + fee)
        self.position = size if direction == 'long' else -size
        self.entry_price = exec_price
        self.trades.append({
            'time': timestamp,
            'type': 'open',
            'direction': direction,
            'price': exec_price,
            'size': size,
            'fee': fee,
            'signal': signal
        })

    def close_position(self, price: float, timestamp, reason: str = 'manual'):
        """平仓"""
        if self.position == 0:
            return
        exec_price = price * (1 - self.slippage) if self.position > 0 else price * (1 + self.slippage)
        value = abs(self.position) * exec_price
        fee = value * self.fee_rate
        self.capital += (value - fee)
        self.trades.append({
            'time': timestamp,
            'type': 'close',
            'price': exec_price,
            'size': abs(self.position),
            'fee': fee,
            'reason': reason
        })
        self.position = 0.0
        self.entry_price = 0.0

    def update_equity(self, current_price: float):
        """更新当前权益（包括浮动盈亏）"""
        if self.position != 0:
            unrealized = self.position * (current_price - self.entry_price)
        else:
            unrealized = 0
        total_equity = self.capital + unrealized
        self.equity_curve.append(total_equity)
        return total_equity

    def get_metrics(self):
        """计算绩效指标"""
        if len(self.equity_curve) < 2:
            return {}
        equity = pd.Series(self.equity_curve)
        returns = equity.pct_change().dropna()
        total_return = (equity.iloc[-1] - self.initial_capital) / self.initial_capital
        # 最大回撤
        peak = equity.expanding().max()
        drawdown = (equity - peak) / peak
        max_drawdown = drawdown.min()
        # 夏普比率（假设无风险利率为 0，年化 252 天）
        sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() != 0 else 0
        return {
            'total_return': total_return,   #总收益率（最终权益 / 初始资金 - 1）
            'max_drawdown': max_drawdown,   #最大回撤
            'sharpe_ratio': sharpe,            #夏普率（按日收益年化）
            'final_equity': equity.iloc[-1]    #最终权益
        }


def run_backtest(config, symbol: str):
    """
    对指定 symbol 运行回测：从 signals_history.csv 按时间顺序读取该交易对的 AI 信号与价格，
    无持仓且信号非中性时按强度比例开仓，有持仓时检查止损/止盈并平仓，结束时输出绩效指标。
    """
    history_file = os.path.join(config.OUTPUT_DIR, "signals_history.csv")
    if not os.path.isfile(history_file):
        logger.error(f"历史信号文件不存在: {history_file}")
        return

    df_signals = pd.read_csv(history_file, parse_dates=['timestamp'])
    df_signals = df_signals[df_signals['symbol'] == symbol].sort_values('timestamp')
    if df_signals.empty:
        logger.error(f"没有 {symbol} 的信号记录")
        return

    # 初始化投资组合
    portfolio = Portfolio(
        initial_capital=getattr(config, 'BACKTEST_INITIAL_CAPITAL', 10000),
        fee_rate=getattr(config, 'BACKTEST_FEE_RATE', 0.0005),
        slippage=getattr(config, 'BACKTEST_SLIPPAGE', 0.001)
    )

    # 逐条信号处理
    for _, row in df_signals.iterrows():
        timestamp = row['timestamp']
        ai_direction = row['ai_direction']
        current_price = row['current_price']
        stop_loss = row.get('ai_stop_loss')
        take_profit1 = row.get('ai_take_profit1')

        # 有持仓时检查止损止盈（需为有效数值）
        if portfolio.position != 0 and pd.notna(stop_loss) and pd.notna(take_profit1):
            try:
                sl, tp1 = float(stop_loss), float(take_profit1)
            except (TypeError, ValueError):
                sl = tp1 = None
            if sl is not None and tp1 is not None:
                if (portfolio.position > 0 and current_price <= sl) or (portfolio.position < 0 and current_price >= sl):
                    portfolio.close_position(current_price, timestamp, reason='stop_loss')
                elif (portfolio.position > 0 and current_price >= tp1) or (portfolio.position < 0 and current_price <= tp1):
                    portfolio.close_position(current_price, timestamp, reason='take_profit1')

        # 无持仓且信号非中性，开仓
        if portfolio.position == 0 and ai_direction != 'neutral' and pd.notna(ai_direction):
            # 根据信号强度确定仓位比例（从配置读取）
            strength = row['ai_strength']
            allocation_map = getattr(config, 'STRENGTH_ALLOCATION', {'strong':0.5, 'medium':0.25, 'weak':0.1})
            ratio = allocation_map.get(strength, 0.25)
            size = portfolio.capital * ratio / current_price
            portfolio.open_position(ai_direction, current_price, size, timestamp, signal=row.to_dict())

        # 更新权益
        portfolio.update_equity(current_price)

    # 回测结束，平仓
    if portfolio.position != 0:
        portfolio.close_position(df_signals.iloc[-1]['current_price'], df_signals.iloc[-1]['timestamp'], reason='end')

    # 输出绩效
    metrics = portfolio.get_metrics()
    logger.info(f"回测完成，{symbol} 绩效: {metrics}")
    return metrics