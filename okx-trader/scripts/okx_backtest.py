# scripts/okx_backtest.py
"""快捷脚本：运行回测。"""

import sys
import os
import json
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import Config
from logger import setup_logger
from backtest import run_backtest


def do_backtest(symbol: str):
    """运行回测并打印结果。"""
    config_path = os.path.join(PROJECT_ROOT, "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if symbol not in raw.get("SYMBOLS", []):
        print(f"错误: {symbol} 不在配置的 SYMBOLS 中")
        print(f"当前 SYMBOLS: {raw.get('SYMBOLS', [])}")
        sys.exit(1)

    # 临时开启回测模式
    raw["BACKTEST_ENABLED"] = True
    config = Config(config_path)
    config.BACKTEST_ENABLED = True
    logger = setup_logger(config)

    print(f"正在运行 {symbol} 回测...")
    try:
        metrics = run_backtest(config, symbol)
    except Exception as e:
        print(f"回测失败: {e}")
        sys.exit(1)

    if not metrics:
        print("回测无结果（可能没有历史信号数据）")
        print("请先运行 okx_signal.py 生成历史信号")
        sys.exit(1)

    total = float(metrics.get("total_return", 0))
    mdd = float(metrics.get("max_drawdown", 0))
    sharpe = float(metrics.get("sharpe_ratio", 0))
    equity = float(metrics.get("final_equity", 0))

    print(f"\n===== 回测结果: {symbol} =====")
    print(f"总收益率:   {total * 100:+.2f}%")
    print(f"最大回撤:   {mdd * 100:.2f}%")
    print(f"夏普比率:   {sharpe:.2f}")
    print(f"最终权益:   {equity:.2f}")
    print(f"交易次数:   {metrics.get('total_trades', 0)}")
    print(f"胜率:       {metrics.get('win_rate', 0) * 100:.1f}%" if metrics.get('win_rate') is not None else "")

    return metrics


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python okx_backtest.py <SYMBOL>")
        print("示例: python okx_backtest.py AI-USDT-SWAP")
        sys.exit(1)
    do_backtest(sys.argv[1])
