"""
共享数据存储 - 线程安全的数据仓库
主程序和 Web 服务器通过此模块共享实时数据
"""
import time
import threading
from collections import deque
from typing import Optional, List, Dict
from datetime import datetime

from intent_engine import IntentResult, ManipulationPhase
from detectors import DetectionResult, BehaviorTag


class SymbolState:
    """单个币种的实时状态快照"""

    def __init__(self, inst_id: str):
        self.inst_id = inst_id
        self.current_phase: str = ManipulationPhase.UNKNOWN.display
        self.current_position: str = "未知"
        self.confidence: float = 0.0
        self.alert_level: str = "info"

        # 价格信息
        self.last_price: float = 0.0
        self.ask_price: float = 0.0
        self.bid_price: float = 0.0
        self.volume_24h: float = 0.0
        self.high_24h: float = 0.0
        self.low_24h: float = 0.0

        # 信号计数
        self.total_signals: int = 0
        self.danger_signals: int = 0

        # 最近事件
        self.recent_tags: List[str] = []
        self.recent_intents: deque = deque(maxlen=50)
        self.recent_detections: deque = deque(maxlen=100)
        self.phase_history: deque = deque(maxlen=20)

        # 运行时信息
        self.uptime_seconds: float = 0.0
        self.last_update: float = time.time()

    def to_dict(self) -> dict:
        return {
            "inst_id": self.inst_id,
            "current_phase": self.current_phase,
            "current_position": self.current_position,
            "phase_icon": PHASE_ICON_MAP.get(self.current_phase, "❓"),
            "phase_color_css": PHASE_CSS_COLOR_MAP.get(self.current_phase, "#888"),
            "confidence": self.confidence,
            "alert_level": self.alert_level,
            "last_price": self.last_price,
            "ask_price": self.ask_price,
            "bid_price": self.bid_price,
            "volume_24h": self.volume_24h,
            "high_24h": self.high_24h,
            "low_24h": self.low_24h,
            "total_signals": self.total_signals,
            "danger_signals": self.danger_signals,
            "recent_tags": self.recent_tags[-10:],
            "recent_intents": [i.to_dict() for i in list(self.recent_intents)[-20:]],
            "recent_detections": [d.to_dict() for d in list(self.recent_detections)[-50:]],
            "phase_history": list(self.phase_history),
        }


# 阶段性图标和颜色映射
PHASE_ICON_MAP = {
    "未知": "❓",
    "吸筹": "🟢",
    "洗盘": "🟡",
    "拉升": "🔵",
    "出货": "🔴",
    "盘整": "⚪",
}

PHASE_CSS_COLOR_MAP = {
    "未知": "#888",
    "吸筹": "#00c853",
    "洗盘": "#ffd600",
    "拉升": "#2979ff",
    "出货": "#ff1744",
    "盘整": "#9e9e9e",
}

ALERT_CSS_COLOR_MAP = {
    "info": "#888",
    "warning": "#ffd600",
    "danger": "#ff1744",
}


class SharedDataStore:
    """
    全局共享数据存储 - 线程安全
    主程序写入，Web 服务器读取
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._symbol_states: Dict[str, SymbolState] = {}
        self._global_events: deque = deque(maxlen=200)
        self._system_start_time = time.time()
        self._system_status = "starting"

    def get_or_create_symbol(self, inst_id: str) -> SymbolState:
        with self._lock:
            if inst_id not in self._symbol_states:
                self._symbol_states[inst_id] = SymbolState(inst_id)
            return self._symbol_states[inst_id]

    def update_symbol_state(self, inst_id: str, **kwargs):
        with self._lock:
            state = self.get_or_create_symbol(inst_id)
            for key, value in kwargs.items():
                if hasattr(state, key):
                    setattr(state, key, value)
            state.last_update = time.time()

    def add_detection(self, inst_id: str, detection: DetectionResult):
        with self._lock:
            state = self.get_or_create_symbol(inst_id)
            state.recent_detections.append(detection)

    def add_intent(self, inst_id: str, intent: IntentResult, from_phase: str = ""):
        with self._lock:
            state = self.get_or_create_symbol(inst_id)
            state.recent_intents.append(intent)
            state.total_signals += 1
            if intent.alert_level == "danger":
                state.danger_signals += 1

            # 记录阶段历史
            state.phase_history.append({
                "from_phase": from_phase or "未知",
                "to_phase": intent.phase.display,
                "reason": intent.description[:100],
                "ts": intent.timestamp,
            })

            # 全局事件
            self._global_events.append({
                "type": "intent",
                "inst_id": inst_id,
                "phase": intent.phase.display,
                "description": intent.description[:150],
                "alert_level": intent.alert_level,
                "timestamp": intent.timestamp,
            })

    def add_global_event(self, event: dict):
        with self._lock:
            self._global_events.append(event)

    def set_system_status(self, status: str):
        with self._lock:
            self._system_status = status

    def get_all_states(self) -> dict:
        with self._lock:
            return {k: v.to_dict() for k, v in self._symbol_states.items()}

    def get_symbol_state(self, inst_id: str) -> Optional[dict]:
        with self._lock:
            state = self._symbol_states.get(inst_id)
            return state.to_dict() if state else None

    def get_system_summary(self) -> dict:
        with self._lock:
            symbols = list(self._symbol_states.values())
            return {
                "status": self._system_status,
                "uptime": time.time() - self._system_start_time,
                "symbol_count": len(symbols),
                "total_signals": sum(s.total_signals for s in symbols),
                "total_dangers": sum(s.danger_signals for s in symbols),
                "recent_events": list(self._global_events)[-50:],
                "symbols": [{
                    "inst_id": s.inst_id,
                    "current_phase": s.current_phase,
                    "phase_icon": PHASE_ICON_MAP.get(s.current_phase, "❓"),
                    "phase_color": PHASE_CSS_COLOR_MAP.get(s.current_phase, "#888"),
                    "current_position": s.current_position,
                    "alert_level": s.alert_level,
                    "last_price": s.last_price,
                } for s in symbols],
            }


# 全局单例
shared_store = SharedDataStore()
