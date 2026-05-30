# scripts/okx_signal.py
"""快捷脚本：为指定交易对生成交易信号。"""

import sys
import os
import json
import logging

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import Config
from logger import setup_logger
from okx_client import OKXClient
from main import prepare_data_and_get_signal, save_signal_to_txt
from grid_manager import GridManager


def run_signal(symbol: str):
    """运行一次信号分析并打印结果。"""
    config = Config(os.path.join(PROJECT_ROOT, "config.json"))
    logger = setup_logger(config)

    if symbol not in config.SYMBOLS:
        print(f"错误: {symbol} 不在配置的 SYMBOLS 中")
        print(f"当前 SYMBOLS: {config.SYMBOLS}")
        sys.exit(1)

    print(f"正在为 {symbol} 生成信号...")
    client = OKXClient(config)
    grid_manager = GridManager(
        config=config,
        default_range_percent=getattr(config, "GRID_DEFAULT_RANGE_PERCENT", 0.2),
        default_grid_count=getattr(config, "GRID_DEFAULT_COUNT", 10),
    )

    ticker = client.get_ticker(symbol)
    current_price = float(ticker.get("last", 0)) if ticker else 0
    if current_price <= 0:
        print(f"错误: 无法获取 {symbol} 当前价格")
        sys.exit(1)

    print(f"当前价格: {current_price:.6f}")

    signal = prepare_data_and_get_signal(config, client, symbol)

    if not signal or signal.get("direction") == "neutral":
        print("结果: 无明确交易信号（观望）")
    else:
        direction = signal["direction"]
        dir_cn = "做多" if direction == "long" else "做空"
        print(f"方向: {dir_cn}")
        print(f"入场: {signal['entry']:.6f}")
        print(f"止损: {signal['stop_loss']:.6f}")
        print(f"止盈1: {signal.get('take_profit1', 'N/A')}")
        print(f"止盈2: {signal.get('take_profit2', 'N/A')}")
        print(f"强度: {signal.get('strength', 'N/A')}")
        print(f"市场状态: {signal.get('market_state', 'N/A')}")

        manip = signal.get("manipulation")
        if manip:
            phase = manip.get("phase_result", {})
            nm = manip.get("next_move", {})
            print(f"\n--- 操盘分析 ---")
            print(f"阶段: {phase.get('phase_cn', 'N/A')} (评分:{phase.get('score', 0)})")
            print(f"庄家下一步: {nm.get('next_action', 'N/A')}")
            print(f"目标价: {nm.get('target_price', 'N/A')}")

    # 写入文件
    save_signal_to_txt(config, symbol, signal)

    # 网格建议
    if signal and signal.get("direction") != "neutral" and current_price > 0:
        grid_text = grid_manager.get_grid_suggestions(current_price, signal)
        print(f"\n--- 网格建议 ---")
        print(grid_text)

    return signal


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python okx_signal.py <SYMBOL>")
        print("示例: python okx_signal.py AI-USDT-SWAP")
        sys.exit(1)
    run_signal(sys.argv[1])
