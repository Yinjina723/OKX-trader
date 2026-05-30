# web_app.py
"""
Web 面板：Flask 应用，提供配置页、生成信号、回测等页面与 API。

- 首页 /：配置表单、生成信号按钮、回测按钮、K 线图与说明展示
- API：/api/config、/api/generate_signal、/api/backtest 等
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict

from flask import Flask, jsonify, request, render_template

from config import Config

logger = logging.getLogger(__name__)
from main import prepare_data_and_get_signal
from grid_manager import GridManager
from okx_client import OKXClient
from backtest import run_backtest


app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"))


def load_config() -> Config:
    """统一从 config.json 加载配置。"""
    return Config("config.json")


def _ensure_logging():
    """确保根 logger 已配置为写入文件，避免 flask run / gunicorn 启动时日志不落盘。"""
    root = logging.getLogger()
    if not any(h for h in root.handlers if getattr(h, "baseFilename", None)):
        try:
            from logger import setup_logger
            setup_logger(load_config())
        except Exception:
            pass




_ensure_logging()


@app.get("/")
def index() -> str:
    """现代化暗色主题首页，加载外部 HTML 模板。"""
    cfg = load_config()
    return render_template(
        "panel.html",
        symbols=cfg.SYMBOLS,
        tf=cfg.TARGET_TIMEFRAME,
        interval=cfg.INTERVAL_MINUTES,
    )


@app.get("/api/config")
def api_get_config() -> Any:
    """
    返回完整的 config.json 内容，附带一个规范化的 SYMBOLS 字段，方便前端展示。
    """
    cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # 兼容 SYMBOL / SYMBOLS 两种写法，统一在返回中提供 SYMBOLS 列表
    symbols = raw.get("SYMBOLS")
    if not symbols and "SYMBOL" in raw:
        symbols = raw["SYMBOL"]
    if symbols is not None:
        raw["SYMBOLS"] = symbols

    return jsonify(raw)


@app.post("/api/config")
def api_update_config() -> Any:
    """更新部分关键配置（周期、网格、回测等）。"""
    payload: Dict[str, Any] = request.get_json(silent=True) or {}

    # 读取原始 config.json
    cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # 简单更新允许的字段
    symbols_str = payload.get("SYMBOLS")
    if isinstance(symbols_str, str):
        # 逗号分隔转列表，并去掉空白
        symbols = [s.strip() for s in symbols_str.split(",") if s.strip()]
        if symbols:
            raw["SYMBOL"] = symbols

    for key in [
        "TARGET_TIMEFRAME",
        "INTERVAL_MINUTES",
        "DAYS",
        "LOOKBACK",
        "GRID_DEFAULT_RANGE_PERCENT",
        "GRID_DEFAULT_COUNT",
        "TICK_SIZE",
        "FEE_RATE",
        "SLIPPAGE",
        "ENABLE_PREMIUM",
        "ENABLE_MARK_CANDLES",
        "ENABLE_INDEX_CANDLES",
        "ADVANCED_INDICATORS",
        "LONG_TERM_TIMEFRAME",
    ]:
        if key in payload and payload[key] is not None:
            raw[key] = payload[key]

    # 规则策略开关
    if "RULE_STRATEGY_ENABLED" in payload:
        raw.setdefault("RULE_STRATEGY", {})
        raw["RULE_STRATEGY"]["ENABLED"] = bool(payload["RULE_STRATEGY_ENABLED"])

    bt = payload.get("BACKTEST") or {}
    if bt:
        raw.setdefault("BACKTEST", {})
        if "ENABLED" in bt:
            raw["BACKTEST"]["ENABLED"] = bool(bt["ENABLED"])
        if "INITIAL_CAPITAL" in bt and bt["INITIAL_CAPITAL"] is not None:
            raw["BACKTEST"]["INITIAL_CAPITAL"] = bt["INITIAL_CAPITAL"]

    # 🆕 AI 增强
    for key in ["AI_ENSEMBLE_ENABLED", "AI_ECO_MODE"]:
        if key in payload and payload[key] is not None:
            raw[key] = bool(payload[key])
    if "AI_ECO_CONSENSUS_THRESHOLD" in payload and payload["AI_ECO_CONSENSUS_THRESHOLD"] is not None:
        raw["AI_ECO_CONSENSUS_THRESHOLD"] = int(payload["AI_ECO_CONSENSUS_THRESHOLD"])
    if "AI_TEMPERATURE" in payload and payload["AI_TEMPERATURE"] is not None:
        raw["AI_TEMPERATURE"] = float(payload["AI_TEMPERATURE"])

    # 🆕 分析模块
    for key in ["MTF_CONFLUENCE_ENABLED", "TECHNICAL_BATCH_ENABLED",
                "MANIPULATION_ENABLED", "SENTIMENT_ENABLED"]:
        if key in payload and payload[key] is not None:
            raw[key] = bool(payload[key])

    # 🆕 波动率自适应 + 多周期参数
    if "VOLATILITY_ADAPTIVE_THRESHOLD" in payload and payload["VOLATILITY_ADAPTIVE_THRESHOLD"] is not None:
        raw["VOLATILITY_ADAPTIVE_THRESHOLD"] = bool(payload["VOLATILITY_ADAPTIVE_THRESHOLD"])
    for key in ["MTF_TREND_TIMEFRAME", "MTF_STRUCTURE_TIMEFRAME"]:
        if key in payload and payload[key] is not None:
            raw[key] = str(payload[key])

    # 🆕 数据源维度
    dims = payload.get("ENABLE_NEW_DIMENSIONS") or {}
    if dims:
        raw.setdefault("ENABLE_NEW_DIMENSIONS", {})
        for dim_key in ["FUNDING_RATE_HISTORY", "INDEX_TICKERS_BASIS",
                         "ELITE_POSITION_RATIO", "OPTION_MAX_PAIN",
                         "OPTION_PUT_CALL_RATIO", "POSITION_TIERS_ANALYSIS",
                         "ELITE_TREND_MULTI_TF"]:
            if dim_key in dims:
                raw["ENABLE_NEW_DIMENSIONS"][dim_key] = bool(dims[dim_key])

    # 🆕 高级参数
    for key in ["OI_LIMIT", "BIGDATA_LIMIT"]:
        if key in payload and payload[key] is not None:
            raw[key] = int(payload[key])
    for key in ["PROXY_URL", "SITE"]:
        if key in payload and payload[key] is not None:
            raw[key] = str(payload[key])

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=4)

    return jsonify({"message": "配置已保存"})

@app.post("/api/generate_signal")
def api_generate_signal() -> Any:
    """为指定 symbol 生成一次信号和网格建议，并返回结果."""
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol")
    if not symbol:
        logger.warning("生成信号: 请求缺少 symbol")
        return jsonify({"error": "symbol 必填"}), 400

    cfg = load_config()
    if symbol not in cfg.SYMBOLS:
        logger.warning("生成信号: symbol=%s 不在 SYMBOLS 中", symbol)
        return jsonify({"error": f"symbol {symbol} 不在配置的 SYMBOLS 中"}), 400

    logger.info("生成信号: 开始 symbol=%s", symbol)
    try:
        client = OKXClient(cfg)
        grid_manager = GridManager(
            config=cfg,
            default_range_percent=getattr(cfg, "GRID_DEFAULT_RANGE_PERCENT", 0.2),
            default_grid_count=getattr(cfg, "GRID_DEFAULT_COUNT", 10),
        )

        # 先获取当前价格
        ticker = client.get_ticker(symbol)
        current_price = float(ticker.get("last", 0)) if ticker else 0

        signal = prepare_data_and_get_signal(cfg, client, symbol)
        grid_text = ""
        if signal and signal.get("direction") != "neutral":
            if current_price > 0:
                grid_text = grid_manager.get_grid_suggestions(current_price, signal)

        # 获取一段简单 K 线数据用于前端画图（同时提取插针事件）
        kline_data = []
        wick_events = []
        try:
            tf = cfg.TARGET_TIMEFRAME.replace("min", "m")
            candles = client.get_klines(symbol, tf, limit=120)
            for row in reversed(candles):  # API 返回最新在前，这里翻转为时间正序
                ts_ms, o, h, l, c = int(row[0]), row[1], row[2], row[3], row[4]
                ts_label = datetime.fromtimestamp(ts_ms / 1000).strftime("%m-%d %H:%M")
                kline_data.append(
                    {
                        "time": ts_label,
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                    }
                )
        except Exception:
            kline_data = []
        
        # 🆕 从操盘分析结果中提取插针事件
        if signal:
            manipulation = signal.get("manipulation", {})
            if manipulation:
                phase = manipulation.get("phase_result", {})
                all_signals = phase.get("signals", [])
                # 过滤出插针相关信号
                wick_keywords = ["插针", "洗盘", "诱多"]
                for s in all_signals:
                    if any(kw in s for kw in wick_keywords):
                        # 推断插针方向
                        wick_type = "down"  # 默认向下插针
                        wick_cn = "洗盘震仓"
                        if "诱多" in s or "向上" in s:
                            wick_type = "up"
                            wick_cn = "诱多出货"
                        wick_events.append({
                            "type": wick_type,
                            "type_cn": wick_cn,
                            "detail": s,
                        })

        # 生成中文说明
        summary_cn = "当前无明确交易信号。"
        if signal:
            direction = signal.get("direction", "neutral")
            entry = signal.get("entry")
            sl = signal.get("stop_loss")
            tp1 = signal.get("take_profit1")
            tp2 = signal.get("take_profit2")
            strength = signal.get("strength", "medium")
            market_state = signal.get("market_state") or "未知"

            dir_cn = {"long": "做多", "short": "做空", "neutral": "观望"}.get(direction, "观望")
            strength_cn = {"strong": "强", "medium": "中等", "weak": "较弱"}.get(strength, "中等")

            # 关键支撑位：优先使用 AI 返回的 key_support，没有再退化为止损价
            support = None
            key_support = signal.get("key_support")
            try:
                if key_support is not None:
                    support = float(key_support)
            except (TypeError, ValueError):
                support = None
            if support is None and sl:
                support = sl

            if direction == "neutral":
                if support is not None:
                    summary_cn = (
                        f"{symbol}：AI 建议观望，当前无明显优势方向。"
                        f"关键支撑位约 {support:.6f}，市场状态：{market_state}。"
                    )
                else:
                    summary_cn = (
                        f"{symbol}：AI 建议观望，当前无明显优势方向。"
                        f"市场状态：{market_state}。"
                    )
            else:
                support_text = f"关键支撑位约 {support:.6f}，" if support is not None else ""
                summary_cn = (
                    f"{symbol}：AI 建议{dir_cn}。入场价约 {entry:.6f}，止损 {sl:.6f}，"
                    f"第一目标价 {tp1:.6f}，第二目标价 {tp2:.6f}。"
                    f"{support_text}信号强度：{strength_cn}，市场状态：{market_state}。"
                )

            # 🆕 追加操盘分析摘要
            manipulation = signal.get("manipulation") if signal else None
            if manipulation:
                phase = manipulation.get("phase_result", {})
                next_mv = manipulation.get("next_move", {})
                predict = manipulation.get("predicted_point", {})
                manip_lines = ["\n——— 🎯 操盘分析 ———"]
                if phase.get("phase_cn"):
                    manip_lines.append(f"当前阶段：{phase['phase_cn']} (评分:{phase.get('score',0)} 置信:{phase.get('confidence',0):.0%})")
                if next_mv.get("next_action"):
                    dir_cn2 = {"long":"做多(升)","short":"做空(降)","neutral":"观望"}.get(next_mv.get("direction",""),"")
                    manip_lines.append(f"庄家下一步：{next_mv['next_action']} → {dir_cn2}")
                    if next_mv.get("target_price"):
                        manip_lines.append(f"庄家目标价：{next_mv['target_price']:.4f} | 底线：{next_mv['stop_price']:.4f}")
                    if next_mv.get("time_frame"):
                        manip_lines.append(f"时间窗：{next_mv['time_frame']}")
                if predict.get("ensemble_target"):
                    pdir = direction == "long" and "做多" or (direction == "short" and "做空" or "观望")
                    manip_lines.append(f"综合预测点位：{predict['ensemble_target']:.4f} (置信:{predict.get('confidence',0):.0%})")
                if manip_lines:
                    summary_cn += "\n".join(manip_lines)

            # 🆕 追加多周期共振摘要
            confluence = signal.get("confluence_detail") if signal else None
            if confluence:
                summary_cn += f"\n——— 📈 多周期共振 ———\n{confluence}"

            logger.info("生成信号: 完成 symbol=%s direction=%s strength=%s", symbol, direction, strength)
        else:
            logger.info("生成信号: 完成 symbol=%s 无信号", symbol)

        return jsonify(
            {
                "symbol": symbol,
                "current_price": current_price,
                "summary_cn": summary_cn,
                "signal": signal or {},
                "grid_suggestions": grid_text,
                "kline": kline_data,
                "wick_events": wick_events,
            }
        )
    except Exception as e:
        logger.exception("生成信号: symbol=%s 失败: %s", symbol, e)
        return jsonify({"error": str(e)}), 500


@app.post("/api/backtest")
def api_backtest() -> Any:
    """对指定 symbol 运行一次回测，并返回绩效指标。"""
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol")
    if not symbol:
        logger.warning("回测: 请求缺少 symbol")
        return jsonify({"error": "symbol 必填"}), 400

    cfg = load_config()
    if symbol not in cfg.SYMBOLS:
        logger.warning("回测: symbol=%s 不在 SYMBOLS 中", symbol)
        return jsonify({"error": f"symbol {symbol} 不在配置的 SYMBOLS 中"}), 400

    logger.info("回测: 开始 symbol=%s", symbol)
    try:
        metrics = run_backtest(cfg, symbol)
    except Exception as e:
        logger.exception("回测: symbol=%s 执行失败: %s", symbol, e)
        return jsonify({"error": str(e)}), 500

    if not metrics:
        logger.warning("回测: symbol=%s 无结果（可能没有历史信号）", symbol)
        return jsonify({"error": "回测没有结果（可能没有历史信号）"}), 400

    total = float(metrics.get("total_return", 0.0))
    mdd = float(metrics.get("max_drawdown", 0.0))
    sharpe = float(metrics.get("sharpe_ratio", 0.0))
    equity = float(metrics.get("final_equity", 0.0))

    pnl_desc = "基本持平"
    if total > 0.01:
        pnl_desc = "整体盈利"
    elif total < -0.01:
        pnl_desc = "整体亏损"

    risk_desc = ""
    if mdd < -0.5:
        risk_desc = "，回撤非常大，风险较高"
    elif mdd < -0.3:
        risk_desc = "，回撤偏大，需要控制风险"
    elif mdd < -0.15:
        risk_desc = "，回撤在可接受范围内"
    else:
        risk_desc = "，回撤较小"

    summary_cn = (
        f"{symbol} 回测：总收益率 {total*100:.2f}%（{pnl_desc}），"
        f"最大回撤 {mdd*100:.2f}%{risk_desc}\n"
        f"夏普比率 {sharpe:.2f}，最终权益约 {equity:.2f}。"
    )
    logger.info(
        "回测: 完成 symbol=%s 收益率=%.2f%% 最大回撤=%.2f%% 夏普=%.2f",
        symbol, total * 100, mdd * 100, sharpe,
    )

    # 确保 metrics 可被 JSON 序列化（例如去掉 numpy 类型等）
    try:
        json.dumps(metrics)
        safe_metrics = metrics
    except TypeError:
        try:
            safe_metrics = json.loads(json.dumps(metrics, default=str))
        except Exception:
            safe_metrics = {}

    return jsonify({"symbol": symbol, "summary_cn": summary_cn, "metrics": safe_metrics})








if __name__ == "__main__":
    # 默认监听本机 5000 端口：python web_app.py（日志已在模块导入时通过 _ensure_logging 配置）
    app.run(host="0.0.0.0", port=5000, debug=True)    #正式环境删除debug=True

