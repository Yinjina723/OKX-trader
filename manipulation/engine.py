# manipulation/engine.py
"""
庄家行为检测 V3 主引擎（P2 dataclass参数封装 + P3模块化拆分）

使用 ManipulationInput dataclass 封装 run_manipulation_analysis 的 25+ 参数。
所有分析维度从子模块导入，主入口函数负责编排流程。
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

from detectors import BehaviorTag, DetectionResult
from intent_engine import (
    ManipulationPhase,
)

from .crowd import analyze_crowd_crowding
from .elite import analyze_elite_divergence, analyze_elite_trend
from .oi_flow import analyze_oi_flow
from .taker import analyze_taker_flow
from .funding import analyze_funding
from .basis import analyze_multi_tf_basis
from .synthesis import synthesize_direction, determine_phase_and_direction
from .wyckoff import simplified_wyckoff
from .predict import predict_next_move_enhanced, calculate_manipulation_target
from .kline_helpers import detect_position, detect_volume_anomaly, detect_wick, detect_stagnation

logger = logging.getLogger(__name__)

# 阶段映射 (新 → 旧)
PHASE_MAP = {
    ManipulationPhase.UNKNOWN: ("unknown", "方向不明"),
    ManipulationPhase.ACCUMULATION: ("accumulation", "吸筹期"),
    ManipulationPhase.SHAKEOUT: ("shakeout", "洗盘震仓"),
    ManipulationPhase.PUMP: ("markup", "拉升期"),
    ManipulationPhase.DISTRIBUTION: ("distribution", "派发期"),
    ManipulationPhase.CONSOLIDATION: ("unknown", "盘整"),
}


@dataclass
class ManipulationInput:
    """
    P2: run_manipulation_analysis 参数 dataclass 封装。
    替代原来的 25+ 个位置参数，提升可维护性和可读性。
    """
    df: pd.DataFrame
    current_price: float

    # ── 核心数据 ──
    oi_data: List = field(default_factory=list)
    taker_vol: List = field(default_factory=list)
    funding_rate: float = 0.0
    ls_ratio: float = 0.0
    elite_ratio: float = 0.0

    # ── 订单簿 ──
    orderbook_features: Dict = field(default_factory=dict)
    orderbook: Dict = field(default_factory=dict)

    # ── 辅助数据 ──
    insurance_data: List = field(default_factory=list)
    mark_deviation: float = 0.0
    ai_signal: Dict = field(default_factory=dict)

    # ── 扩展数据 ──
    funding_rate_history: List = field(default_factory=list)
    index_price: float = 0.0
    elite_position_data: List = field(default_factory=list)
    option_oi_strike_data: List = field(default_factory=list)
    option_pcr_data: List = field(default_factory=list)
    position_tiers_data: List = field(default_factory=list)
    elite_trend_data: Dict = field(default_factory=dict)
    sentiment_data: Dict = field(default_factory=dict)
    symbol: str = ""

    # ── 多周期数据 ──
    multi_tf_oi: Dict = field(default_factory=dict)
    multi_tf_taker: Dict = field(default_factory=dict)
    multi_tf_ls: Dict = field(default_factory=dict)
    index_candles_5m: List = field(default_factory=list)
    index_candles_1H: List = field(default_factory=list)
    mark_candles_5m: List = field(default_factory=list)

    # ── 检测器参数 ──
    wick_shadow_ratio: float = 3.0


def run_manipulation_analysis(
    # P2: 保留原有函数签名以兼容旧调用方，但也接受 ManipulationInput
    df: pd.DataFrame = None,
    current_price: float = 0,
    oi_data: List = None,
    taker_vol: List = None,
    funding_rate: float = 0,
    ls_ratio: float = 0,
    elite_ratio: float = 0,
    orderbook_features: Dict = None,
    orderbook: Dict = None,
    insurance_data: List = None,
    mark_deviation: float = 0,
    ai_signal: Dict = None,
    funding_rate_history: List = None,
    index_price: float = 0,
    elite_position_data: List = None,
    option_oi_strike_data: List = None,
    option_pcr_data: List = None,
    position_tiers_data: List = None,
    elite_trend_data: Dict = None,
    sentiment_data: Dict = None,
    symbol: str = "",
    multi_tf_oi: Dict = None,
    multi_tf_taker: Dict = None,
    multi_tf_ls: Dict = None,
    index_candles_5m: List = None,
    index_candles_1H: List = None,
    mark_candles_5m: List = None,
    wick_shadow_ratio: float = 3.0,
    # P2: 也支持传入 dataclass 作为第一个参数
    _input: ManipulationInput = None,
) -> Dict:
    """
    V3 庄家博弈推断引擎（模块化版）
    
    支持两种调用方式:
      1. 传统方式: run_manipulation_analysis(df=..., current_price=..., ...)
      2. Dataclass方式: run_manipulation_analysis(_input=ManipulationInput(df=..., ...))
    
    核心流程：
      散户多空比 → 拥挤度
      精英 vs 散户 → 背离度
      OI 变化 + 价格 → 资金方向
      Taker 买卖 → 聪明钱方向
      资金费率 → 拥挤辅助
      ↓
      7维加权合成 → 明确方向(long/short/neutral) + 推理链
      ↓
      结合K线辅助 → 阶段 + 价位建议
    """
    # P2: 如果传入了 dataclass，从中提取参数
    if _input is not None:
        df = _input.df
        current_price = _input.current_price
        oi_data = _input.oi_data if oi_data is None else oi_data
        taker_vol = _input.taker_vol if taker_vol is None else taker_vol
        funding_rate = _input.funding_rate if funding_rate == 0 else funding_rate
        ls_ratio = _input.ls_ratio if ls_ratio == 0 else ls_ratio
        elite_ratio = _input.elite_ratio if elite_ratio == 0 else elite_ratio
        orderbook = _input.orderbook if orderbook is None else orderbook
        insurance_data = _input.insurance_data if insurance_data is None else insurance_data
        mark_deviation = _input.mark_deviation if mark_deviation == 0 else mark_deviation
        ai_signal = _input.ai_signal if ai_signal is None else ai_signal
        funding_rate_history = _input.funding_rate_history if funding_rate_history is None else funding_rate_history
        index_price = _input.index_price if index_price == 0 else index_price
        elite_position_data = _input.elite_position_data if elite_position_data is None else elite_position_data
        option_oi_strike_data = _input.option_oi_strike_data if option_oi_strike_data is None else option_oi_strike_data
        option_pcr_data = _input.option_pcr_data if option_pcr_data is None else option_pcr_data
        position_tiers_data = _input.position_tiers_data if position_tiers_data is None else position_tiers_data
        elite_trend_data = _input.elite_trend_data if elite_trend_data is None else elite_trend_data
        sentiment_data = _input.sentiment_data if sentiment_data is None else sentiment_data
        symbol = _input.symbol if not symbol else symbol
        multi_tf_oi = _input.multi_tf_oi if multi_tf_oi is None else multi_tf_oi
        multi_tf_taker = _input.multi_tf_taker if multi_tf_taker is None else multi_tf_taker
        multi_tf_ls = _input.multi_tf_ls if multi_tf_ls is None else multi_tf_ls
        index_candles_5m = _input.index_candles_5m if index_candles_5m is None else index_candles_5m
        index_candles_1H = _input.index_candles_1H if index_candles_1H is None else index_candles_1H
        mark_candles_5m = _input.mark_candles_5m if mark_candles_5m is None else mark_candles_5m
        wick_shadow_ratio = _input.wick_shadow_ratio if wick_shadow_ratio == 3.0 else wick_shadow_ratio

    # 默认值处理
    if df is None or (hasattr(df, 'empty') and df.empty) or current_price <= 0:
        return _empty_result("数据无效")

    atr = df['ATR'].iloc[-1] if 'ATR' in df.columns and len(df) > 0 else current_price * 0.02
    oi_data = oi_data or []
    taker_vol = taker_vol or []
    funding_rate_history = funding_rate_history or []
    elite_position_data = elite_position_data or []
    option_oi_strike_data = option_oi_strike_data or []
    option_pcr_data = option_pcr_data or []
    position_tiers_data = position_tiers_data or []
    multi_tf_oi = multi_tf_oi or {}
    multi_tf_taker = multi_tf_taker or {}
    multi_tf_ls = multi_tf_ls or {}
    index_candles_5m = index_candles_5m or []
    index_candles_1H = index_candles_1H or []
    mark_candles_5m = mark_candles_5m or []

    # ══════════════ 核心: 7维博弈分析 ══════════════
    crowd = analyze_crowd_crowding(ls_ratio or 0, funding_rate or 0)
    elite = analyze_elite_divergence(ls_ratio or 0, elite_ratio or 0, elite_position_data)
    oi_flow = analyze_oi_flow(oi_data, df, current_price)
    taker = analyze_taker_flow(taker_vol, df)
    funding = analyze_funding(funding_rate or 0, funding_rate_history)
    elite_trend_dim = analyze_elite_trend(elite_trend_data or {}, ls_ratio or 0) if elite_trend_data else {"score": 0, "signal": ""}
    basis_tf = analyze_multi_tf_basis(current_price, index_candles_5m, index_candles_1H)

    # ══════════════ 精英面板：明确展示精英动向 ══════════════
    elite_pos_ratio = elite.get("elite_pos_ratio", 1.0) if isinstance(elite, dict) else 1.0
    elite_signal = elite.get("signal", "无") if isinstance(elite, dict) else "无"
    elite_score = elite.get("score", 0) if isinstance(elite, dict) else 0
    elite_detail = elite.get("detail", "") if isinstance(elite, dict) else ""

    elite_trend_signal = elite_trend_dim.get("signal", "无") if isinstance(elite_trend_dim, dict) else "无"
    elite_trend_score = elite_trend_dim.get("score", 0) if isinstance(elite_trend_dim, dict) else 0
    elite_trend_dirs = elite_trend_dim.get("tf_directions", {}) if isinstance(elite_trend_dim, dict) else {}
    elite_trend_detail = elite_trend_dim.get("detail", "") if isinstance(elite_trend_dim, dict) else ""

    # 仓位方向判断
    if elite_pos_ratio > 1.1:
        pos_direction = "long"
        pos_label = "多头"
    elif elite_pos_ratio < 0.9:
        pos_direction = "short"
        pos_label = "空头"
    else:
        pos_direction = "neutral"
        pos_label = "中性"

    # 综合精英总结
    if pos_direction == "long" and elite_trend_score > 0:
        elite_summary = f"🟢 精英正在做多 (仓位比 {elite_pos_ratio:.2f}) — 趋势向上"
    elif pos_direction == "short" and elite_trend_score < 0:
        elite_summary = f"🔴 精英正在做空 (仓位比 {elite_pos_ratio:.2f}) — 趋势向下"
    elif pos_direction == "long" and elite_trend_score < 0:
        elite_summary = f"🟡 精英仓多 (仓位比 {elite_pos_ratio:.2f}) 但短线趋势转弱"
    elif pos_direction == "short" and elite_trend_score > 0:
        elite_summary = f"🟡 精英仓空 (仓位比 {elite_pos_ratio:.2f}) 但短线趋势转强"
    elif elite_score != 0 and elite_trend_score != 0 and elite_score * elite_trend_score < 0:
        elite_summary = f"🟡 精英信号分歧 — 背离:{elite_score:+.1f} 多周期:{elite_trend_score:+.1f}"
    else:
        elite_summary = "⚪ 精英无显著方向"

    elite_panel = {
        "position_ratio": elite_pos_ratio,
        "position_direction": pos_direction,
        "position_label": pos_label,
        "divergence_signal": elite_signal,
        "divergence_score": elite_score,
        "divergence_detail": elite_detail,
        "trend_signal": elite_trend_signal,
        "trend_score": elite_trend_score,
        "trend_directions": elite_trend_dirs,
        "trend_detail": elite_trend_detail,
        "ls_ratio": ls_ratio or 0,
        "elite_ratio": elite_ratio or 0,
        "elite_summary": elite_summary,
    }

    # ══════════════ 综合博弈推断 ══════════════
    synthesis = synthesize_direction(
        crowd, elite, oi_flow, taker, funding,
        mark_deviation or 0, index_price or 0, current_price,
        elite_trend=elite_trend_dim,
        basis_tf=basis_tf,
        atr=atr,
    )

    # ══════════════ K线辅助检测 ══════════════
    position = detect_position(df, current_price)
    all_detections = []
    all_detections.extend(detect_volume_anomaly(df, symbol))
    all_detections.extend(detect_wick(df, symbol, wick_shadow_ratio))
    all_detections.extend(detect_stagnation(df, position, symbol))
    if position:
        all_detections.insert(0, DetectionResult(
            tag=position, symbol=symbol, timestamp=time.time(),
            confidence=0.9, detail=f"价格区间位置: {position.value}",
        ))

    # ══════════════ 阶段 + 方向综合 ══════════════
    phase_old, phase_cn, direction, confidence, summary_signals = determine_phase_and_direction(
        synthesis, position, all_detections,
    )

    # 添加核心博弈信号（7维度）
    for dim_result, dim_name in [(crowd, "散户拥挤度"), (elite, "精英背离"), (oi_flow, "OI流向"),
                                  (taker, "Taker"), (elite_trend_dim, "精英多周期"), (basis_tf, "基差")]:
        if dim_result.get("signal"):
            summary_signals.insert(0, f"[{dim_name}] {dim_result['signal']}")

    # ══════════════ 威科夫 ══════════════
    wyckoff = simplified_wyckoff(df, current_price)

    # ══════════════ 下一步预测 ══════════════
    next_move = predict_next_move_enhanced(
        direction, phase_cn, current_price, atr, synthesis, wyckoff.get("events", []),
    )

    # ══════════════ 综合点位 ══════════════
    predicted_point = calculate_manipulation_target(
        df, current_price, atr,
        {"phase": phase_old, "phase_cn": phase_cn}, next_move, ai_signal,
    )

    # ══════════════ 辅助数据 ══════════════
    oi_change_pct = oi_flow.get("oi_change_pct", 0)
    net_taker = taker.get("net_taker", 0)

    phase_result = {
        "phase": phase_old,
        "phase_cn": phase_cn,
        "score": int(synthesis["net_score"] * 10),
        "confidence": round(confidence, 2),
        "signals": summary_signals,
        "dimension_scores": {
            "精英背离": round(elite.get("score", 0), 2) if isinstance(elite, dict) else 0,
            "精英多周期": round(elite_trend_dim.get("score", 0), 2) if isinstance(elite_trend_dim, dict) else 0,
            "散户拥挤度": round(crowd.get("score", 0), 2) if isinstance(crowd, dict) else 0,
            "OI流向": round(oi_flow.get("score", 0), 2) if isinstance(oi_flow, dict) else 0,
            "Taker": round(taker.get("score", 0), 2) if isinstance(taker, dict) else 0,
            "资金费率": round(funding.get("score", 0), 2) if isinstance(funding, dict) else 0,
            "基差多周期": round(basis_tf.get("score", 0), 2) if isinstance(basis_tf, dict) else 0,
        },
        "weighted_bull": synthesis["bull_weight"],
        "weighted_bear": synthesis["bear_weight"],
        "bull_score": int(synthesis["bull_weight"] * 10),
        "bear_score": int(synthesis["bear_weight"] * 10),
        "oi_change_pct": round(oi_change_pct, 4),
        "net_taker": round(net_taker, 2),
    }

    # ══════════════ 日志输出 ══════════════
    dir_icon = {"long": "🟢", "short": "🔴", "neutral": "⚪"}
    dir_cn = {"long": "做多", "short": "做空", "neutral": "观望"}
    logger.info("=" * 70)
    logger.info(f"  🧠 庄家博弈推断 V3 | {symbol}")
    logger.info(f"  {'─' * 50}")
    logger.info(f"  📍 当前阶段: {phase_cn}")
    logger.info(f"  🎯 预测方向: {dir_icon.get(direction,'')} {dir_cn.get(direction,'')} "
                f"(多头权重:{synthesis['bull_weight']:.2f} 空头权重:{synthesis['bear_weight']:.2f})")
    logger.info(f"  📊 置信度: {confidence:.0%} | 净得分: {synthesis['net_score']:.2f}")
    logger.info(f"  {'─' * 50}")
    for dim_name, dim_score in [("精英背离", elite.get("score",0)), ("精英多周期", elite_trend_dim.get("score",0)),
                                 ("散户拥挤度", crowd.get("score",0)), ("OI流向", oi_flow.get("score",0)),
                                 ("Taker", taker.get("score",0)), ("资金费率", funding.get("score",0)),
                                 ("基差多周期", basis_tf.get("score",0))]:
        bar = "█" * int(abs(dim_score) * 10)
        sign = "+" if dim_score > 0 else "-" if dim_score < 0 else " "
        logger.info(f"  {sign} {dim_name:12s} [{bar:<10s}] {dim_score:+.2f}")
    logger.info(f"  {'─' * 50}")
    logger.info(f"  推理链: {' → '.join(synthesis['reasoning_chain'][:3])}")
    logger.info(f"  下一步: {next_move['next_action']}")
    logger.info(f"  目标价: {next_move['target_price']:.4f} | 止损: {next_move['stop_price']:.4f}")
    logger.info("=" * 70)

    return {
        "phase_result": phase_result,
        "wyckoff": wyckoff,
        "next_move": next_move,
        "predicted_point": predicted_point,
        "elite_panel": elite_panel,
    }


def _empty_result(reason: str) -> Dict:
    return {
        "phase_result": {
            "phase": "unknown", "phase_cn": reason, "score": 0, "confidence": 0,
            "signals": [], "dimension_scores": {},
            "weighted_bull": 0, "weighted_bear": 0,
            "bull_score": 0, "bear_score": 0,
            "oi_change_pct": 0, "net_taker": 0,
        },
        "wyckoff": {"schematic": "none", "current_phase": "unknown", "events": [], "detail": reason},
        "next_move": {
            "next_action": "无数据", "direction": "neutral",
            "target_price": 0, "stop_price": 0,
            "time_frame": "", "reasoning": reason, "risk_scenario": "",
        },
        "predicted_point": {
            "ensemble_target": 0, "ensemble_direction": "neutral",
            "distance_pct": 0, "confidence": 0, "detail": reason,
        },
        "elite_panel": {
            "position_ratio": 1.0, "position_direction": "neutral", "position_label": "未知",
            "divergence_signal": "无数据", "divergence_score": 0, "divergence_detail": "",
            "trend_signal": "无数据", "trend_score": 0, "trend_directions": {}, "trend_detail": "",
            "ls_ratio": 0, "elite_ratio": 0, "elite_summary": "⚪ 无数据",
        },
    }
