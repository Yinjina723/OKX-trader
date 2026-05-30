"""
庄家行为检测系统 - 主程序入口
=================
架构：数据采集 → 特征检测 → 意图推断 → 信号输出 + Web 仪表盘

启动方式：
    python mian.py
    然后浏览器打开 http://localhost:5000
"""
import os
import sys
import time
import signal
import logging
import threading
from typing import Dict, List

from detector_config import SystemConfig, default_config
from data_collector import DataCollector
from detectors import DetectorManager, DetectionResult
from intent_engine import IntentInferenceEngine, IntentResult, ManipulationPhase
from signal_output import SignalOutput, Colors
from shared_data import shared_store

# ==================== 日志配置 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("monitor.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("Main")


# ==================== 单币种 Pipeline ====================

class SymbolPipeline:
    """
    单币种完整流水线
    数据采集 → 检测器 → 意图引擎 → 信号输出
    """

    def __init__(self, symbol_config, okx_config, detector_config, system_config):
        self.inst_id = symbol_config.inst_id

        # 数据采集层
        self.collector = DataCollector(symbol_config, okx_config, detector_config)

        # 检测器管理器
        self.detector_manager = DetectorManager(symbol_config, self.collector, detector_config)

        # 意图推断引擎
        self.intent_engine = IntentInferenceEngine(self.inst_id)

        # 信号输出
        self.signal_output = SignalOutput(self.inst_id)

        # 追踪上一个阶段（用于记录转换历史）
        self._prev_phase = "未知"

        # 初始化共享存储中的 symbol
        shared_store.get_or_create_symbol(self.inst_id)

        # 注册回调链
        self._wire_callbacks()

        # 状态摘要定时器
        self._summary_timer: threading.Timer = None

    def _wire_callbacks(self):
        """串联回调链：检测器 → 意图引擎 → 信号输出 + 共享存储"""

        def on_detection(detection: DetectionResult):
            # 存入共享存储（供 Web 查看）
            shared_store.add_detection(self.inst_id, detection)

            # 轻量终端输出
            self.signal_output.display_detection(detection)

            # 送入意图引擎
            intent = self.intent_engine.process_detection(detection)
            if intent:
                self._on_intent_complete(intent)

        self.detector_manager.set_callback(on_detection)

    def _on_intent_complete(self, intent: IntentResult):
        """意图确认后的完整处理"""
        # 存入共享存储（附带阶段转换信息）
        shared_store.add_intent(self.inst_id, intent, from_phase=self._prev_phase)
        self._prev_phase = intent.phase.display

        # 更新 symbol 状态到共享存储
        shared_store.update_symbol_state(
            self.inst_id,
            current_phase=intent.phase.display,
            confidence=intent.confidence,
            alert_level=intent.alert_level,
        )

        # 终端彩色输出
        self.signal_output.display_intent(intent)

    def _update_ticker_info(self):
        """定期更新 ticker 信息到共享存储"""
        ticker = self.collector.get_latest_ticker()
        if ticker:
            shared_store.update_symbol_state(
                self.inst_id,
                last_price=ticker.last,
                ask_price=ticker.ask_px,
                bid_price=ticker.bid_px,
                volume_24h=ticker.vol_24h,
                high_24h=ticker.high_24h,
                low_24h=ticker.low_24h,
            )

    def _update_position_info(self):
        """更新位置信息到共享存储"""
        pos = self.detector_manager.position_detector.current_position
        if pos:
            shared_store.update_symbol_state(
                self.inst_id,
                current_position=pos.value,
            )

    def _print_status_summary(self):
        """定期打印状态摘要到终端"""
        status = self.intent_engine.get_status()
        self.signal_output.show_status_summary(status)

        # 更新 ticker 和位置
        self._update_ticker_info()
        self._update_position_info()

        # 重新设置定时器
        self._summary_timer = threading.Timer(5.0, self._print_status_summary)
        self._summary_timer.daemon = True
        self._summary_timer.start()

    def start(self):
        """启动流水线"""
        logger.info(f"{'='*50}")
        logger.info(f"[{self.inst_id}] 启动监控流水线")
        logger.info(f"{'='*50}")

        # 1. 启动数据采集（包含加载历史数据）
        self.collector.start()

        # 2. 等待数据就绪
        logger.info(f"[{self.inst_id}] 等待数据就绪...")
        if not self.collector.wait_ready(timeout=30.0):
            logger.error(f"[{self.inst_id}] 数据初始化超时，将继续运行但数据可能不完整")

        # 3. 启动检测器（定期扫描）
        self.detector_manager.start()

        # 4. 启动状态摘要打印
        self._summary_timer = threading.Timer(5.0, self._print_status_summary)
        self._summary_timer.daemon = True
        self._summary_timer.start()

        logger.info(f"[{self.inst_id}] 流水线启动完成 ✓")

    def stop(self):
        """停止流水线"""
        logger.info(f"[{self.inst_id}] 停止监控...")
        if self._summary_timer:
            self._summary_timer.cancel()
        self.detector_manager.stop()
        self.collector.stop()
        logger.info(f"[{self.inst_id}] 已停止")


# ==================== 主程序 ====================

class MonitorApp:
    """主监控应用 - 管理所有币种的流水线"""

    def __init__(self, config: SystemConfig = None):
        self.config = config or default_config
        self.pipelines: Dict[str, SymbolPipeline] = {}
        self._running = False

    def start_all(self):
        """启动所有币种监控"""
        self._running = True
        shared_store.set_system_status("running")

        symbols = self.config.symbols
        logger.info(f"\n{'#'*60}")
        logger.info(f"#  庄家行为检测系统  (WebSocket 实盘)")
        logger.info(f"#  监控币种: {', '.join(s.inst_id for s in symbols)}")
        logger.info(f"#  检测间隔: {self.config.detector.detection_interval}s")
        logger.info(f"#  信号冷却: {self.config.detector.signal_cooldown_sec}s")
        logger.info(f"{'#'*60}\n")

        for sym_cfg in symbols:
            pipeline = SymbolPipeline(
                sym_cfg,
                self.config.okx,
                self.config.detector,
                self.config,
            )
            self.pipelines[sym_cfg.inst_id] = pipeline
            pipeline.start()

        # 启动 Web 服务器
        self._start_web()

        logger.info(f"\n{'='*60}")
        logger.info(f"  所有监控已启动！")
        logger.info(f"  Web 仪表盘: http://localhost:5000")
        logger.info(f"  按 Ctrl+C 停止")
        logger.info(f"{'='*60}\n")

        shared_store.add_global_event({
            "type": "system",
            "inst_id": "SYSTEM",
            "phase": "启动",
            "description": f"系统启动，监控 {len(symbols)} 个币种",
            "alert_level": "info",
            "timestamp": time.time(),
        })

    def _start_web(self):
        """启动 Web 服务器"""
        try:
            from web_interface import start_web_server
            start_web_server(host="0.0.0.0", port=5000)
            logger.info("Web 服务器已启动: http://localhost:5000")
        except Exception as e:
            logger.error(f"Web 服务器启动失败: {e}")

    def stop_all(self):
        """停止所有监控"""
        logger.info("正在停止所有监控...")
        self._running = False
        shared_store.set_system_status("stopped")

        for inst_id, pipeline in self.pipelines.items():
            try:
                pipeline.stop()
            except Exception as e:
                logger.error(f"停止 {inst_id} 时异常: {e}")

        shared_store.add_global_event({
            "type": "system",
            "inst_id": "SYSTEM",
            "phase": "停止",
            "description": "系统已停止",
            "alert_level": "info",
            "timestamp": time.time(),
        })
        logger.info("所有监控已停止")

    def run_forever(self):
        """主循环 - 保持程序运行"""
        self.start_all()

        # 注册信号处理
        def signal_handler(sig, frame):
            logger.info(f"\n收到信号 {sig}，正在退出...")
            self.stop_all()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # 保持运行
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_all()


# ==================== 入口 ====================

def main():
    """主入口"""
    config = SystemConfig()

    # === API Key（公有行情不需要，仅私有频道/REST 下单需要） ===
    # config.okx.api_key = "your-api-key"
    # config.okx.secret_key = "your-secret-key"
    # config.okx.passphrase = "your-passphrase"

    # 自定义币种：
    # from config import SymbolConfig
    # config.symbols = [
    #     SymbolConfig(inst_id="BTC-USDT-SWAP", inst_type="SWAP"),
    #     SymbolConfig(inst_id="ETH-USDT-SWAP", inst_type="SWAP"),
    # ]

    app = MonitorApp(config)
    app.run_forever()


if __name__ == "__main__":
    main()
