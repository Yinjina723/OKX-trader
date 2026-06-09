# local_server.py
"""本地代理面板 —— 转发请求到新加坡服务器，拦截分析结果写入本地 Excel。

使用方式:
    python local_server.py
    浏览器访问 http://localhost:5001

新加坡服务器地址在 config.json 的 REMOTE_SERVER 字段配置。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import requests
from flask import Flask, jsonify, request, render_template, Response

# 确保能找到项目内的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from trade_log import write_signal, init_log_file

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("local_server")

# ── 加载配置 ──
cfg = Config("config.json")
REMOTE = cfg.REMOTE_SERVER.rstrip("/")

logger.info("新加坡服务器地址: %s", REMOTE)


# ══════════════════════════════════════════════════════════════
#  页面路由（本地渲染模板）
# ══════════════════════════════════════════════════════════════

@app.get("/")
def index() -> str:
    return render_template("panel.html", symbols=cfg.SYMBOLS)


@app.get("/backtest")
def backtest_page() -> str:
    return render_template("backtest.html", symbols=cfg.SYMBOLS)


# ══════════════════════════════════════════════════════════════
#  API 代理
# ══════════════════════════════════════════════════════════════

def _forward(method: str, path: str, json_body: dict = None, timeout: int = 120) -> Response:
    """将请求转发到新加坡服务器，返回 Flask Response。"""
    url = f"{REMOTE}{path}"
    headers = {k: v for k, v in request.headers if k.lower() not in ("host", "content-length")}

    try:
        if method == "GET":
            resp = requests.get(url, params=request.args, headers=headers, timeout=timeout, stream=True)
        elif method in ("POST", "PUT", "DELETE"):
            resp = requests.request(
                method, url,
                json=json_body,
                headers=headers,
                timeout=timeout,
                stream=True,
            )
        else:
            return jsonify({"error": f"不支持的方法: {method}"}), 405

        # 构造响应（排除 hop-by-hop headers）
        excluded = {"transfer-encoding", "content-encoding", "connection", "keep-alive"}
        resp_headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in excluded]

        return Response(
            resp.content,
            status=resp.status_code,
            headers=resp_headers,
            content_type=resp.headers.get("content-type", "application/json"),
        )

    except requests.exceptions.ConnectionError:
        logger.error("无法连接到新加坡服务器: %s", REMOTE)
        return jsonify({"error": f"无法连接到新加坡服务器 {REMOTE}，请检查服务器是否在线"}), 502
    except requests.exceptions.Timeout:
        logger.error("请求超时: %s", url)
        return jsonify({"error": "新加坡服务器响应超时"}), 504
    except Exception as e:
        logger.exception("代理请求异常: %s", e)
        return jsonify({"error": f"代理请求失败: {str(e)}"}), 500


# ══════════════════════════════════════════════════════════════
#  /api/analyze —— 特殊处理：拦截结果写入本地 Excel
# ══════════════════════════════════════════════════════════════

@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """日线分析 —— 转发到新加坡，拦截结果写入本地 Excel。"""
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol", "")

    url = f"{REMOTE}/api/analyze"
    headers = {k: v for k, v in request.headers if k.lower() not in ("host", "content-length")}

    try:
        resp = requests.post(url, json=data, headers=headers, timeout=180)

        # 如果分析成功，写入本地 Excel
        if resp.status_code == 200:
            try:
                result = resp.json()
                signal = result.get("signal")
                if signal and signal.get("direction") in ("long", "short"):
                    _write_local_log(symbol, signal)
            except Exception:
                pass  # JSON 解析失败不影响返回

        # 返回原响应
        excluded = {"transfer-encoding", "content-encoding", "connection", "keep-alive"}
        resp_headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in excluded]

        return Response(
            resp.content,
            status=resp.status_code,
            headers=resp_headers,
            content_type=resp.headers.get("content-type", "application/json"),
        )

    except requests.exceptions.ConnectionError:
        logger.error("无法连接到新加坡服务器: %s", REMOTE)
        return jsonify({"error": f"无法连接到新加坡服务器 {REMOTE}，请检查服务器是否在线"}), 502
    except requests.exceptions.Timeout:
        logger.error("请求超时: %s", url)
        return jsonify({"error": "新加坡服务器响应超时"}), 504
    except Exception as e:
        logger.exception("代理请求异常: %s", e)
        return jsonify({"error": f"代理请求失败: {str(e)}"}), 500


def _write_local_log(symbol: str, signal: dict):
    """将 AI 分析结果写入本地交易日志 Excel。"""
    try:
        init_log_file()

        direction = signal.get("direction", "neutral")
        entry = signal.get("entry") or 0
        sl = signal.get("stop_loss") or 0
        tp = signal.get("take_profit1") or 0
        note = signal.get("tomorrow_prediction", "")

        # 推算回测预期盈亏%
        backtest_pct = None
        if entry > 0 and sl > 0 and tp > 0 and entry != sl:
            rr = abs(tp - entry) / abs(entry - sl)
            backtest_pct = round((rr * 0.5 - 0.5) * 100 / entry * abs(entry - sl), 2)

        row = write_signal(
            symbol=symbol,
            direction=direction,
            entry=entry,
            stop_loss=sl,
            take_profit=tp,
            backtest_expect_pct=backtest_pct,
            note=note,
        )
        if row > 0:
            logger.info("交易日志已写入: %s %s 第%s行", symbol, direction, row)
        else:
            logger.warning("交易日志写入失败: %s", symbol)
    except Exception as e:
        logger.warning("写入交易日志失败(非致命): %s", e)


# ══════════════════════════════════════════════════════════════
#  其他 /api/* 和 /backtest-reports/* 全部代理
# ══════════════════════════════════════════════════════════════

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    return _forward(request.method, "/api/config",
                    json_body=request.get_json(silent=True) if request.method == "POST" else None)


@app.route("/api/decision/<path:subpath>", methods=["GET", "POST"])
def api_decision(subpath: str):
    return _forward(request.method, f"/api/decision/{subpath}",
                    json_body=request.get_json(silent=True) if request.method == "POST" else None)


@app.route("/api/backtest/<path:subpath>", methods=["GET", "POST"])
def api_backtest(subpath: str):
    return _forward(request.method, f"/api/backtest/{subpath}",
                    json_body=request.get_json(silent=True) if request.method == "POST" else None)


@app.route("/api/export/<path:subpath>", methods=["GET"])
def api_export(subpath: str):
    return _forward("GET", f"/api/export/{subpath}")


@app.route("/backtest-reports/<path:filename>")
def backtest_reports(filename: str):
    return _forward("GET", f"/backtest-reports/{filename}")


# ══════════════════════════════════════════════════════════════
#  健康检查
# ══════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    try:
        resp = requests.get(f"{REMOTE}/api/config", timeout=5)
        remote_ok = resp.status_code == 200
    except Exception:
        remote_ok = False

    return jsonify({
        "local": "ok",
        "remote_server": REMOTE,
        "remote_connected": remote_ok,
    })


# ══════════════════════════════════════════════════════════════
#  启动
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════╗
║   OKX AI 本地面板 (代理模式)              ║
║                                          ║
║   新加坡服务器: {REMOTE:<28s}║
║   本地面板:     http://localhost:5001     ║
║                                          ║
║   流程:                                   ║
║   本地点「生成分析」→ 新加坡跑 AI/指标    ║
║   → 结果返回到本地页面 + 写入本地 Excel   ║
║                                          ║
║   本地点「回测」→ 新加坡跑回测管线        ║
║   → 结果返回到本地页面                    ║
╚══════════════════════════════════════════╝
    """)
    app.run(host="0.0.0.0", port=5001, debug=False)
