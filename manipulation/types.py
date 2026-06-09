# manipulation/types.py
"""庄家行为分析共用类型定义（从 detectors.py / intent_engine.py 提取）

   BehaviorTag    — 庄家行为标签枚举（原 detectors.py）
   DetectionResult — 检测结果 dataclass（原 detectors.py）
   ManipulationPhase — 庄家操纵阶段枚举（原 intent_engine.py）
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict


# ==================== BehaviorTag（原 detectors.py 第30-59行）====================

class BehaviorTag(Enum):
    """庄家行为标签"""
    # 位置
    POSITION_LOW = "低位区"
    POSITION_MID = "中位区"
    POSITION_HIGH = "高位区"

    # 成交量
    VOL_DILIANG = "地量"
    VOL_BEILIANG = "倍量"
    VOL_TIANLIANG = "天量"
    VOL_DUIDAO = "疑似对倒"

    # 夹板战术
    SANDWICH_ACCUMULATION = "夹板吸筹"
    SANDWICH_FAKE = "虚假压单"

    # 拖拉机单
    TRACTOR_ACCUMULATION = "隐蔽吸筹(拆单)"

    # 插针
    WICK_DOWN_TEST = "向下试盘"
    WICK_SHAKE_OUT = "洗盘插针"
    WICK_UP_DISTRIBUTION = "向上插针(诱多出货)"

    # 放量滞涨
    STAGNATION_DISTRIBUTION = "高位派发"

    # 大单砸盘
    WHALE_SELL = "大单主动出货"


# ==================== DetectionResult（原 detectors.py 第62-80行）====================

@dataclass
class DetectionResult:
    """检测结果"""
    tag: BehaviorTag
    symbol: str
    timestamp: float
    confidence: float = 1.0       # 置信度 0~1
    detail: str = ""              # 详细描述
    data: dict = field(default_factory=dict)  # 附加数据

    def to_dict(self) -> dict:
        return {
            "tag": self.tag.value,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "detail": self.detail,
            "data": self.data,
        }


# ==================== ManipulationPhase（原 intent_engine.py 第26-43行）====================

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
