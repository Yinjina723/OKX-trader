"""
意图推断引擎 - 状态机 + 规则引擎
吃行为标签和位置状态，输出对庄家心态的判断。

状态流转：
  吸筹(ACCUMULATION) → 洗盘(SHAKEOUT) → 拉升(PUMP) → 出货(DISTRIBUTION)

关键设计：
- 状态有惯性，不会瞬间跳变，通过连续确认防噪音
- "位置"作为首要过滤条件
"""
import time
import logging
from collections import deque
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from enum import Enum

from detectors import BehaviorTag, DetectionResult, PositionDetector

logger = logging.getLogger(__name__)


# ==================== 庄家阶段状态 ====================

class ManipulationPhase(Enum):
    """庄家操纵阶段"""
    UNKNOWN = ("未知", "white")
    ACCUMULATION = ("吸筹", "green")       # 低位收集筹码
    SHAKEOUT = ("洗盘", "yellow")           # 清洗浮筹
    PUMP = ("拉升", "blue")                 # 拉离成本区
    DISTRIBUTION = ("出货", "red")          # 高位派发
    CONSOLIDATION = ("盘整", "gray")        # 横盘整理

    @property
    def display(self) -> str:
        """返回中文显示名"""
        return self.value[0]

    @property
    def color(self) -> str:
        """返回对应颜色"""
        return self.value[1]


@dataclass
class PhaseTransition:
    """状态转换记录"""
    from_phase: ManipulationPhase
    to_phase: ManipulationPhase
    reason: str
    timestamp: float
    confidence: float


@dataclass
class IntentResult:
    """意图推断结果"""
    phase: ManipulationPhase
    symbol: str
    timestamp: float
    confidence: float
    description: str                     # 推演出的庄家心态
    evidence: List[DetectionResult] = field(default_factory=list)  # 支撑证据
    alert_level: str = "info"            # info / warning / danger
    suggestion: str = ""                 # 操作建议

    def to_dict(self) -> dict:
        return {
            "phase": self.phase.display,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "description": self.description,
            "evidence": [e.to_dict() for e in self.evidence[-10:]],
            "alert_level": self.alert_level,
            "suggestion": self.suggestion,
        }


# ==================== 意图推断引擎 ====================

class IntentInferenceEngine:
    """
    核心：状态机 + 规则引擎
    - 吃行为标签流
    - 根据当前位置和已有状态推断庄家意图
    """

    def __init__(self, inst_id: str):
        self.inst_id = inst_id

        # 当前状态
        self.current_phase: ManipulationPhase = ManipulationPhase.UNKNOWN
        self.current_position: Optional[BehaviorTag] = None

        # 历史
        self._phase_history: List[PhaseTransition] = []
        self._recent_tags: deque = deque(maxlen=50)  # 最近的行为标签
        self._evidence_buffer: deque = deque(maxlen=20)  # 证据缓冲区

        # 状态确认机制：防止噪音
        self._pending_phase: Optional[ManipulationPhase] = None
        self._pending_count: int = 0
        self._confirm_threshold: int = 3  # 需要连续确认的次数

        # 冷却：同一信号类型的最小间隔
        self._last_alert_time: Dict[str, float] = {}

        # 回调
        self._on_intent_change: Optional[callable] = None

    def set_callback(self, callback: callable):
        """设置意图变化的回调"""
        self._on_intent_change = callback

    def process_detection(self, result: DetectionResult):
        """
        处理一条检测结果，更新状态机。

        参数:
            result: 检测器输出的一条行为标签
        返回:
            IntentResult 或 None（如果不需要输出）
        """
        tag = result.tag
        self._recent_tags.append(result)

        # 更新位置信息
        if tag in (BehaviorTag.POSITION_LOW, BehaviorTag.POSITION_MID,
                    BehaviorTag.POSITION_HIGH):
            self.current_position = tag
            # 位置变化也加入证据
            self._evidence_buffer.append(result)

        # 执行规则引擎
        intent = self._evaluate_rules(result)

        # 意图确认机制（防噪音）
        if intent:
            if intent.phase == self._pending_phase:
                self._pending_count += 1
                self._evidence_buffer.append(result)
            else:
                self._pending_phase = intent.phase
                self._pending_count = 1
                self._evidence_buffer.clear()
                self._evidence_buffer.append(result)

            # 达到确认阈值 → 触发状态变更
            if self._pending_count >= self._confirm_threshold:
                old_phase = self.current_phase
                self.current_phase = self._pending_phase

                # 记录历史
                transition = PhaseTransition(
                    from_phase=old_phase,
                    to_phase=self._pending_phase,
                    reason=intent.description,
                    timestamp=time.time(),
                    confidence=intent.confidence,
                )
                self._phase_history.append(transition)

                # 构建正式输出
                final_intent = IntentResult(
                    phase=self._pending_phase,
                    symbol=self.inst_id,
                    timestamp=time.time(),
                    confidence=intent.confidence,
                    description=intent.description,
                    evidence=list(self._evidence_buffer),
                    alert_level=intent.alert_level,
                    suggestion=self._get_suggestion(self._pending_phase),
                )

                # 冷却检查
                now = time.time()
                phase_key = self._pending_phase.display
                last = self._last_alert_time.get(phase_key, 0)
                if now - last > 30:  # 30秒冷却
                    self._last_alert_time[phase_key] = now

                    # 触发回调
                    if self._on_intent_change:
                        try:
                            self._on_intent_change(final_intent)
                        except Exception as e:
                            logger.error(f"意图回调异常: {e}")

                    return final_intent

                self._pending_phase = None
                self._pending_count = 0

        return None

    def _evaluate_rules(self, result: DetectionResult) -> Optional[IntentResult]:
        """
        核心规则引擎 - 根据行为标签 + 位置 推断庄家意图
        """
        tag = result.tag
        position = self.current_position

        # ============ 规则 1: 低位区 + 吸筹信号 ============
        if position == BehaviorTag.POSITION_LOW:
            if tag in (BehaviorTag.SANDWICH_ACCUMULATION,
                       BehaviorTag.TRACTOR_ACCUMULATION,
                       BehaviorTag.VOL_DUIDAO):
                return IntentResult(
                    phase=ManipulationPhase.ACCUMULATION,
                    symbol=self.inst_id,
                    timestamp=time.time(),
                    confidence=0.75,
                    description=f"低位区检测到{tag.value}，庄家正在悄悄收集筹码",
                    alert_level="info",
                )

            if tag == BehaviorTag.WICK_SHAKE_OUT:
                # 低位插针可能是洗盘
                if self.current_phase in (ManipulationPhase.ACCUMULATION,
                                          ManipulationPhase.UNKNOWN):
                    return IntentResult(
                        phase=ManipulationPhase.SHAKEOUT,
                        symbol=self.inst_id,
                        timestamp=time.time(),
                        confidence=0.7,
                        description=f"低位插针 → 庄家试探支撑/清洗浮筹，拉升前最后恐吓",
                        alert_level="warning",
                    )

            if tag == BehaviorTag.VOL_TIANLIANG:
                # 低位天量可能是启动信号
                return IntentResult(
                    phase=ManipulationPhase.ACCUMULATION,
                    symbol=self.inst_id,
                    timestamp=time.time(),
                    confidence=0.65,
                    description=f"低位天量出现，疑似主力大举建仓",
                    alert_level="info",
                )

        # ============ 规则 2: 中位区 + 吸筹后出现拉升信号 ============
        if position == BehaviorTag.POSITION_MID:
            if self.current_phase in (ManipulationPhase.ACCUMULATION,
                                      ManipulationPhase.SHAKEOUT,
                                      ManipulationPhase.CONSOLIDATION):

                if tag == BehaviorTag.VOL_BEILIANG:
                    # 倍量 + 中位 → 可能进入拉升
                    # 需要额外判断是否有滞涨（在 StagnationDetector 中处理）
                    return IntentResult(
                        phase=ManipulationPhase.PUMP,
                        symbol=self.inst_id,
                        timestamp=time.time(),
                        confidence=0.6,
                        description=f"中位区倍量出现，此前已吸筹，疑似拉升初期",
                        alert_level="info",
                    )

                if tag == BehaviorTag.VOL_DUIDAO:
                    return IntentResult(
                        phase=ManipulationPhase.CONSOLIDATION,
                        symbol=self.inst_id,
                        timestamp=time.time(),
                        confidence=0.55,
                        description=f"中位对倒刷量 → 警惕制造活跃假象吸引跟风，或拉升中继洗盘",
                        alert_level="warning",
                    )

            if tag == BehaviorTag.WHALE_SELL:
                return IntentResult(
                    phase=ManipulationPhase.DISTRIBUTION,
                    symbol=self.inst_id,
                    timestamp=time.time(),
                    confidence=0.7,
                    description=f"中位区出现大单砸盘 → 警惕提前出货",
                    alert_level="warning",
                )

        # ============ 规则 3: 高位区 + 出货信号（最高优先级） ============
        if position == BehaviorTag.POSITION_HIGH:
            if tag in (BehaviorTag.STAGNATION_DISTRIBUTION,
                       BehaviorTag.WHALE_SELL):
                return IntentResult(
                    phase=ManipulationPhase.DISTRIBUTION,
                    symbol=self.inst_id,
                    timestamp=time.time(),
                    confidence=0.85,
                    description=f"高位区{tag.value} → 庄家正在把筹码倒给市场！",
                    alert_level="danger",
                )

            if tag == BehaviorTag.VOL_DUIDAO:
                return IntentResult(
                    phase=ManipulationPhase.DISTRIBUTION,
                    symbol=self.inst_id,
                    timestamp=time.time(),
                    confidence=0.7,
                    description=f"高位对倒 → 制造活跃假象诱多",
                    alert_level="danger",
                )

            if tag == BehaviorTag.SANDWICH_FAKE:
                return IntentResult(
                    phase=ManipulationPhase.DISTRIBUTION,
                    symbol=self.inst_id,
                    timestamp=time.time(),
                    confidence=0.7,
                    description=f"高位虚假压单 → 假支撑真派发",
                    alert_level="danger",
                )

            if tag == BehaviorTag.WICK_SHAKE_OUT:
                return IntentResult(
                    phase=ManipulationPhase.DISTRIBUTION,
                    symbol=self.inst_id,
                    timestamp=time.time(),
                    confidence=0.65,
                    description=f"高位插针 → 疑似出货前最后拉升诱多",
                    alert_level="danger",
                )

        # ============ 规则 4: 通用信号 ============
        if tag == BehaviorTag.VOL_DILIANG:
            if self.current_phase in (ManipulationPhase.ACCUMULATION,
                                      ManipulationPhase.UNKNOWN):
                return IntentResult(
                    phase=ManipulationPhase.CONSOLIDATION,
                    symbol=self.inst_id,
                    timestamp=time.time(),
                    confidence=0.5,
                    description=f"地量出现 → 市场冷清，方向选择前夕",
                    alert_level="info",
                )

        # ============ 规则 5: 状态间的自然过渡 ============
        if self.current_phase == ManipulationPhase.PUMP and tag == BehaviorTag.SANDWICH_FAKE:
            return IntentResult(
                phase=ManipulationPhase.PUMP,
                symbol=self.inst_id,
                timestamp=time.time(),
                confidence=0.6,
                description=f"拉升中压单消失 → 庄家撤掉压制，可能继续拉升",
                alert_level="info",
            )

        return None

    def _get_suggestion(self, phase: ManipulationPhase) -> str:
        """根据阶段返回操作建议"""
        suggestions = {
            ManipulationPhase.ACCUMULATION: "关注但不急于入场，等待洗盘确认后的信号",
            ManipulationPhase.SHAKEOUT: "观察是否出现止跌信号，耐心等待右侧确认",
            ManipulationPhase.PUMP: "可考虑分批布局，严格止损",
            ManipulationPhase.DISTRIBUTION: "⚠️ 建议减仓或观望，避免追高",
            ManipulationPhase.CONSOLIDATION: "观望为主，等待方向选择",
            ManipulationPhase.UNKNOWN: "数据不足，等待更多信号",
        }
        return suggestions.get(phase, "")

    def get_status(self) -> dict:
        """获取当前引擎状态（供外部查询）"""
        return {
            "symbol": self.inst_id,
            "current_phase": self.current_phase.display,
            "current_position": self.current_position.value if self.current_position else "未知",
            "pending_phase": self._pending_phase.display if self._pending_phase else "无",
            "pending_count": self._pending_count,
            "confirm_threshold": self._confirm_threshold,
            "recent_tags": [t.tag.value for t in list(self._recent_tags)[-10:]],
            "phase_history": [
                {"from": t.from_phase.display, "to": t.to_phase.display,
                 "reason": t.reason, "ts": t.timestamp}
                for t in self._phase_history[-5:]
            ],
        }
