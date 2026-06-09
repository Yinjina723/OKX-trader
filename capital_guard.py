# capital_guard.py
"""资金卫士模块 —— 阶梯入场 + 联合爆仓价计算 + 安全评估。

核心理念（来自交易员经验）：
  不用高杠杆，用 70% 资金入场 + 30% 备用金在更深爆仓区挂单。
  联合爆仓价被推到"不可触及"的水平，确保不爆仓。

空头示例：
  阶段1（70%资金）: 入场价=P1, 杠杆=1x → 独立爆仓 = P1 × 2.0
  阶段2（30%备用金）: 入场价=P2(猎杀爆仓区), 杠杆=1x
  联合爆仓 ≈ 加权入场 × 2.0

用法:
  from capital_guard import build_capital_plan, CapitalPlan
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 默认配置 ──
DEFAULT_TOTAL_CAPITAL = 220.0
DEFAULT_TRADING_RATIO = 0.5        # 50% 资金用于首段入场
DEFAULT_RESERVE_RATIO = 0.5        # 50% 资金作为备用金
DEFAULT_LEVERAGE = 1               # 1x 杠杆
UNREACHABLE_THRESHOLD_PCT = 200    # 安全距离 > 200% 视为"不可触及"


@dataclass
class EntryPhase:
    """单个入场阶段。"""
    phase: int                      # 1 或 2
    label: str                      # "首段入场(70%)" / "备用金入场(30%)"
    capital: float                  # 投入资金
    leverage: int                   # 杠杆倍数
    entry_price: float              # 入场价
    notional: float                 # 名义仓位 = capital × leverage
    independent_liq: float          # 该阶段独立爆仓价
    description: str                # 中文描述


@dataclass
class CapitalPlan:
    """完整资金方案。"""
    symbol: str
    direction: str                  # "long" | "short" | "neutral"
    total_capital: float            # 总资金
    trading_ratio: float            # 首段比例
    reserve_ratio: float            # 备用比例
    leverage: int                   # 杠杆

    current_price: float            # 当前价格
    phases: List[EntryPhase] = field(default_factory=list)

    weighted_entry: float = 0.0     # 加权入场价
    combined_liquidation: float = 0.0  # 联合爆仓价
    safety_distance_pct: float = 0.0   # 安全距离(%)
    safety_level: str = "unknown"       # "unreachable" | "safe" | "danger"

    hunt_zone_price: Optional[float] = None  # 猎杀模块提供的备用金入场价
    summary: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "total_capital": self.total_capital,
            "trading_ratio": self.trading_ratio,
            "reserve_ratio": self.reserve_ratio,
            "leverage": self.leverage,
            "current_price": self.current_price,
            "phases": [
                {
                    "phase": p.phase,
                    "label": p.label,
                    "capital": round(p.capital, 2),
                    "leverage": p.leverage,
                    "entry_price": round(p.entry_price, 8),
                    "notional": round(p.notional, 2),
                    "independent_liq": round(p.independent_liq, 8),
                    "description": p.description,
                }
                for p in self.phases
            ],
            "weighted_entry": round(self.weighted_entry, 8),
            "combined_liquidation": round(self.combined_liquidation, 8),
            "safety_distance_pct": round(self.safety_distance_pct, 1),
            "safety_level": self.safety_level,
            "hunt_zone_price": round(self.hunt_zone_price, 8) if self.hunt_zone_price else None,
            "summary": self.summary,
        }


def calc_liquidation_price(
    entry_price: float,
    leverage: int,
    direction: str,
    maintenance_margin: float = 0.005,
) -> float:
    """计算独立爆仓价。

    简化公式（忽略维持保证金率的微小影响）:
      多头爆仓 = entry_price × (1 - 1/leverage)
      空头爆仓 = entry_price × (1 + 1/leverage)

    完整公式（含维持保证金率）:
      多头爆仓 = entry_price × (1 - 1/leverage + maintenance_margin/leverage)
      空头爆仓 = entry_price × (1 + 1/leverage - maintenance_margin/leverage)
    """
    if leverage <= 0 or entry_price <= 0:
        return 0.0

    if direction == "long":
        # 价格下跌到此处爆仓
        return entry_price * (1.0 - 1.0 / leverage + maintenance_margin / leverage)
    elif direction == "short":
        # 价格上涨到此处爆仓
        return entry_price * (1.0 + 1.0 / leverage - maintenance_margin / leverage)
    else:
        return 0.0


def build_capital_plan(
    symbol: str,
    direction: str,
    current_price: float,
    ai_entry_price: Optional[float],
    hunt_zone_price: Optional[float],
    total_capital: float = DEFAULT_TOTAL_CAPITAL,
    trading_ratio: float = DEFAULT_TRADING_RATIO,
    reserve_ratio: float = DEFAULT_RESERVE_RATIO,
    leverage: int = DEFAULT_LEVERAGE,
) -> CapitalPlan:
    """根据方向、AI入场价、猎杀爆仓区价格，构建阶梯入场方案。

    参数:
      symbol: 交易对
      direction: "long" / "short" / "neutral"
      current_price: 当前价格
      ai_entry_price: AI 推荐的入场价（用于阶段1）
      hunt_zone_price: 猎杀模块输出的密集爆仓区价格（用于阶段2备用金入场）
      total_capital: 总资金（默认 $220）
      trading_ratio: 首段入场比例（默认 0.7 = 70%）
      reserve_ratio: 备用金比例（默认 0.3 = 30%）
      leverage: 杠杆倍数（默认 1）

    返回:
      CapitalPlan 对象
    """
    plan = CapitalPlan(
        symbol=symbol,
        direction=direction,
        total_capital=total_capital,
        trading_ratio=trading_ratio,
        reserve_ratio=reserve_ratio,
        leverage=leverage,
        current_price=current_price,
    )

    # ── 方向无效时直接返回 ──
    if direction not in ("long", "short"):
        plan.safety_level = "unknown"
        plan.summary = f"方向为 {direction}，无需资金方案"
        return plan

    # ── AI 入场价无效时用当前价 ──
    phase1_entry = ai_entry_price if ai_entry_price and ai_entry_price > 0 else current_price
    if phase1_entry <= 0:
        plan.safety_level = "unknown"
        plan.summary = "入场价无效，无法构建方案"
        return plan

    # ── 阶段1: 70% 资金入场 ──
    phase1_capital = total_capital * trading_ratio
    phase1_notional = phase1_capital * leverage
    phase1_liq = calc_liquidation_price(phase1_entry, leverage, direction)

    plan.phases.append(EntryPhase(
        phase=1,
        label=f"首段入场({int(trading_ratio*100)}%)",
        capital=phase1_capital,
        leverage=leverage,
        entry_price=phase1_entry,
        notional=phase1_notional,
        independent_liq=phase1_liq,
        description=f"投入 ${phase1_capital:.0f}，{leverage}x杠杆，独立爆仓价 {phase1_liq:.6f}",
    ))

    # ── 阶段2: 30% 备用金 ──
    phase2_capital = total_capital * reserve_ratio
    plan.hunt_zone_price = hunt_zone_price

    if hunt_zone_price and hunt_zone_price > 0:
        # 使用猎杀模块的爆仓密集区作为备用金入场价
        phase2_entry = hunt_zone_price
        phase2_label = f"备用金入场({int(reserve_ratio*100)}%) — 猎杀爆仓区"
    else:
        # 无猎杀数据时，按方向估算一个更深的入场价
        if direction == "long":
            phase2_entry = phase1_entry * 0.85  # 下方 15%
        else:
            phase2_entry = phase1_entry * 1.15  # 上方 15%
        phase2_label = f"备用金入场({int(reserve_ratio*100)}%) — 估算更深位置"

    phase2_notional = phase2_capital * leverage
    phase2_liq = calc_liquidation_price(phase2_entry, leverage, direction)

    plan.phases.append(EntryPhase(
        phase=2,
        label=phase2_label,
        capital=phase2_capital,
        leverage=leverage,
        entry_price=phase2_entry,
        notional=phase2_notional,
        independent_liq=phase2_liq,
        description=f"备用 ${phase2_capital:.0f} 在 {phase2_entry:.6f} 挂单，独立爆仓价 {phase2_liq:.6f}",
    ))

    # ── 计算加权入场价 ──
    total_margin = phase1_capital + phase2_capital
    if total_margin > 0:
        plan.weighted_entry = (
            phase1_entry * phase1_capital + phase2_entry * phase2_capital
        ) / total_margin
    else:
        plan.weighted_entry = phase1_entry

    # ── 计算联合爆仓价 ──
    # 联合爆仓 ≈ 加权入场 × (1 ± 1/leverage)
    plan.combined_liquidation = calc_liquidation_price(
        plan.weighted_entry, leverage, direction
    )

    # ── 计算安全距离 ──
    if current_price > 0:
        if direction == "long":
            # 多头：价格跌到联合爆仓价的距离
            plan.safety_distance_pct = (
                (current_price - plan.combined_liquidation) / current_price * 100
            )
        else:
            # 空头：价格涨到联合爆仓价的距离
            plan.safety_distance_pct = (
                (plan.combined_liquidation - current_price) / current_price * 100
            )

    # ── 安全等级 ──
    if plan.safety_distance_pct >= UNREACHABLE_THRESHOLD_PCT:
        plan.safety_level = "unreachable"
    elif plan.safety_distance_pct >= 100:
        plan.safety_level = "safe"
    elif plan.safety_distance_pct > 0:
        plan.safety_level = "danger"
    else:
        plan.safety_level = "danger"

    # ── 生成摘要 ──
    level_emoji = {
        "unreachable": "✅ 级别安全",
        "safe": "⚠️ 较安全",
        "danger": "❌ 危险",
        "unknown": "❓ 未知",
    }
    dir_cn = "做多" if direction == "long" else "做空"

    lines = [
        f"🛡️ 资金卫士 · {symbol} {dir_cn}方案",
        f"💰 总资金: ${total_capital:.0f} | 杠杆: {leverage}x | 分仓: {int(trading_ratio*100)}/{int(reserve_ratio*100)}",
        f"",
        f"📌 阶段1: {plan.phases[0].label}",
        f"   入场价: {plan.phases[0].entry_price:.6f}",
        f"   保证金: ${plan.phases[0].capital:.0f} | 独立爆仓: {plan.phases[0].independent_liq:.6f}",
        f"",
        f"📌 阶段2: {plan.phases[1].label}",
        f"   入场价: {plan.phases[1].entry_price:.6f}",
        f"   保证金: ${plan.phases[1].capital:.0f} | 独立爆仓: {plan.phases[1].independent_liq:.6f}",
        f"",
        f"📊 加权入场: {plan.weighted_entry:.6f}",
        f"💥 联合爆仓: {plan.combined_liquidation:.6f}",
        f"🛡️ 安全距离: {plan.safety_distance_pct:.1f}% — {level_emoji.get(plan.safety_level, '?')}",
    ]

    if plan.safety_level == "unreachable":
        lines.append(f"\n🏆 爆仓价几乎不可能被触及，可放心持有。")
    elif plan.safety_level == "safe":
        lines.append(f"\n⚠️ 安全距离充足但需关注极端行情。")
    elif plan.safety_level == "danger":
        lines.append(f"\n🔴 安全距离不足，建议调整资金比例或杠杆。")

    plan.summary = "\n".join(lines)

    logger.info(
        f"资金卫士 [{symbol}]: 方向={dir_cn}, 加权入场={plan.weighted_entry:.6f}, "
        f"联合爆仓={plan.combined_liquidation:.6f}, 安全距离={plan.safety_distance_pct:.1f}%, "
        f"等级={plan.safety_level}"
    )

    return plan


def integrate_with_hunt(
    symbol: str,
    direction: str,
    current_price: float,
    ai_entry_price: Optional[float],
    hunt_result: Optional[Dict],
    total_capital: float = DEFAULT_TOTAL_CAPITAL,
    trading_ratio: float = DEFAULT_TRADING_RATIO,
    reserve_ratio: float = DEFAULT_RESERVE_RATIO,
    leverage: int = DEFAULT_LEVERAGE,
) -> CapitalPlan:
    """将猎杀模块的输出整合到资金卫士中。

    从 hunt_result 中提取密集爆仓区价格作为阶段2入场价。

    参数:
      symbol: 交易对
      direction: AI 分析的方向
      current_price: 当前价
      ai_entry_price: AI 推荐入场价
      hunt_result: 猎杀模块输出的 dict (HuntResult.to_dict())
      total_capital, trading_ratio, reserve_ratio, leverage: 资金参数

    返回:
      CapitalPlan 对象
    """
    hunt_zone_price = None

    if hunt_result and hunt_result.get("mode") != "none":
        zones = hunt_result.get("liquidation_zones", [])
        # 取 10x 爆仓区（最密集），找不到则取第一个
        for z in zones:
            if z.get("leverage") == 10:
                hunt_zone_price = z.get("price")
                break
        if hunt_zone_price is None and zones:
            hunt_zone_price = zones[0].get("price")

    return build_capital_plan(
        symbol=symbol,
        direction=direction,
        current_price=current_price,
        ai_entry_price=ai_entry_price,
        hunt_zone_price=hunt_zone_price,
        total_capital=total_capital,
        trading_ratio=trading_ratio,
        reserve_ratio=reserve_ratio,
        leverage=leverage,
    )
