# signal_utils.py
"""
信号处理模块（统一入口）：
  - normalize_signal:      标准化信号格式（方向校验、补全有效期/强度/market_state）
  - resolve_signal_conflicts: 冲突仲裁（各分析维度分歧超过阈值时降级/抑制）
  - signal_post_filter:    信号后过滤（成交量确认、波动率过滤、关键位距离检查）

职责分工：
  - 各分析模块（rule_strategy / ai_analysis / manipulation / technical_analysis）
    只做特征提取和结论生成，不做过滤决策
  - 所有过滤/仲裁统一在本模块完成，由 config 全参数控制
"""
import logging
from typing import Dict, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  信号标准化
# ════════════════════════════════════════════════════════════

def normalize_signal(config, signal: Optional[Dict]) -> Dict:
    """
    统一整理 AI / 规则返回的信号：
    - 方向非法时置为 neutral
    - 非 neutral 信号补全有效期与强度
    - 补全 market_state 字段
    """
    if not signal:
        return {}

    direction = signal.get("direction")
    if direction not in ("long", "short", "neutral"):
        signal["direction"] = "neutral"

    if signal["direction"] != "neutral":
        if "validity_minutes" not in signal:
            signal["validity_minutes"] = config.DEFAULT_SIGNAL_VALIDITY
        if "strength" not in signal:
            signal["strength"] = "medium"

    if not signal.get("market_state"):
        signal["market_state"] = "未知"

    return signal


# ════════════════════════════════════════════════════════════
#  信号冲突仲裁（P0 新增）
# ════════════════════════════════════════════════════════════

def resolve_signal_conflicts(
    ai_signal: Dict,
    rule_signal: Optional[Dict] = None,
    manipulation_result: Optional[Dict] = None,
    confluence_result: Optional[Dict] = None,
    tech_batch: Optional[Dict] = None,
) -> Dict:
    """
    信号冲突仲裁器。

    仲裁规则：
      1. 各维度方向一致 → 保持原强度
      2. 存在 1 个维度分歧 → 降一级强度（strong→medium→weak→neutral）
      3. 存在 2+ 维度分歧且方向矛盾 → 强制 neutral，输出冲突日志
      4. 冲突时附加 conflict_detail 供日志记录

    返回：可能降级/置为 neutral 后的信号字典（原始信号深拷贝修改）
    """
    import copy

    signal = copy.deepcopy(ai_signal) if ai_signal else {"direction": "neutral"}
    if signal.get("direction") == "neutral":
        return signal

    # ── 收集各维度方向 ──
    directions: Dict[str, str] = {}

    if signal.get("direction"):
        directions["AI"] = signal["direction"]

    if rule_signal and rule_signal.get("direction") not in (None, "neutral"):
        directions["规则策略"] = rule_signal["direction"]

    if manipulation_result:
        phase = manipulation_result.get("phase_result", {})
        phase_name = phase.get("phase", "")
        if phase_name in ("accumulation", "markup", "shakeout"):
            # shakeout 偏多 → 但若 score<0 则实际偏空
            if phase.get("score", 0) >= 0:
                directions["操盘检测"] = "long"
            else:
                directions["操盘检测"] = "short"
        elif phase_name in ("distribution", "markdown"):
            directions["操盘检测"] = "short"

    if confluence_result and confluence_result.get("direction") not in (None, "neutral"):
        directions["多周期共振"] = confluence_result["direction"]

    if tech_batch:
        # 技术背离方向
        rsi_div = tech_batch.get("rsi_divergence", {})
        if rsi_div.get("type", "none") == "bullish_divergence":
            directions["技术面(背离)"] = "long"
        elif rsi_div.get("type", "none") == "bearish_divergence":
            directions["技术面(背离)"] = "short"
        # K线形态偏向后合并
        patterns = tech_batch.get("candlestick_patterns", [])
        bull_count = sum(1 for p in patterns if p.get("direction") == "bull")
        bear_count = sum(1 for p in patterns if p.get("direction") == "bear")
        if bull_count >= 2:
            directions["K线形态"] = "long"
        elif bear_count >= 2:
            directions["K线形态"] = "short"

    # ── 分析分歧 ──
    active_dirs = [(name, d) for name, d in directions.items() if d != "neutral"]
    if len(active_dirs) <= 1:
        return signal  # 只有一个维度有方向，无冲突

    has_long = any(d == "long" for _, d in active_dirs)
    has_short = any(d == "short" for _, d in active_dirs)

    if not (has_long and has_short):
        # 无方向矛盾，可能降级但不会抑制
        return signal

    # ── 存在方向矛盾 → 统计分歧程度 ──
    long_voters = [name for name, d in active_dirs if d == "long"]
    short_voters = [name for name, d in active_dirs if d == "short"]
    conflict_detail = {name: d for name, d in active_dirs}

    logger.warning(
        f"[信号冲突] 看多: {long_voters}  看空: {short_voters}"
    )

    # 少数派 ≥ 2票 且 少数派 ≥ 多数派×2/3 → 真正严重分歧 → neutral
    # 避免单噪声信号瘫痪整个系统（单票否决制已废除）
    total = len(long_voters) + len(short_voters)
    minority = min(len(long_voters), len(short_voters))
    majority = max(len(long_voters), len(short_voters))

    if minority >= 2 and minority >= majority * 2 / 3:
        # 严重冲突：多来源真正分歧 → 强制观望
        signal["direction"] = "neutral"
        signal["strength"] = "weak"
        signal["conflict"] = True
        signal["conflict_detail"] = conflict_detail
        signal["market_state"] = (
            signal.get("market_state", "")
            + f" | ⚠️信号分歧: 看多=[{','.join(long_voters)}] 看空=[{','.join(short_voters)}]"
        )
        logger.warning(
            f"⚠️ 信号严重冲突({minority}票反对 vs {majority}票) → 强制 neutral: {conflict_detail}"
        )
        return signal

    # 少数派不足门槛 → 按多数派方向走
    direction = "long" if len(long_voters) > len(short_voters) else "short"
    signal["direction"] = direction
    signal["conflict"] = True
    signal["conflict_detail"] = conflict_detail
    signal["market_state"] = (
        signal.get("market_state", "")
        + f" | ⚡轻度分歧(按多数): 看多=[{','.join(long_voters)}] 看空=[{','.join(short_voters)}]"
    )
    logger.info(
        f"⚡ 轻度分歧(少数={minority}/{total}) → 按多数派走 {direction}: {conflict_detail}"
    )

    # 单维度分歧 → 降级
    strength_order = {"strong": 3, "medium": 2, "weak": 1}
    current_strength = signal.get("strength", "medium")
    level = strength_order.get(current_strength, 2)
    if level > 1:
        new_level = level - 1
        new_strength = {v: k for k, v in strength_order.items()}.get(new_level, "weak")
        signal["strength"] = new_strength
        logger.info(
            f"[信号降级] 存在分歧 {conflict_detail}，强度 {current_strength} → {new_strength}"
        )

    return signal


# ════════════════════════════════════════════════════════════
#  信号后处理过滤器（从 technical_analysis.py 移入）
# ════════════════════════════════════════════════════════════

def signal_post_filter(signal: Dict, df: pd.DataFrame, current_price: float,
                       funding_rate: float = 0, mark_deviation: float = 0) -> Dict:
    """
    对信号做后处理过滤：
    1. 成交量确认：信号出现时成交量必须 > 短期均量
    2. 波动率过滤：ATR 过小（横盘）时不交易
    3. 距离过滤：入场价距关键位太近时降低置信度
    4. 🆕 资金费率极端过滤：费率>0.1%时不追多，<-0.1%时不追空
    5. 🆕 标记价偏离过滤：标记价偏离>1%时抑制信号
    6. 🆕 信号频率控制：同一方向连续N个周期都出信号 → 降低置信度

    过滤逻辑统一在此执行，各分析模块只做特征提取不做过滤。
    """
    if not signal or signal.get("direction") == "neutral":
        return signal

    latest = df.iloc[-1] if not df.empty else None
    if latest is None:
        return signal

    confidence = signal.get('confidence', 1.0)
    if not isinstance(confidence, (int, float)):
        confidence = 1.0
    warnings = []

    # 1. 成交量确认
    vol = latest.get('vol', 0)
    avg_vol = df['vol'].tail(20).mean() if 'vol' in df.columns else vol
    vol_ratio = vol / avg_vol if avg_vol > 0 else 1
    if vol_ratio < 0.6:
        confidence *= 0.5
        warnings.append(f"成交量萎缩({vol_ratio:.2f}x)")

    # 2. ATR 过滤（横盘不交易）
    atr = latest.get('ATR', 0)
    atr_pct = atr / current_price if current_price > 0 else 0
    if atr_pct < 0.002:  # ATR 不到价格的 0.2%
        warnings.append("波动率过低(横盘)")

    # 3. 距关键位的距离
    direction = signal.get("direction")
    entry = signal.get("entry", current_price)
    support = signal.get("key_support")
    resistance = signal.get("key_resistance")

    if direction == "long" and support:
        try:
            dist = abs(float(entry) - float(support)) / float(entry) if entry else 1
            if dist < 0.005:
                warnings.append(f"入场价距支撑仅 {dist*100:.2f}%")
        except (TypeError, ValueError):
            pass

    if direction == "short" and resistance:
        try:
            dist = abs(float(resistance) - float(entry)) / float(entry) if entry else 1
            if dist < 0.005:
                warnings.append(f"入场价距压力仅 {dist*100:.2f}%")
        except (TypeError, ValueError):
            pass

    # 4. 🆕 资金费率极端过滤
    abs_fr = abs(funding_rate)
    if direction == "long" and funding_rate > 0.001:  # 费率>0.1%不追多
        confidence *= 0.6
        warnings.append(f"资金费率偏高({funding_rate*100:.2f}%)，多头拥挤追多风险大")
    elif direction == "short" and funding_rate < -0.001:  # 费率<-0.1%不追空
        confidence *= 0.6
        warnings.append(f"资金费率偏低({funding_rate*100:.2f}%)，空头拥挤追空风险大")

    # 5. 🆕 标记价偏离过滤
    if abs(mark_deviation) > 0.01:  # 偏离>1%
        confidence *= 0.7
        warnings.append(f"标记价偏离{mark_deviation*100:.1f}%，期货与现货价差异常")

    # 应用降级
    if warnings:
        signal['filter_warnings'] = warnings
        signal['confidence'] = round(confidence, 2)
        logger.info(f"信号后过滤: 置信度={confidence}, 警告={warnings}")

    return signal
