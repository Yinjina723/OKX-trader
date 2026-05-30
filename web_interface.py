"""
Web 监控界面 - Flask 服务器
提供 REST API 和实时仪表盘页面
"""
import json
import time
import threading
import logging
from typing import Optional
from datetime import datetime

import flask
from flask import Flask, render_template, jsonify, request

from shared_data import shared_store

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


# ==================== 页面路由 ====================

@app.route("/")
def index():
    """主仪表盘页面"""
    return render_template("index.html")


@app.route("/symbol/<inst_id>")
def symbol_detail(inst_id: str):
    """单个币种详情页"""
    state = shared_store.get_symbol_state(inst_id)
    if not state:
        return render_template("index.html")
    return render_template("detail.html", symbol=inst_id)


# ==================== API 接口 ====================

@app.route("/api/summary")
def api_summary():
    """获取系统总览"""
    return jsonify(shared_store.get_system_summary())


@app.route("/api/symbols")
def api_symbols():
    """获取所有币种状态"""
    return jsonify(shared_store.get_all_states())


@app.route("/api/symbol/<inst_id>")
def api_symbol_detail(inst_id: str):
    """获取单个币种详情"""
    state = shared_store.get_symbol_state(inst_id)
    if not state:
        return jsonify({"error": "Symbol not found"}), 404
    return jsonify(state)


@app.route("/api/events")
def api_events():
    """获取全局事件流"""
    count = int(request.args.get("count", 50))
    with shared_store._lock:
        events = list(shared_store._global_events)[-count:]
    return jsonify(events)


@app.route("/api/health")
def api_health():
    """健康检查"""
    return jsonify({
        "status": shared_store._system_status,
        "uptime": time.time() - shared_store._system_start_time,
        "timestamp": datetime.now().isoformat(),
    })


# ==================== 启动方法 ====================

def start_web_server(host: str = "0.0.0.0", port: int = 5000):
    """在新线程中启动 Flask Web 服务器"""
    logger.info(f"Web 服务器启动: http://{host}:{port}")
    # 使用 threading 运行 Flask
    t = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    t.start()
    return t


if __name__ == "__main__":
    # 独立测试时可以单独运行 Web 服务器
    logging.basicConfig(level=logging.INFO)
    app.run(host="0.0.0.0", port=5000, debug=True)
