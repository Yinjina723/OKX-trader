# manipulation_v2.py
"""
庄家行为检测 V3 兼容包装器（P3模块化拆分后）

本文件保留以兼容旧的 import 路径:
    from manipulation_v2 import run_manipulation_analysis, calculate_manipulation_target

所有实现已迁移到 manipulation/ 子模块中。
"""
from manipulation.engine import run_manipulation_analysis, calculate_manipulation_target, ManipulationInput

# 老接口兼容: 也返回 calculate_manipulation_target
__all__ = [
    "run_manipulation_analysis",
    "calculate_manipulation_target",
    "ManipulationInput",
]
