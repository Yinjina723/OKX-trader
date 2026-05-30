# manipulation/__init__.py
"""
庄家行为检测 V3 — 数据驱动的方向预测引擎（模块化拆分）

分析维度（7个 + 博弈合成）：
  1. crowd       — 散户拥挤度（反向指标）
  2. elite       — 精英 vs 散户背离 + 精英多周期趋向
  3. oi_flow     — OI 持仓流向（四象限）
  4. taker       — Taker 主动买卖量趋势（聪明钱方向）
  5. funding     — 资金费率极端值
  6. basis       — 多周期基差分析
  7. synthesis   — 7维加权合成 + 阶段判定

辅助模块：
  kline_helpers — K线辅助检测（位置/量异常/插针/滞涨）
  wyckoff       — 威科夫模式识别
  predict       — 下一步预测 + 综合点位计算

主入口：
  engine.run_manipulation_analysis()  — 统一入口（含 dataclass 参数封装）
"""

from .engine import run_manipulation_analysis, calculate_manipulation_target, ManipulationInput

__all__ = [
    "run_manipulation_analysis",
    "calculate_manipulation_target",
    "ManipulationInput",
]
