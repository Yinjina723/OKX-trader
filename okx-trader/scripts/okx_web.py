# scripts/okx_web.py
"""快捷脚本：启动 Web 面板。"""

import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def start_web(host: str = "0.0.0.0", port: int = 5000, debug: bool = False):
    """启动 Flask Web 面板。"""
    os.chdir(PROJECT_ROOT)

    from web_app import app
    print(f"OKX 交易助手 Web 面板启动中...")
    print(f"访问地址: http://{host}:{port}")
    print(f"按 Ctrl+C 停止")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    start_web(host=host, port=port)
