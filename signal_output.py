"""
信号输出层 - 将意图推断结果可视化呈现
输出：状态灯 + 推演文字 + 告警推送
"""
import time
import logging
import threading
from typing import Optional, Dict
from datetime import datetime

from intent_engine import IntentResult, ManipulationPhase

logger = logging.getLogger(__name__)


# ANSI 颜色码
class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    GRAY = "\033[90m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"
    CYAN = "\033[96m"

    # 背景色
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"


PHASE_COLORS = {
    ManipulationPhase.UNKNOWN: Colors.GRAY,
    ManipulationPhase.ACCUMULATION: Colors.GREEN,
    ManipulationPhase.SHAKEOUT: Colors.YELLOW,
    ManipulationPhase.PUMP: Colors.BLUE,
    ManipulationPhase.DISTRIBUTION: Colors.RED,
    ManipulationPhase.CONSOLIDATION: Colors.GRAY,
}

PHASE_ICONS = {
    ManipulationPhase.UNKNOWN: "❓",
    ManipulationPhase.ACCUMULATION: "🟢",
    ManipulationPhase.SHAKEOUT: "🟡",
    ManipulationPhase.PUMP: "🔵",
    ManipulationPhase.DISTRIBUTION: "🔴",
    ManipulationPhase.CONSOLIDATION: "⚪",
}

ALERT_COLORS = {
    "info": Colors.WHITE,
    "warning": Colors.YELLOW,
    "danger": Colors.RED,
}


class SignalOutput:
    """
    信号输出层
    - 终端彩色输出
    - 信号日志记录
    - 支持自定义 Webhook/推送扩展
    """

    def __init__(self, inst_id: str):
        self.inst_id = inst_id
        self._last_intent: Optional[IntentResult] = None
        self._total_signals = 0
        self._danger_signals = 0
        self._lock = threading.Lock()
        self._webhook_url: Optional[str] = None
        self._start_time = time.time()

    def set_webhook(self, url: str):
        """设置 Webhook 推送地址"""
        self._webhook_url = url

    def display_intent(self, intent: IntentResult):
        """显示意图推断结果（终端彩色输出 + 日志）"""
        with self._lock:
            self._last_intent = intent
            self._total_signals += 1
            if intent.alert_level == "danger":
                self._danger_signals += 1

        phase = intent.phase
        color = PHASE_COLORS.get(phase, Colors.WHITE)
        icon = PHASE_ICONS.get(phase, "?")
        alert_color = ALERT_COLORS.get(intent.alert_level, Colors.WHITE)

        timestamp = datetime.fromtimestamp(intent.timestamp).strftime("%H:%M:%S")

        # 构建输出
        lines = []
        lines.append("")
        lines.append(f"{'=' * 60}")
        lines.append(f"{icon} {color}{Colors.BOLD}[{intent.symbol}] 庄家阶段: {phase.display}{Colors.RESET}")
        lines.append(f"{'=' * 60}")

        # 状态灯
        status_bar = self._build_status_bar(phase)
        lines.append(status_bar)

        # 推演描述
        lines.append(f"\n{Colors.CYAN}🧠 意图推演:{Colors.RESET}")
        lines.append(f"  {intent.description}")

        # 操作建议
        if intent.suggestion:
            lines.append(f"\n{Colors.CYAN}💡 操作建议:{Colors.RESET}")
            if intent.alert_level == "danger":
                lines.append(f"  {Colors.RED}{Colors.BOLD}{intent.suggestion}{Colors.RESET}")
            else:
                lines.append(f"  {intent.suggestion}")

        # 置信度
        conf_bar = "█" * int(intent.confidence * 20) + "░" * (20 - int(intent.confidence * 20))
        lines.append(f"\n📊 置信度: [{alert_color}{conf_bar}{Colors.RESET}] {intent.confidence:.0%}")

        # 证据链
        if intent.evidence:
            lines.append(f"\n📋 支撑证据:")
            for ev in intent.evidence[-5:]:  # 最多显示5条
                ev_time = datetime.fromtimestamp(ev.timestamp).strftime("%H:%M:%S")
                lines.append(f"  [{ev_time}] {ev.tag.value}: {ev.detail[:80]}")

        lines.append(f"\n{'=' * 60}\n")

        output = "\n".join(lines)

        # 终端输出
        print(output)

        # 日志记录
        if intent.alert_level == "danger":
            logger.warning(f"🚨 [{intent.symbol}] {phase.display}: {intent.description}")
        else:
            logger.info(f"[{intent.symbol}] {phase.display}: {intent.description}")

        # Webhook 推送
        if self._webhook_url and intent.alert_level in ("warning", "danger"):
            self._send_webhook(intent)

    def display_detection(self, detection):
        """显示单条检测结果（轻量输出）"""
        tag_color = {
            "低位区": Colors.GREEN,
            "中位区": Colors.WHITE,
            "高位区": Colors.RED,
            "地量": Colors.GRAY,
            "倍量": Colors.YELLOW,
            "天量": Colors.RED,
            "疑似对倒": Colors.YELLOW,
            "夹板吸筹": Colors.GREEN,
            "虚假压单": Colors.YELLOW,
            "隐蔽吸筹(拆单)": Colors.GREEN,
            "向下试盘": Colors.YELLOW,
            "洗盘插针": Colors.YELLOW,
            "高位派发": Colors.RED,
            "大单主动出货": Colors.RED,
        }.get(detection.tag.value, Colors.WHITE)

        ts = datetime.fromtimestamp(detection.timestamp).strftime("%H:%M:%S")
        print(f"  [{ts}] {tag_color}[{detection.tag.value}]{Colors.RESET} "
              f"{detection.detail[:70]}")

    def _build_status_bar(self, phase: ManipulationPhase) -> str:
        """构建可视化状态条"""
        phases_order = [
            ManipulationPhase.UNKNOWN,
            ManipulationPhase.ACCUMULATION,
            ManipulationPhase.SHAKEOUT,
            ManipulationPhase.PUMP,
            ManipulationPhase.DISTRIBUTION,
        ]

        segments = []
        current_idx = phases_order.index(phase) if phase in phases_order else 0

        for i, p in enumerate(phases_order):
            color = PHASE_COLORS.get(p, Colors.WHITE)
            icon = PHASE_ICONS.get(p, "?")
            if i <= current_idx:
                segments.append(f"{color}{Colors.BOLD}{icon}{p.display}{Colors.RESET}")
            else:
                segments.append(f"{Colors.GRAY}{icon}{p.display}{Colors.RESET}")

        return " → ".join(segments)

    def show_status_summary(self, engine_status: dict):
        """定期打印状态摘要"""
        phase = engine_status["current_phase"]
        position = engine_status["current_position"]
        pending = engine_status["pending_phase"]
        count = engine_status["pending_count"]

        color = PHASE_COLORS.get(
            next((p for p in ManipulationPhase if p.display == phase), ManipulationPhase.UNKNOWN),
            Colors.WHITE
        )

        runtime = time.time() - self._start_time
        hours = int(runtime // 3600)
        minutes = int((runtime % 3600) // 60)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(f"\r[{ts}] 运行 {hours}h{minutes}m | "
              f"位置: {position} | "
              f"阶段: {color}{phase}{Colors.RESET} | "
              f"信号: {self._total_signals} | "
              f"告警: {self._danger_signals}",
              end="", flush=True)

    def _send_webhook(self, intent: IntentResult):
        """发送 Webhook（预留接口）"""
        try:
            import requests
            if not self._webhook_url:
                return
            payload = {
                "symbol": intent.symbol,
                "phase": intent.phase.display,
                "description": intent.description,
                "confidence": intent.confidence,
                "alert_level": intent.alert_level,
                "suggestion": intent.suggestion,
                "timestamp": intent.timestamp,
            }
            requests.post(self._webhook_url, json=payload, timeout=5)
        except Exception as e:
            logger.warning(f"Webhook 发送失败: {e}")
