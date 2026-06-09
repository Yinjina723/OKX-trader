# manipulation/__init__.py
"""
庄家行为检测 —— 日线专用版

基于纯 K 线数据的庄家行为分析，包含:
- kline_helpers  — K线辅助检测（位置/量异常/插针/滞涨）
- wyckoff        — 威科夫模式识别
- daily_engine   — 日线操盘检测主引擎
- types          — 共用类型定义
"""

from .daily_engine import run_daily_manipulation

__all__ = [
    "run_daily_manipulation",
]
