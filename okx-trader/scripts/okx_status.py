# scripts/okx_status.py
"""快捷脚本：读取最新交易信号状态。"""

import sys
import os
import json
import glob
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def read_status(symbol: str = None):
    """读取最新信号文件并输出摘要。"""
    config_path = os.path.join(PROJECT_ROOT, "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    output_dir = config.get("OUTPUT_DIR", "./output")
    if not os.path.isdir(output_dir):
        print(f"输出目录不存在: {output_dir}")
        return

    # 找到最新的信号文件
    pattern = os.path.join(output_dir, "点位+网格+*.txt")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)

    if symbol:
        # 筛选指定 symbol
        clean = symbol.replace("/", "_")
        files = [f for f in files if clean in f]

    if not files:
        print("没有找到信号文件")
        return

    latest_file = files[0]
    mtime = datetime.fromtimestamp(os.path.getmtime(latest_file))
    print(f"最新信号文件: {os.path.basename(latest_file)}")
    print(f"最后更新: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 50)

    with open(latest_file, "r", encoding="utf-8") as f:
        content = f.read()

    # 只打印最后 30 行
    lines = content.strip().split("\n")
    recent = lines[-30:] if len(lines) > 30 else lines
    for line in recent:
        print(line)

    print("-" * 50)
    print(f"完整文件: {latest_file}")


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else None
    read_status(symbol)
