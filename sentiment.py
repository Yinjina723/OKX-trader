# sentiment.py
"""市场情绪分析 —— 资金费率 + 持仓量 + 多空比，输出结构化情绪结论"""

import logging
from typing import Any, Dict, List, Optional

from okx_client import OKXClient

logger = logging.getLogger(__name__)


# ── 资金费率分析 ─────────────────────────────────────────
def _analyze_funding(data: List[Dict]) -> Dict:
    """分析资金费率：多头拥挤度、趋势变化、反转预警。"""
    if not data:
        return {"status": "无数据", "warning": ""}

    rates = [float(d.get("fundingRate", 0)) for d in data]
    if not rates:
        return {"status": "无数据", "warning": ""}

    current = rates[-1]
    avg_all = sum(rates) / len(rates)
    avg_recent = sum(rates[-5:]) / min(5, len(rates))  # 最近5期

    # 趋势判断
    half = len(rates) // 2 or 1
    first_half = rates[:half]
    second_half = rates[half:]

    avg_first = sum(first_half) / len(first_half) if first_half else 0
    avg_second = sum(second_half) / len(second_half) if second_half else 0
    trend = "上升" if avg_second > avg_first else "下降" if avg_second < avg_first else "持平"
    trend_pct = (avg_second - avg_first) / (abs(avg_first) + 1e-10) * 100 if avg_first != 0 else 0

    # 警告判断
    warnings = []
    if current > 0.001:  # 0.1% 以上
        warnings.append("多头拥挤")
    elif current < -0.001:
        warnings.append("空头拥挤")

    if trend == "上升" and current > 0:
        warnings.append("费率持续走高——多头过热风险")
    elif trend == "下降" and current < 0:
        warnings.append("费率持续走低——空头过热风险")

    # 极端值检测（最近5期全部同号+绝对值变大）
    if len(rates) >= 5:
        last5 = rates[-5:]
        if all(r > 0 for r in last5) and last5[-1] > sum(last5) / 5:
            warnings.append("多头拥挤加剧")
        elif all(r < 0 for r in last5) and last5[-1] < sum(last5) / 5:
            warnings.append("空头拥挤加剧")

    return {
        "current": round(current, 6),
        "avg_recent_5": round(avg_recent, 6),
        "avg_all": round(avg_all, 6),
        "trend": trend,
        "trend_pct": round(trend_pct, 1),
        "sample_count": len(rates),
        "summary": f"当前{current:.4%}, 近5期均值{avg_recent:.4%}, {len(rates)}期内趋势{trend}",
        "warnings": warnings,
    }


# ── 持仓量分析 ───────────────────────────────────────────
def _analyze_oi(data: List[Dict]) -> Dict:
    """分析持仓量变化趋势，判断资金流入/流出。"""
    if not data:
        return {"status": "无数据", "warning": ""}

    values = [float(d.get("oi", 0)) for d in data if d.get("oi")] or \
             [float(d.get("value", 0)) for d in data if d.get("value")] or \
             [float(d.get("oiVol", 0)) for d in data if d.get("oiVol")]
    if not values:
        return {"status": "无数据", "warning": ""}

    current = values[-1]
    # 30天变化（或最近一半）
    window = min(30, len(values) - 1)
    if window > 0:
        prev = values[-(window + 1)]
        change_30d = (current - prev) / (abs(prev) + 1e-10) * 100 if prev > 0 else 0
    else:
        change_30d = 0

    # 趋势
    half = len(values) // 2 or 1
    avg_first = sum(values[:half]) / half
    avg_second = sum(values[half:]) / (len(values) - half) if len(values) - half > 0 else avg_first
    trend = "上升" if avg_second > avg_first * 1.02 else "下降" if avg_second < avg_first * 0.98 else "横盘"
    trend_pct = (avg_second - avg_first) / (abs(avg_first) + 1e-10) * 100

    # 解读
    interpretation = ""
    if trend == "上升":
        interpretation = "持仓增加——资金流入，趋势可能延续"
    elif trend == "下降":
        interpretation = "持仓减少——资金流出，趋势可能减弱"
    else:
        interpretation = "持仓横盘——市场观望"

    return {
        "current": round(current, 0),
        "change_30d_pct": round(change_30d, 1),
        "trend": trend,
        "trend_pct": round(trend_pct, 1),
        "sample_count": len(values),
        "interpretation": interpretation,
        "summary": f"当前{current:,.0f}, 30日变化{change_30d:+.1f}%, 趋势{trend}",
    }


# ── 多空比分析 ───────────────────────────────────────────
def _analyze_ls_ratio(data: List[Dict]) -> Dict:
    """分析多空人数比，检测散户极端情绪。"""
    if not data:
        return {"status": "无数据", "warning": ""}

    # 多空比可能在多个字段中
    longs = [float(d.get("longAccount", 0)) for d in data]
    shorts = [float(d.get("shortAccount", 0)) for d in data]

    if not longs or not shorts or sum(longs) + sum(shorts) == 0:
        return {"status": "无数据", "warning": ""}

    ratios = [l / (s + 1e-10) for l, s in zip(longs, shorts)]
    current = ratios[-1]
    avg_30 = sum(ratios[-30:]) / min(30, len(ratios))

    # 趋势
    half = len(ratios) // 2 or 1
    avg_first = sum(ratios[:half]) / half
    avg_second = sum(ratios[half:]) / (len(ratios) - half)
    trend = "偏多" if avg_second > avg_first else "偏空" if avg_second < avg_first else "持平"

    # 极端判断
    warnings = []
    if current > 2.0:
        warnings.append("极度偏多——散户狂热，警惕反转做空")
    elif current > 1.5:
        warnings.append("偏多——散户偏乐观")
    if current < 0.7:
        warnings.append("极度偏空——散户恐慌，关注反弹机会")
    elif current < 0.9:
        warnings.append("偏空——散户偏悲观")

    return {
        "current_ratio": round(current, 2),
        "avg_30d": round(avg_30, 2),
        "trend": trend,
        "sample_count": len(ratios),
        "summary": f"当前{current:.2f}:1, 30日均值{avg_30:.2f}:1, 趋势{trend}",
        "warnings": warnings,
    }


# ── 综合情绪分析 ─────────────────────────────────────────
def analyze_sentiment(client: OKXClient, symbol: str) -> Dict[str, Any]:
    """
    拉取资金费率、OI、多空比数据，分析市场情绪。

    返回: {
        funding: {...},
        oi: {...},
        ls_ratio: {...},
        overall_bias: "bullish"|"bearish"|"mixed"|"insufficient",
        summary_text: "一段中文综合描述",
    }
    """
    logger.info(f"获取 {symbol} 情绪数据...")

    # 并行获取（顺序请求）
    funding_raw = client.get_funding_rate_history(symbol, limit=90) or []
    oi_raw = client.get_open_interest(symbol, period="1D", limit=90) or []
    ls_raw = client.get_long_short_ratio(symbol, period="1D", limit=90) or []

    funding = _analyze_funding(funding_raw)
    oi = _analyze_oi(oi_raw)
    ls_ratio = _analyze_ls_ratio(ls_raw)

    # ── 综合偏向 ──
    bull_points = 0
    bear_points = 0
    data_count = 0

    # 费率
    if funding.get("current") is not None:
        data_count += 1
        if funding["current"] > 0.0005:
            bull_points += 1
        elif funding["current"] < -0.0005:
            bear_points += 1

    # OI
    if oi.get("trend") and oi["trend"] != "横盘":
        data_count += 1
        if oi["trend"] == "上升":
            bull_points += 1
        else:
            bear_points += 1

    # 多空比
    if ls_ratio.get("current_ratio") is not None:
        data_count += 1
        ratio = ls_ratio["current_ratio"]
        if ratio > 1.5:
            bull_points += 1
        elif ratio < 0.9:
            bear_points += 1

    overall = "mixed"
    if data_count >= 2:
        if bull_points >= 2:
            overall = "bullish"
        elif bear_points >= 2:
            overall = "bearish"
    elif data_count == 0:
        overall = "insufficient"

    # ── 综合描述文本 ──
    lines = []
    if funding.get("summary"):
        lines.append(f"资金费率: {funding['summary']}")
    if oi.get("summary"):
        lines.append(f"持仓量: {oi['summary']}")
    if ls_ratio.get("summary"):
        lines.append(f"多空比: {ls_ratio['summary']}")

    # 收集警告
    all_warnings = []
    for section in [funding, oi, ls_ratio]:
        if isinstance(section, dict) and section.get("warnings"):
            all_warnings.extend(section["warnings"])

    bias_cn = {"bullish": "偏多", "bearish": "偏空", "mixed": "多空交织", "insufficient": "数据不足"}
    summary_parts = [f"综合情绪: {bias_cn.get(overall, 'mixed')}"]
    summary_parts.extend(lines)

    logger.info(f"情绪分析完成: {bias_cn.get(overall, 'mixed')}")

    return {
        "funding": funding,
        "oi": oi,
        "ls_ratio": ls_ratio,
        "overall_bias": overall,
        "summary_text": "\n".join(summary_parts),
        "warnings": all_warnings,
    }
