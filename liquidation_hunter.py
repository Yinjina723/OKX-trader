# liquidation_hunter.py
"""猎杀爆仓策略模块 —— 根据多空比极端值推算爆仓区域，提前挂单"吃尸体"。

核心逻辑：
  1. 当多空比 > LS_LONG_EXTREME (默认2.0)，多头拥挤 → 猎杀多头（下方挂多单）
  2. 当多空比 < LS_SHORT_EXTREME (默认0.7)，空头拥挤 → 猎杀空头（上方挂空单）
  3. 以 VWAP 作为散户平均入场价，依杠杆档位推算爆仓瀑布区
  4. 在最密集爆仓区挂限价单，设紧止损，目标回到 VWAP

爆仓价公式:
  多头爆仓 = VWAP × (1 - 1/杠杆)   ← 价格跌到此处触发强平
  空头爆仓 = VWAP × (1 + 1/杠杆)   ← 价格涨到此处触发强平
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── 默认配置 ──
DEFAULT_LEVERAGE_LEVELS = [20, 10, 5]   # 杠杆档位（从高到低，先爆高杠杆）
LS_LONG_EXTREME = 2.0                   # 多空比高于此值触发猎杀多头
LS_SHORT_EXTREME = 0.7                  # 多空比低于此值触发猎杀空头
DEFAULT_ENTRY_BUFFER = 1.005            # 挂单在爆仓区上方 0.5%（确保先于爆仓成交）
DEFAULT_STOP_MARGIN = 0.02              # 止损在最低爆仓区下方 2%
DEFAULT_RISK_REWARD_TARGET = 1.0        # 止盈目标：回到 VWAP（盈亏比 1:1 起）


@dataclass
class LiquidationZone:
    """单个杠杆档位的爆仓区。"""
    leverage: int
    price: float
    description: str


@dataclass
class HuntResult:
    """猎杀策略分析结果。"""
    mode: str                        # "hunt_longs" | "hunt_shorts" | "none"
    mode_cn: str                     # 中文模式名
    ls_ratio: float                  # 当前多空比
    vwap: Optional[float]            # VWAP（散户成本）
    current_price: float             # 当前价格
    liquidation_zones: List[LiquidationZone] = field(default_factory=list)
    recommended_entry: Optional[float] = None    # 推荐挂单价
    stop_loss: Optional[float] = None            # 止损
    take_profit: Optional[float] = None          # 止盈
    risk_reward_ratio: Optional[float] = None    # 盈亏比
    signal: str = "none"                         # long / short / none
    summary: str = ""                            # 中文总结
    
    def to_dict(self) -> Dict:
        return {
            "mode": self.mode,
            "mode_cn": self.mode_cn,
            "ls_ratio": self.ls_ratio,
            "vwap": self.vwap,
            "current_price": self.current_price,
            "liquidation_zones": [
                {"leverage": z.leverage, "price": z.price, "description": z.description}
                for z in self.liquidation_zones
            ],
            "recommended_entry": self.recommended_entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward_ratio": self.risk_reward_ratio,
            "signal": self.signal,
            "summary": self.summary,
        }


def analyze_liquidation_hunt(
    ls_ratio: Optional[float],
    vwap: Optional[float],
    current_price: float,
    leverage_levels: List[int] = None,
    ls_long_extreme: float = LS_LONG_EXTREME,
    ls_short_extreme: float = LS_SHORT_EXTREME,
) -> HuntResult:
    """
    分析是否触发猎杀爆仓策略。

    参数:
      ls_ratio: 当前多空比（如 2.5 表示 2.5:1 多头/空头）
      vwap: 成交量加权均价（散户近似成本）
      current_price: 当前价格
      leverage_levels: 要计算爆仓的杠杆档位，默认 [20, 10, 5]
      ls_long_extreme: 多空比阈值，>此值猎杀多头
      ls_short_extreme: 多空比阈值，<此值猎杀空头

    返回:
      HuntResult 对象
    """

    if leverage_levels is None:
        leverage_levels = DEFAULT_LEVERAGE_LEVELS

    # ── 数据不足，不触发 ──
    if ls_ratio is None or vwap is None or current_price <= 0 or vwap <= 0:
        return HuntResult(
            mode="none",
            mode_cn="数据不足",
            ls_ratio=ls_ratio or 0,
            vwap=vwap,
            current_price=current_price,
            signal="none",
            summary="多空比或VWAP数据不足，无法推算爆仓区",
        )

    # ── 判断极端拥挤 ──
    if ls_ratio >= ls_long_extreme:
        return _hunt_longs(ls_ratio, vwap, current_price, leverage_levels)
    elif ls_ratio <= ls_short_extreme:
        return _hunt_shorts(ls_ratio, vwap, current_price, leverage_levels)
    else:
        return HuntResult(
            mode="none",
            mode_cn="正常范围",
            ls_ratio=ls_ratio,
            vwap=vwap,
            current_price=current_price,
            signal="none",
            summary=f"多空比 {ls_ratio:.2f}:1 处于正常范围({ls_short_extreme}-{ls_long_extreme})，无需猎杀",
        )


def _hunt_longs(
    ls_ratio: float,
    vwap: float,
    current_price: float,
    leverage_levels: List[int],
) -> HuntResult:
    """
    猎杀多头：市场极度看多 → 大概率向下插针爆多头 → 在下方挂多单。
    
    爆仓区在 VWAP 下方：
      20x: VWAP × (1 - 1/20) = VWAP × 0.95
      10x: VWAP × (1 - 1/10) = VWAP × 0.90  ← 最密集爆仓区
      5x:  VWAP × (1 - 1/5)  = VWAP × 0.80  ← 极限插针
    """
    zones: List[LiquidationZone] = []

    for lev in sorted(leverage_levels, reverse=True):  # 高杠杆先爆
        liq_price = vwap * (1.0 - 1.0 / lev)
        zones.append(LiquidationZone(
            leverage=lev,
            price=round(liq_price, 8),
            description=f"{lev}x多头爆仓价 ≈ {liq_price:.6f} (VWAP跌{1.0/lev*100:.0f}%)",
        ))

    # 核心猎杀区：10x 爆仓价（最密集）
    core_lev = 10 if 10 in leverage_levels else leverage_levels[-1]
    core_liq = vwap * (1.0 - 1.0 / core_lev)

    # 极限区：最低杠杆的爆仓价
    min_lev = min(leverage_levels)
    extreme_liq = vwap * (1.0 - 1.0 / min_lev)

    # 推荐入场：10x 爆仓区略上方，确保先于爆仓成交
    entry = core_liq * DEFAULT_ENTRY_BUFFER

    # 止损：在 5x 爆仓区下方（跌破最低杠杆爆仓价 = 趋势彻底反转）
    stop = extreme_liq * (1.0 - DEFAULT_STOP_MARGIN)

    # 止盈：回到 VWAP（回到第一波爆仓起点之上）
    tp = vwap

    # 盈亏比
    if entry != stop and entry > 0:
        rr = abs(tp - entry) / abs(entry - stop)
    else:
        rr = 0

    # 汇总
    zone_lines = "\n".join(f"    {z.description}" for z in zones)
    summary = (
        f"🔴 多头极端拥挤（多空比 {ls_ratio:.2f}:1）\n"
        f"📍 VWAP（散户成本）: {vwap:.6f}\n"
        f"💥 爆仓瀑布预估(从VWAP往下):\n{zone_lines}\n"
        f"🎯 猎杀策略：在 {entry:.6f} 挂多单\n"
        f"   ├── 止损: {stop:.6f}\n"
        f"   └── 止盈: {tp:.6f} (回到VWAP)\n"
        f"📊 盈亏比: 1:{rr:.1f}"
    )

    logger.info(f"猎杀多头: LS={ls_ratio:.2f}, VWAP={vwap:.6f}, "
                f"入场={entry:.6f}, 止损={stop:.6f}, 止盈={tp:.6f}")

    return HuntResult(
        mode="hunt_longs",
        mode_cn="猎杀多头",
        ls_ratio=ls_ratio,
        vwap=vwap,
        current_price=current_price,
        liquidation_zones=zones,
        recommended_entry=round(entry, 8),
        stop_loss=round(stop, 8),
        take_profit=round(tp, 8),
        risk_reward_ratio=round(rr, 1),
        signal="long",
        summary=summary,
    )


def _hunt_shorts(
    ls_ratio: float,
    vwap: float,
    current_price: float,
    leverage_levels: List[int],
) -> HuntResult:
    """
    猎杀空头：市场极度看空 → 大概率向上拉盘爆空头 → 在上方挂空单。
    
    爆仓区在 VWAP 上方：
      20x: VWAP × (1 + 1/20) = VWAP × 1.05
      10x: VWAP × (1 + 1/10) = VWAP × 1.10  ← 最密集爆仓区
      5x:  VWAP × (1 + 1/5)  = VWAP × 1.20  ← 极限拉盘
    """
    zones: List[LiquidationZone] = []

    for lev in sorted(leverage_levels, reverse=True):
        liq_price = vwap * (1.0 + 1.0 / lev)
        zones.append(LiquidationZone(
            leverage=lev,
            price=round(liq_price, 8),
            description=f"{lev}x空头爆仓价 ≈ {liq_price:.6f} (VWAP涨{1.0/lev*100:.0f}%)",
        ))

    core_lev = 10 if 10 in leverage_levels else leverage_levels[-1]
    core_liq = vwap * (1.0 + 1.0 / core_lev)

    min_lev = min(leverage_levels)
    extreme_liq = vwap * (1.0 + 1.0 / min_lev)

    # 推荐入场：10x 爆仓区略下方
    entry = core_liq / DEFAULT_ENTRY_BUFFER

    # 止损：最高杠杆爆仓区上方
    stop = extreme_liq * (1.0 + DEFAULT_STOP_MARGIN)

    # 止盈：回到 VWAP
    tp = vwap

    if entry != stop and entry > 0:
        rr = abs(entry - tp) / abs(stop - entry)
    else:
        rr = 0

    zone_lines = "\n".join(f"    {z.description}" for z in zones)
    summary = (
        f"🟢 空头极端拥挤（多空比 {ls_ratio:.2f}:1）\n"
        f"📍 VWAP（散户成本）: {vwap:.6f}\n"
        f"💥 爆仓瀑布预估(从VWAP往上):\n{zone_lines}\n"
        f"🎯 猎杀策略：在 {entry:.6f} 挂空单\n"
        f"   ├── 止损: {stop:.6f}\n"
        f"   └── 止盈: {tp:.6f} (回到VWAP)\n"
        f"📊 盈亏比: 1:{rr:.1f}"
    )

    logger.info(f"猎杀空头: LS={ls_ratio:.2f}, VWAP={vwap:.6f}, "
                f"入场={entry:.6f}, 止损={stop:.6f}, 止盈={tp:.6f}")

    return HuntResult(
        mode="hunt_shorts",
        mode_cn="猎杀空头",
        ls_ratio=ls_ratio,
        vwap=vwap,
        current_price=current_price,
        liquidation_zones=zones,
        recommended_entry=round(entry, 8),
        stop_loss=round(stop, 8),
        take_profit=round(tp, 8),
        risk_reward_ratio=round(rr, 1),
        signal="short",
        summary=summary,
    )
