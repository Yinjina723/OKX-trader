# scripts/okx_install.py
"""环境检测与依赖安装。"""

import sys
import os
import subprocess


def check_python():
    """检查 Python 版本。"""
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 10):
        print(f"⚠ 当前 Python {v.major}.{v.minor}.{v.micro}, 建议 3.10+")
        return False
    print(f"✓ Python {v.major}.{v.minor}.{v.micro}")
    return True


def install_deps():
    """安装依赖。"""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    req_path = os.path.join(project_root, "requirements.txt")

    if not os.path.isfile(req_path):
        print(f"⚠ 未找到 requirements.txt: {req_path}")
        print("可能需要在项目根目录运行")
        return False

    print(f"安装依赖: {req_path}")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", req_path],
            cwd=project_root,
        )
        print("✓ 依赖安装完成")
        return True
    except subprocess.CalledProcessError:
        print("✗ 依赖安装失败")
        return False


def check_config():
    """检查 config.json 配置。"""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, "config.json")

    if not os.path.isfile(config_path):
        print(f"✗ 未找到 config.json: {config_path}")
        return False

    import json
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    issues = []
    if not cfg.get("API_KEY"):
        issues.append("API_KEY 未配置")
    if not cfg.get("SECRET_KEY"):
        issues.append("SECRET_KEY 未配置")
    if not cfg.get("PASSPHRASE"):
        issues.append("PASSPHRASE 未配置")
    if not cfg.get("SYMBOLS"):
        issues.append("SYMBOLS 为空")
    if not cfg.get("AI_API_KEY"):
        issues.append("AI_API_KEY (DeepSeek) 未配置")

    if issues:
        print("⚠ 配置问题:")
        for i in issues:
            print(f"  - {i}")
        return False

    print("✓ config.json 配置完整")
    return True


def main():
    print("=" * 50)
    print("  OKX 交易助手 — 环境检测")
    print("=" * 50)
    print()

    ok = True
    ok = check_python() and ok
    ok = install_deps() and ok
    ok = check_config() and ok

    print()
    if ok:
        print("✓ 环境就绪，可以运行:")
        print("  python scripts/okx_signal.py AI-USDT-SWAP")
        print("  python scripts/okx_web.py")
        print("  python scripts/okx_backtest.py AI-USDT-SWAP")
    else:
        print("⚠ 请修复上述问题后重试")


if __name__ == "__main__":
    main()
