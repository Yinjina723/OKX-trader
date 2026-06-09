# ai_analysis.py
"""日线 AI 分析 —— 调用 DeepSeek API 综合 K 线 + 指标 + 操盘结果给出交易建议"""

import json
import logging
import re
from typing import Dict, List

import numpy as np
import pandas as pd
from openai import OpenAI

from config import Config

logger = logging.getLogger(__name__)

def _build_system_prompt(rr: float) -> str:
    """构建系统提示词 —— 职业自营交易员 / 机构操盘手视角"""
    return """# 角色定义
你是一名职业加密货币自营交易员（Prop Trader），管理自有资金，没有外部LP的赎回压力。
你的唯一优势在于：市场结构的精准识别、对手盘心理的捕捉、非对称赔率机会的把握。
你不追求交易频率，只追求单笔交易的质量和盈亏比。

# 分析框架

## 1. 多时间框架结构分析（自上而下）
- 日线定方向：HH/HL序列 = 上升趋势，LH/LL序列 = 下降趋势，无明确序列 = 盘整
- 4H/1H 定入场：在日线方向前提下，在小级别找回调/反弹结构位
- 拒绝在日内追价 —— 等价格回到结构位再动手
- 日线收盘突破箱体视为有效，日内假突破（收盘回到箱体内）= 流动性猎杀

## 2. 流动性分析（关键价位识别）
- 前期N日最高/最低点是流动性磁铁 —— 大量止损单聚集于此
- 均线密集区（MA5/MA10/MA30/MA60交汇）= 多空博弈带，突破/跌破后常引发加速行情
- 日内VWAP = 机构的大资金平均成本，价格偏离VWAP>3% = 短期情绪极端
- 布林带上轨/下轨 = 统计学极端位置，触及后回归概率>70%

## 3. 量价关系（拒绝无量的假突破）
- 放量突破结构位 = 强势确认（多方/空方真金白银在打）
- 缩量到关键位 = 蓄力 or 衰竭？需要结合OI判断
- 量价背离（价涨量缩 / 价跌量缩）= 趋势动能衰减，反转概率升高
- 插针+放量+快速回归 = 流动性猎杀（大概率是反向入场机会）
- 连续小阳线+缩量 = 吸筹痕迹；连续小阴线+放量 = 出货痕迹

## 4. 对手盘心理（反向指标 —— 散户一致性 = 反向信号）
- 资金费率 > +0.05%/8h 且 RSI > 70 → 多头拥挤，做空窗口
- 资金费率 < -0.05%/8h 且 RSI < 30 → 空头拥挤，做多窗口
- 多空人数比 > 3:1 → 散户极度看多，反向操作胜率显著提高
- OI（持仓量）在价格下跌时上升 = 空头在加仓 = 空头拥挤 = 潜在轧空(short squeeze)
- OI在价格上涨时上升 = 多头在加仓 = 多头拥挤 = 潜在踩踏(long squeeze)
- OI下降 + 价格突破 = 一方溃败认输，趋势延续（强信号）
- 资金费率中性 + OI稳定 + 价格在均线区震荡 = 蓄力阶段，等待突破

## 5. 庄家/做市商行为痕迹（威科夫 Wyckoff 视角）
- Spring（弹簧）= 跌破支撑后迅速收回 = 假跌破吸筹 → 看涨
- Upthrust（上扬冲）= 突破阻力后迅速回落 = 假突破出货 → 看跌
- 收盘回到前箱体内 = 假突破确认，反向入场信号
- 连续缩量小K线在支撑附近 = 吸筹区（Accumulation）
- 连续放量小K线在阻力附近 = 派发区（Distribution）
- 长下影线（下影/实体>2）= 买方拒绝更低价，潜在支撑
- 长上影线（上影/实体>2）= 卖方拒绝更高价，潜在阻力

## 6. 风险收益比（盈亏比）计算 —— 核心纪律
- 止损必须设在结构位外侧（前低下方 / 前高上方 + 1~2倍ATR缓冲）
- 止盈1(take_profit1) = 最近的结构位（保守目标，高概率触及）
- 止盈2(take_profit2) = 更远的结构位（激进目标，低概率但大赔率）
- 盈亏比 < 1.5:1 的交易直接拒绝 —— 放弃，不做
- 如果结构混乱、没有清晰的S/R位，给出 neutral —— 宁可错过不可做错

## 7. 进场时机判断
- 不要追突破 —— 等回踩确认后再入场
- 做多理想入场：价格回到支撑区 + 小级别出现反转K线（锤子线/看涨吞没）+ RSI背离
- 做空理想入场：价格回到阻力区 + 小级别出现反转K线（流星线/看跌吞没）+ RSI背离
- 如果当前价格已大幅移动（日涨跌>8%），优先考虑反向或观望
- 最好在当天开盘附近或回踩均线时入场（VWAP附近=合理成本）

## 8. 输出要求
- direction 必须基于多证据交叉验证，至少3条独立逻辑支持
- entry 必须是当前收盘价附近可执行的结构位挂单价（不是已穿越的历史价格）
- stop_loss 必须放在结构破坏位（触发止损 = 你的分析被市场明确证伪）
- take_profit1 优先于 take_profit2（更保守、更可能触及）
- 没把握就 neutral —— 空仓也是一种仓位，而且是最安全的仓位
- 永远不要在价格已经大幅移动后追单

# JSON输出格式（只输出JSON，不要任何推理文字）
{"direction":"long|short|neutral","entry":挂单价（6位小数）,"stop_loss":止损价,"take_profit1":保守止盈,"take_profit2":激进止盈,"strength":"strong|medium|weak","tomorrow_prediction":"简述：多时间框架结构判断+关键矛盾信号+风险提示（中文，50字内）","key_support":关键支撑价或null,"key_resistance":关键阻力价或null}"""


def analyze_daily_with_ai(
    config: Config,
    symbol: str,
    df: pd.DataFrame,
    yesterday_idx: int,
    manipulation: Dict = None,
    patterns: List[Dict] = None,
    divergence: Dict = None,
    ma_alignment: Dict = None,
    sentiment: Dict = None,
) -> Dict:
    """
    调用 DeepSeek 对日线做综合分析。

    返回: {direction, entry, stop_loss, take_profit1, take_profit2, strength, market_state, ...}
    返回 {} 表示调用失败或无密钥。
    """
    if not config.DEEPSEEK_API_KEY:
        logger.warning("DeepSeek API 密钥未配置，跳过 AI 分析")
        return {"direction": "neutral", "market_state": "AI未配置", "ai_unavailable": True}

    if df.empty or yesterday_idx >= len(df) or yesterday_idx < 0:
        return {"direction": "neutral", "market_state": "数据不足"}

    yesterday = df.iloc[yesterday_idx]
    latest = df.iloc[-1]

    # ── 构建提示词 ──
    prompt = _build_daily_prompt(symbol, df, yesterday_idx, latest,
                                  manipulation, patterns, divergence, ma_alignment, sentiment)

    # ── 调用 AI ──
    client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    try:
        logger.info(f"请求 DeepSeek 日线分析 {symbol}...")
        messages = [
            {"role": "system", "content": _build_system_prompt(config.TAKE_PROFIT_RR)},
            {"role": "user", "content": prompt},
        ]
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=config.AI_TEMPERATURE,
            max_tokens=config.AI_MAX_TOKENS,
            stream=False,
        )
        if not response.choices:
            logger.error("AI 返回空 choices")
            return {"direction": "neutral", "market_state": "AI返回空"}

        content = response.choices[0].message.content
        logger.info(f"AI 原始返回: {content[:300]}...")

        json_str = _extract_json(content)
        if json_str:
            result = json.loads(json_str)
            # 安全转换数值字段（AI 可能返回字符串类型的数字）
            for key in ("entry", "stop_loss", "take_profit1", "take_profit2",
                        "key_support", "key_resistance"):
                val = result.get(key)
                if val is not None and not isinstance(val, (int, float)):
                    try:
                        result[key] = float(val)
                    except (ValueError, TypeError):
                        result[key] = None
            logger.info(f"AI 分析结果: direction={result.get('direction')} "
                        f"strength={result.get('strength')}")
            return result
        else:
            logger.warning("AI 返回中未找到 JSON")
            return {"direction": "neutral", "market_state": "AI解析失败"}

    except Exception as e:
        logger.exception(f"AI 调用失败: {e}")
        return {"direction": "neutral", "market_state": f"AI异常: {e}"}


def _build_daily_prompt(
    symbol: str,
    df: pd.DataFrame,
    yesterday_idx: int,
    latest,
    manipulation: Dict = None,
    patterns: List[Dict] = None,
    divergence: Dict = None,
    ma_alignment: Dict = None,
    sentiment: Dict = None,
) -> str:
    """构建专业操盘手视角的日线分析提示词。"""
    yesterday = df.iloc[yesterday_idx]
    close_y = float(yesterday["close"]); open_y = float(yesterday["open"])
    high_y  = float(yesterday["high"]); low_y = float(yesterday["low"])
    vol_y   = float(yesterday.get("vol", 0))
    change_pct = (close_y - open_y) / open_y * 100 if open_y > 0 else 0
    amplitude = (high_y - low_y) / low_y * 100 if low_y > 0 else 0

    # ── 近10日结构（HH/HL或LL/LH序列）──
    n_lookback = min(10, len(df) - 1)
    recent_highs = df["high"].iloc[max(0, yesterday_idx - n_lookback):yesterday_idx + 1].astype(float)
    recent_lows  = df["low"].iloc[max(0, yesterday_idx - n_lookback):yesterday_idx + 1].astype(float)
    recent_volumes = df["vol"].iloc[max(0, yesterday_idx - n_lookback):yesterday_idx + 1].astype(float)
    max_10d = float(recent_highs.max())
    min_10d = float(recent_lows.min())
    avg_vol = float(recent_volumes.mean()) if len(recent_volumes) > 0 else 0

    # 趋势判断
    trend = "盘整"
    if len(recent_highs) >= 4 and len(recent_lows) >= 4:
        hh = recent_highs.iloc[-1] > recent_highs.iloc[-4:].max()
        hl = recent_lows.iloc[-1] > recent_lows.iloc[-4:].min()
        lh = recent_highs.iloc[-1] < recent_highs.iloc[-4:].max()
        ll = recent_lows.iloc[-1] < recent_lows.iloc[-4:].min()
        if hh and hl: trend = "上升趋势（Higher High + Higher Low）"
        elif ll and lh: trend = "下降趋势（Lower Low + Lower High）"
        else: trend = "盘整/结构转换中"

    # 近5日涨跌
    prev = df["close"].iloc[max(0, yesterday_idx - 4):yesterday_idx + 1].astype(float)
    chg_5 = [round((prev.iloc[i] - prev.iloc[i - 1]) / prev.iloc[i - 1] * 100, 2)
             for i in range(1, len(prev))] if len(prev) >= 2 else []

    # ── 指标 ──
    rsi = float(yesterday.get("RSI14", 50))
    macd_h = float(yesterday.get("MACD_Hist", 0))
    ma5 = float(yesterday.get("MA5", 0)); ma10 = float(yesterday.get("MA10", 0))
    ma30 = float(yesterday.get("MA30", 0)); ma60 = float(yesterday.get("MA60", 0))
    atr = float(yesterday.get("ATR", 0))
    bb_pos = float(yesterday.get("BB_position", 0.5))
    bb_up = float(yesterday.get("BB_upper", 0)); bb_low = float(yesterday.get("BB_lower", 0))

    # RSI 状态描述
    rsi_state = "极度超买(>80)，追多极度危险" if rsi > 80 else \
                "超买(>70)，多头拥挤" if rsi > 70 else \
                "极度超卖(<20)，追空极度危险" if rsi < 20 else \
                "超卖(<30)，空头拥挤" if rsi < 30 else "中性区间"

    # 量能判断
    vol_ratio = vol_y / avg_vol if avg_vol > 0 else 1
    vol_desc = "放量" if vol_ratio > 1.5 else "缩量" if vol_ratio < 0.5 else "正常"
    candle_desc = "大阳线" if change_pct > amplitude * 0.6 else \
                  "大阴线" if change_pct < -amplitude * 0.6 else \
                  "十字星/犹豫" if abs(change_pct) < amplitude * 0.15 else \
                  "小阳" if change_pct > 0 else "小阴"

    lines = [
        f"【{symbol} 日线作战图】",
        "",
        f"·· 市场结构 ··",
        f"昨日K线: O={open_y:.6f} H={high_y:.6f} L={low_y:.6f} C={close_y:.6f}",
        f"类型: {candle_desc} · 实体{change_pct:+.2f}% · 振幅{amplitude:.2f}% · {vol_desc}(量比{vol_ratio:.1f}x)",
        f"10日趋势: {trend}",
        f"近5日涨跌序列: {chg_5}",
    ]

    # ── 关键价位区 ──
    lines.append("")
    lines.append(f"·· 关键战场（10日价格记忆区）··")
    lines.append(f"箱体上沿(10日最高): {max_10d:.6f}")
    lines.append(f"箱体下沿(10日最低): {min_10d:.6f}")
    # 距关键位的距离
    dist_to_support = (close_y - min_10d) / min_10d * 100 if min_10d > 0 else 0
    dist_to_resistance = (max_10d - close_y) / close_y * 100 if close_y > 0 else 0
    lines.append(f"距支撑: {dist_to_support:.1f}%（{'远，回踩空间大' if dist_to_support > 5 else '近，可能在支撑附近' if dist_to_support > 1 else '紧贴支撑！' if dist_to_support > 0 else '已跌破支撑！'}）")
    lines.append(f"距阻力: {dist_to_resistance:.1f}%（{'远，上行空间大' if dist_to_resistance > 5 else '近，面临阻力考验' if dist_to_resistance > 1 else '紧贴阻力！' if dist_to_resistance > 0 else '已突破阻力！'}）")
    # VWAP偏离
    vwap_yesterday = float(yesterday.get("VWAP", 0))
    if vwap_yesterday > 0:
        vwap_deviation = (close_y - vwap_yesterday) / vwap_yesterday * 100
        lines.append(f"VWAP偏离: {vwap_deviation:+.2f}%（机构平均成本偏离，>{3:.0f}%=情绪极端）")
    if ma_alignment and ma_alignment.get("detail"):
        lines.append(f"均线排布: {ma_alignment['detail']}")
    lines.append(f"MA5={ma5:.6f}  MA10={ma10:.6f}  MA30={ma30:.6f}  MA60={ma60:.6f}")
    lines.append(f"布林带上轨={bb_up:.6f}  下轨={bb_low:.6f}  当前位置={bb_pos:.1%}")

    # ── 动量和背离 ──
    lines.append("")
    lines.append(f"·· 动量仪表 ··")
    lines.append(f"RSI(14)={rsi:.1f} — {rsi_state}")
    lines.append(f"MACD柱={macd_h:.6f} — {'多头动能' if macd_h > 0 else '空头动能'}")
    lines.append(f"ATR(14)={atr:.6f}（日内波动基准）")
    if patterns:
        lines.append(f"K线形态: {', '.join(p['name'] for p in patterns[:5])}")
    rsi_div = divergence.get("rsi", {}).get("detail", "") if divergence else ""
    macd_div = divergence.get("macd", {}).get("detail", "") if divergence else ""
    if rsi_div or macd_div:
        lines.append(f"背离信号: RSI={rsi_div}  MACD={macd_div}")

    # ── 资金和市场情绪（反向指标）──
    lines.append("")
    lines.append("·· 对手盘心理（⚠ 极端值=反向指标）··")
    if sentiment:
        funding = sentiment.get("funding", {})
        oi = sentiment.get("oi", {})
        ls_ratio = sentiment.get("ls_ratio", {})
        lines.append(f"综合偏向: {sentiment.get('overall_bias','?')}")
        if funding.get("current") is not None:
            lines.append(f"资金费率: {funding.get('summary','')}")
            if funding.get("warnings"):
                lines.append(f"  ❗ {', '.join(funding['warnings'])}")
        if oi.get("current") is not None:
            lines.append(f"持仓量(OI): {oi.get('summary','')} · {oi.get('interpretation','')}")
            # OI趋势 + 价格联动判断
            oi_trend = oi.get("trend", "")
            if oi_trend:
                price_trend = "涨" if chg_5 and sum(chg_5) > 0 else "跌" if chg_5 and sum(chg_5) < 0 else "盘"
                if oi_trend == "上升" and price_trend == "涨":
                    lines.append(f"  ⚠ OI上升+价格涨=多头持续加仓，拥挤度升高")
                elif oi_trend == "上升" and price_trend == "跌":
                    lines.append(f"  ⚠ OI上升+价格跌=空头加仓，潜在轧空")
                elif oi_trend == "下降" and price_trend == "涨":
                    lines.append(f"  ✓ OI下降+价格涨=空头溃败认输，趋势延续")
                elif oi_trend == "下降" and price_trend == "跌":
                    lines.append(f"  ✓ OI下降+价格跌=多头溃败认输，趋势延续")
        if ls_ratio.get("current_ratio") is not None:
            lines.append(f"多空人数比: {ls_ratio.get('summary','')}")
            if ls_ratio.get("warnings"):
                lines.append(f"  ❗ {', '.join(ls_ratio['warnings'])}")
        if sentiment.get("warnings"):
            lines.append(f"综合预警: {'; '.join(sentiment['warnings'])}")
    else:
        lines.append("(无情绪数据)")

    # ── 聪明钱痕迹 ──
    lines.append("")
    lines.append("·· 聪明钱痕迹（机构/做市商行为）··")
    if manipulation:
        ph = manipulation.get("phase_result", {})
        wy = manipulation.get("wyckoff", {})
        lines.append(f"庄家阶段: {ph.get('phase_cn','?')} — 置信度{ph.get('confidence',0):.0%}")
        if wy.get("events"):
            lines.append(f"威科夫事件: {'; '.join(wy['events'])}")
        wicks = manipulation.get("wicks", [])
        if wicks:
            lines.append(f"历史流动性猎杀({len(wicks)}次): {'; '.join(wicks[-3:])}")
    else:
        lines.append("(无庄家分析数据)")

    lines.extend([
        "",
        "·· 你的作战计划 ··",
        "基于以上信息，以操盘手视角判断：今天是否存在高胜率交易机会？",
        "如果有，给出精确的入场挂单价、止损位、止盈目标。没把握就 neutral。",
        "",
        "JSON:",
    ])

    return "\n".join(lines)


def _extract_json(text: str) -> str | None:
    """从文本中提取第一个完整 JSON 对象。"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def analyze_with_two_timeframes(
    config: Config,
    symbol: str,
    df: pd.DataFrame,
    yesterday_idx: int,
    hourly_df: pd.DataFrame,
    manipulation: Dict = None,
    patterns: List[Dict] = None,
    divergence: Dict = None,
    ma_alignment: Dict = None,
    sentiment: Dict = None,
) -> Dict:
    """双时间框架分析：日线定方向 + 1H定执行，一次AI调用直接给出基于现价的精确点位。

    Args:
        config: 配置对象
        symbol: 交易对
        df: 日线DataFrame（含技术指标）
        yesterday_idx: 最近完整日线的索引
        hourly_df: 今日1H K线DataFrame，必须已排序
        manipulation, patterns, divergence, ma_alignment, sentiment: 日线分析上下文

    Returns:
        同 analyze_daily_with_ai 格式的 dict，或 {} 表示失败
    """
    if not config.DEEPSEEK_API_KEY:
        logger.warning("DeepSeek API 密钥未配置，跳过双时间框架分析")
        return {"direction": "neutral", "market_state": "AI未配置", "ai_unavailable": True}

    if df.empty or yesterday_idx >= len(df) or yesterday_idx < 0:
        return {"direction": "neutral", "market_state": "日线数据不足"}

    if hourly_df is None or hourly_df.empty:
        logger.warning("无1H数据，回退到纯日线分析")
        return analyze_daily_with_ai(
            config, symbol, df, yesterday_idx,
            manipulation=manipulation, patterns=patterns,
            divergence=divergence, ma_alignment=ma_alignment,
            sentiment=sentiment,
        )

    # ── 层1: 日线结构摘要 ──
    daily_context = _summarize_daily_structure(df, yesterday_idx)
    yesterday_close = daily_context["close"]

    # ── 层2: 今日1H盘中走势 ──
    hourly_context = _format_hourly_context(hourly_df)
    current_price = hourly_context["current_price"]
    today_open = hourly_context["today_open"]
    today_change = hourly_context["today_change_pct"]
    hours_count = hourly_context["hours_count"]
    hourly_table = hourly_context["table"]
    vol_summary = hourly_context["vol_summary"]

    # ── 构建双时间框架提示词 ──
    lines = [
        f"你是职业加密货币交易员，管理自营资金。今天你需要结合日线结构（宏观方向）和1H盘中走势（微观执行）做出交易决策。",
        "",
        f"【{symbol} 双时间框架作战图】",
        "",
        f"·· 层1：日线结构（宏观——它告诉你该往哪走）··",
        f"昨日收盘: {yesterday_close:.6f}",
        f"10日趋势: {daily_context['trend']}",
        f"关键支撑: {daily_context['support']}（来源: {daily_context['support_source']}）",
        f"关键阻力: {daily_context['resistance']}（来源: {daily_context['resistance_source']}）",
        f"RSI(14): {daily_context['rsi']:.1f} — {daily_context['rsi_state']}",
        f"MACD柱: {daily_context['macd_hist']:.6f} — {'多头动能' if daily_context['macd_hist'] > 0 else '空头动能'}",
        f"均线排列: {daily_context['ma_alignment']}",
        f"布林带位置: {daily_context['bb_pos']:.1%}（上轨{daily_context['bb_upper']:.6f} / 下轨{daily_context['bb_lower']:.6f}）",
        f"昨日K线: {daily_context['candle_desc']} · 量能: {daily_context['vol_desc']}",
        f"近5日涨跌: {daily_context['chg_5']}",
    ]

    # 指标详情
    if patterns:
        lines.append(f"K线形态: {', '.join(p['name'] for p in patterns[:5])}")
    if divergence:
        rsi_div = divergence.get("rsi", {}).get("detail", "")
        macd_div = divergence.get("macd", {}).get("detail", "")
        if rsi_div or macd_div:
            lines.append(f"背离信号: RSI={rsi_div}  MACD={macd_div}")

    # 情绪数据
    lines.append("")
    lines.append("·· 对手盘心理 ··")
    if sentiment:
        funding = sentiment.get("funding", {})
        lines.append(f"综合偏向: {sentiment.get('overall_bias','?')}")
        if funding.get("summary"):
            lines.append(f"资金费率: {funding['summary']}")
            if funding.get("warnings"):
                lines.append(f"  ⚠ {', '.join(funding['warnings'])}")
    else:
        lines.append("(无情绪数据)")

    # 庄家分析
    lines.append("")
    lines.append("·· 聪明钱痕迹 ··")
    if manipulation:
        ph = manipulation.get("phase_result", {})
        wy = manipulation.get("wyckoff", {})
        lines.append(f"庄家阶段: {ph.get('phase_cn','?')} — 置信度{ph.get('confidence',0):.0%}")
        if wy.get("events"):
            lines.append(f"威科夫事件: {'; '.join(wy['events'])}")
        wicks = manipulation.get("wicks", [])
        if wicks:
            lines.append(f"历史流动性猎杀({len(wicks)}次): {'; '.join(wicks[-3:])}")
    else:
        lines.append("(无庄家分析数据)")

    # ── 1H 盘中走势 ──
    lines.append("")
    lines.append(f"·· 层2：今日1H盘中走势（微观——它告诉你现在在哪、怎么进场）··")
    lines.append(f"今日开盘: {today_open:.6f} → 当前价: {current_price:.6f}（{today_change:+.2f}%）")
    lines.append(f"已走 {hours_count} 小时 / 24小时")
    lines.append(f"量能特征: {vol_summary}")
    lines.append("")
    lines.append("今日逐小时K线:")
    lines.append(hourly_table)

    # ── 任务 ──
    lines.append("")
    lines.append("·· 你的作战任务（操盘手视角）··")
    lines.append(f"结合日线结构和今日1H盘中走势，给出可执行的交易计划：")
    lines.append("")
    lines.append("1. 验证：日线方向在1H走势中是否得到验证？")
    lines.append("   - 日线看涨 + 今日持续推高 → 方向确认，但注意是否已到阻力位")
    lines.append("   - 日线看涨 + 今日反转向下 → 方向矛盾，降低置信度，检查是否是洗盘")
    lines.append("   - 日线看跌 + 今日持续下跌 → 方向确认，但注意是否已到支撑位")
    lines.append("   - 日线看跌 + 今日反转向上 → 方向矛盾，检查是否是空头回补/诱多")
    lines.append("")
    lines.append(f"2. 入场价：必须基于当前价 {current_price:.6f} 附近的结构位")
    lines.append("   - 做多 → entry 设在1H级别支撑位（等回踩），不要在拉升中追高")
    lines.append("   - 做空 → entry 设在1H级别阻力位（等反弹），不要在下跌中追低")
    lines.append("   - 如果当前价已在关键位 → 可以直接在当前价附近挂单")
    lines.append(f"   - 如果今日已大幅移动（>{abs(today_change):.0f}%），优先考虑等回调/反弹到结构位再入场")
    lines.append("")
    lines.append("3. 止损：设在1H级别的结构位外侧（前低下方/前高上方 + ATR缓冲）")
    lines.append("4. 止盈1：保守目标，参考1H级别的下一阻力/支撑")
    lines.append("5. 止盈2：激进目标，参考日线级别的关键阻力/支撑")
    lines.append("")
    lines.append("6. 如果找不到清晰的入场结构或赔率不够 → neutral（这不丢人）")
    lines.append("")
    lines.append("只输出 JSON（不输出任何推理文字）：")
    lines.append('{"direction":"long|short|neutral","entry":入场挂单价,"stop_loss":止损价,"take_profit1":止盈1,"take_profit2":止盈2,"strength":"strong|medium|weak","tomorrow_prediction":"简述：日线结构+1H验证+入场逻辑+风险提示","key_support":价格或null,"key_resistance":价格或null}')

    prompt = "\n".join(lines)

    # ── 调用 DeepSeek ──
    try:
        client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
        logger.info(f"请求 DeepSeek 双时间框架分析 {symbol} (日线+{hours_count}根1H)...")

        system_prompt = _build_system_prompt(config.TAKE_PROFIT_RR)
        # 追加双时间框架专属的纪律
        system_prompt += (
            "\n双时间框架专属纪律："
            "\n- entry 必须基于当前价给出的合理挂单价，绝不写已被今日走势穿越的价格"
            "\n- 今日大幅移动后，不要追单——等回踩/反弹到结构位再挂单"
            "\n- 如果今天已涨>10%且到阻力位 → 优先考虑做空或观望"
            "\n- 如果今天已跌>10%且到支撑位 → 优先考虑做多或观望"
        )

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=config.AI_TEMPERATURE,
            max_tokens=config.AI_MAX_TOKENS,
            stream=False,
        )

        if not response.choices:
            logger.error("双时间框架AI返回空")
            return {"direction": "neutral", "market_state": "AI返回空"}

        content = response.choices[0].message.content
        logger.info(f"双时间框架AI返回: {content[:300]}...")

        json_str = _extract_json(content)
        if json_str:
            result = json.loads(json_str)
            for key in ("entry", "stop_loss", "take_profit1", "take_profit2",
                        "key_support", "key_resistance"):
                val = result.get(key)
                if val is not None and not isinstance(val, (int, float)):
                    try:
                        result[key] = float(val)
                    except (ValueError, TypeError):
                        result[key] = None
            # 标记为双时间框架分析
            result["_analysis_mode"] = "dual_timeframe"
            result["_current_price"] = current_price
            result["_today_change_pct"] = round(today_change, 2)
            logger.info(f"双时间框架结果: direction={result.get('direction')} "
                        f"entry={result.get('entry')}")
            return result
        else:
            logger.warning("双时间框架AI返回中未找到JSON，回退日线分析")
            return analyze_daily_with_ai(
                config, symbol, df, yesterday_idx,
                manipulation=manipulation, patterns=patterns,
                divergence=divergence, ma_alignment=ma_alignment,
                sentiment=sentiment,
            )

    except Exception as e:
        logger.exception(f"双时间框架分析异常: {e}")
        return {"direction": "neutral", "market_state": f"双时间框架异常: {e}"}


def _summarize_daily_structure(df: pd.DataFrame, day_idx: int) -> Dict:
    """从日线DataFrame中提取结构化摘要（纯计算，不调AI）。"""
    yesterday = df.iloc[day_idx]
    close_y = float(yesterday["close"])
    open_y = float(yesterday["open"])
    high_y = float(yesterday["high"])
    low_y = float(yesterday["low"])
    vol_y = float(yesterday.get("vol", 0))
    change_pct = (close_y - open_y) / open_y * 100 if open_y > 0 else 0
    amplitude = (high_y - low_y) / low_y * 100 if low_y > 0 else 0

    # 近10日结构
    n_lookback = min(10, len(df) - 1)
    recent_highs = df["high"].iloc[max(0, day_idx - n_lookback):day_idx + 1].astype(float)
    recent_lows = df["low"].iloc[max(0, day_idx - n_lookback):day_idx + 1].astype(float)
    recent_volumes = df["vol"].iloc[max(0, day_idx - n_lookback):day_idx + 1].astype(float)
    max_10d = float(recent_highs.max())
    min_10d = float(recent_lows.min())
    avg_vol = float(recent_volumes.mean()) if len(recent_volumes) > 0 else 0

    # 趋势
    trend = "盘整/结构转换中"
    if len(recent_highs) >= 4 and len(recent_lows) >= 4:
        hh = recent_highs.iloc[-1] > recent_highs.iloc[-4:].max()
        hl = recent_lows.iloc[-1] > recent_lows.iloc[-4:].min()
        lh = recent_highs.iloc[-1] < recent_highs.iloc[-4:].max()
        ll = recent_lows.iloc[-1] < recent_lows.iloc[-4:].min()
        if hh and hl:
            trend = "上升趋势（Higher High + Higher Low）"
        elif ll and lh:
            trend = "下降趋势（Lower Low + Lower High）"

    # 近5日涨跌
    prev = df["close"].iloc[max(0, day_idx - 4):day_idx + 1].astype(float)
    chg_5 = [round((prev.iloc[i] - prev.iloc[i - 1]) / prev.iloc[i - 1] * 100, 2)
             for i in range(1, len(prev))] if len(prev) >= 2 else []

    # 指标
    rsi = float(yesterday.get("RSI14", 50))
    macd_h = float(yesterday.get("MACD_Hist", 0))
    ma5 = float(yesterday.get("MA5", 0))
    ma10 = float(yesterday.get("MA10", 0))
    ma30 = float(yesterday.get("MA30", 0))
    ma60 = float(yesterday.get("MA60", 0))
    bb_pos = float(yesterday.get("BB_position", 0.5))
    bb_up = float(yesterday.get("BB_upper", 0))
    bb_low = float(yesterday.get("BB_lower", 0))

    # RSI状态
    if rsi > 80:
        rsi_state = "极度超买(>80)，追多极度危险"
    elif rsi > 70:
        rsi_state = "超买(>70)，多头拥挤"
    elif rsi < 20:
        rsi_state = "极度超卖(<20)，追空极度危险"
    elif rsi < 30:
        rsi_state = "超卖(<30)，空头拥挤"
    else:
        rsi_state = "中性区间"

    # 量能
    vol_ratio = vol_y / avg_vol if avg_vol > 0 else 1
    if vol_ratio > 1.5:
        vol_desc = "放量"
    elif vol_ratio < 0.5:
        vol_desc = "缩量"
    else:
        vol_desc = "正常"

    # K线描述
    if change_pct > amplitude * 0.6:
        candle_desc = "大阳线"
    elif change_pct < -amplitude * 0.6:
        candle_desc = "大阴线"
    elif abs(change_pct) < amplitude * 0.15:
        candle_desc = "十字星/犹豫"
    elif change_pct > 0:
        candle_desc = "小阳"
    else:
        candle_desc = "小阴"

    # 均线排列（简化）
    mas = [ma5, ma10, ma30, ma60]
    if all(mas[i] >= mas[i + 1] for i in range(len(mas) - 1)):
        ma_desc = "多头排列（MA5>MA10>MA30>MA60）"
    elif all(mas[i] <= mas[i + 1] for i in range(len(mas) - 1)):
        ma_desc = "空头排列（MA5<MA10<MA30<MA60）"
    else:
        ma_desc = "交叉/缠绕（均线方向不一致）"

    # 支撑/阻力来源
    support = min_10d
    resistance = max_10d
    support_source = "10日最低"
    resistance_source = "10日最高"

    return {
        "close": close_y,
        "open": open_y,
        "high": high_y,
        "low": low_y,
        "trend": trend,
        "chg_5": chg_5,
        "rsi": rsi,
        "rsi_state": rsi_state,
        "macd_hist": macd_h,
        "ma_alignment": ma_desc,
        "bb_pos": bb_pos,
        "bb_upper": bb_up,
        "bb_lower": bb_low,
        "candle_desc": candle_desc,
        "vol_desc": vol_desc,
        "vol_ratio": round(vol_ratio, 1),
        "support": support,
        "resistance": resistance,
        "support_source": support_source,
        "resistance_source": resistance_source,
        "atr": float(yesterday.get("ATR", 0)),
    }


def _format_hourly_context(hourly_df: pd.DataFrame) -> Dict:
    """将今日1H K线DataFrame格式化为上下文数据。"""
    if hourly_df is None or hourly_df.empty:
        return {
            "current_price": 0,
            "today_open": 0,
            "today_change_pct": 0,
            "hours_count": 0,
            "table": "(无1H数据)",
            "vol_summary": "无",
        }

    df = hourly_df.copy()
    today_open = float(df.iloc[0]["open"])
    current_price = float(df.iloc[-1]["close"])
    today_change = (current_price - today_open) / today_open * 100 if today_open > 0 else 0
    hours_count = len(df)

    # 计算量能特征
    vols = df["vol"].astype(float).values
    avg_vol = float(np.mean(vols))
    max_vol_idx = int(np.argmax(vols))
    max_vol_hour = ""
    if "timestamp" in df.columns:
        try:
            ts = df.iloc[max_vol_idx]["timestamp"]
            if hasattr(ts, 'strftime'):
                max_vol_hour = ts.strftime("%H:%M")
            else:
                max_vol_hour = str(ts)[-8:-3] if len(str(ts)) > 5 else f"第{max_vol_idx+1}根"
        except Exception:
            max_vol_hour = f"第{max_vol_idx+1}根"
    else:
        max_vol_hour = f"第{max_vol_idx+1}根"

    # 最近3根的量
    recent_vols = vols[-3:] if len(vols) >= 3 else vols
    vol_trend = "放量" if recent_vols[-1] > avg_vol * 1.3 else \
                "缩量" if recent_vols[-1] < avg_vol * 0.7 else "正常"

    # 最高/最低价位置
    high_price = float(df["high"].max())
    low_price = float(df["low"].min())

    # 涨幅分布：前一半 vs 后一半
    half = hours_count // 2
    if half > 0:
        first_half_open = float(df.iloc[0]["open"])
        first_half_close = float(df.iloc[half - 1]["close"])
        second_half_open = float(df.iloc[half]["open"])
        first_half_chg = (first_half_close - first_half_open) / first_half_open * 100 if first_half_open > 0 else 0
        second_half_chg = (current_price - second_half_open) / second_half_open * 100 if second_half_open > 0 else 0
        rhythm = f"前半段{first_half_chg:+.1f}% / 后半段{second_half_chg:+.1f}%"
    else:
        rhythm = ""

    vol_summary = (f"今日均价量{avg_vol:.0f} · 最大量在{max_vol_hour} · "
                   f"最近{vol_trend} · 节奏: {rhythm}")

    # 构建表格
    table_header = f"{'时间':>6} {'开盘':>10} {'最高':>10} {'最低':>10} {'收盘':>10} {'量能':>8} {'特征'}"
    table_rows = [table_header]
    table_rows.append("-" * 70)

    price_precision = 6 if current_price < 1 else 4

    for i, (_, row) in enumerate(df.iterrows()):
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])
        v = float(row["vol"])

        # 时间
        if "timestamp" in df.columns:
            ts = row["timestamp"]
            if hasattr(ts, 'strftime'):
                time_str = ts.strftime("%H:%M")
            else:
                time_str = str(ts)[-8:-3] if len(str(ts)) > 5 else f"{i:02d}:00"
        else:
            time_str = f"{i:02d}:00"

        # 量能标记
        vol_ratio_local = v / avg_vol if avg_vol > 0 else 1
        if vol_ratio_local > 2:
            vol_tag = "🔥放量"
        elif vol_ratio_local > 1.3:
            vol_tag = "↑"
        elif vol_ratio_local < 0.5:
            vol_tag = "↓缩量"
        else:
            vol_tag = ""

        # 单根K线特征
        body = c - o
        wick_up = h - max(o, c)
        wick_down = min(o, c) - l
        if body > 0 and wick_down > abs(body) * 1.5:
            tag = "锤子线"
        elif body < 0 and wick_up > abs(body) * 1.5:
            tag = "流星线"
        elif abs(body) < (h - l) * 0.1:
            tag = "十字星"
        elif body > 0:
            tag = "阳线" + ("+大" if abs(body) / (l if l > 0 else 1) > 0.03 else "")
        else:
            tag = "阴线" + ("-大" if abs(body) / (l if l > 0 else 1) > 0.03 else "")

        if vol_tag:
            tag = f"{tag} {vol_tag}"

        table_rows.append(
            f"{time_str:>6} {o:{price_precision + 4}.{price_precision}f} "
            f"{h:{price_precision + 4}.{price_precision}f} {l:{price_precision + 4}.{price_precision}f} "
            f"{c:{price_precision + 4}.{price_precision}f} {v:>8.0f} {tag}"
        )

    return {
        "current_price": current_price,
        "today_open": today_open,
        "today_change_pct": round(today_change, 2),
        "hours_count": hours_count,
        "table": "\n".join(table_rows),
        "vol_summary": vol_summary,
        "today_high": high_price,
        "today_low": low_price,
    }


def adjust_signal_with_live_price(
    config,
    symbol: str,
    original_signal: dict,
    live_price: float,
    original_close: float,
) -> dict | None:
    """实盘价格偏离过大时，让AI重新校准入场/止损/止盈点位。

    当实盘价与信号入场价偏离超过阈值时调用，
    AI 会结合现价重新评估合理的挂单价、止损和目标。

    返回调整后的 dict（同样格式），或 None 表示无需调整/调用失败。
    """
    if not config.DEEPSEEK_API_KEY:
        return None

    ai = original_signal.get("ai", {})
    direction = ai.get("direction", "neutral")
    entry_old = ai.get("entry")
    stop_old = ai.get("stop_loss")
    tp1_old = ai.get("take_profit1")
    tp2_old = ai.get("take_profit2")
    prediction = ai.get("tomorrow_prediction", "")
    signal_date = original_signal.get("date", "?")

    if direction == "neutral" or not entry_old:
        return None

    # 偏离百分比
    deviation_pct = abs(live_price - float(entry_old)) / float(entry_old) * 100

    prompt = f"""你是职业加密货币交易员。之前基于{symbol}在{signal_date}的分析给了信号，但价格已大幅移动，需要重新校准。

·· 原始信号 ··
交易对: {symbol}
信号日: {signal_date} · 当时收盘价: {original_close:.6f}
原方向: {direction} · 入场: {entry_old} · 止损: {stop_old} · 止盈: {tp1_old} / {tp2_old}
原判断: {prediction}

·· 当前现实 ··
实盘价: {live_price:.6f}（偏离入场价 {deviation_pct:.1f}%）

·· 你的任务（操盘手视角）··
请在头脑中快速过这三个问题，然后输出JSON：

1. 原方向逻辑还成立吗？
   - 做空信号 + 已经跌了很多 → 是否已到结构支撑？空头是否该止盈？
   - 做多信号 + 已经涨了很多 → 是否已到结构阻力？多头是否该止盈？
   - 价格已穿越原止损价 → 原逻辑被证伪

2. 如果原方向不成立：
   - 是否出现反向信号？（极端RSI+资金费率反转+结构位=反向入场机会）
   - 还是市场已进入不确定状态，应该观望？

3. 如果原方向仍成立：
   - 入场价应该调整到哪个结构位（基于当前价格而不是历史价格）？
   - 止损应该放在哪？（AVOID placing stop too close to current price）

关键原则：
- 永远不要在价格已经大幅移动后追单（你已经错过了最佳入场）
- 如果价格已到极端位置（RSI>80或<20），反向交易比顺势交易更安全
- 如果价格已穿越原止损位，原信号作废
- 结构是第一位的，没有清晰的S/R就不要给信号

只输出 JSON（不要任何其他文字）：
{{"direction":"long|short|neutral","entry":校准后入场,"stop_loss":校准后止损,"take_profit1":校准后止盈1,"take_profit2":校准后止盈2,"strength":"strong|medium|weak","tomorrow_prediction":"校准逻辑简述：结构判断+是否追单+调整原因"}}"""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
        logger = logging.getLogger(__name__)
        logger.info(f"请求 DeepSeek 实盘校准 {symbol} (偏离{deviation_pct:.1f}%)...")

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是职业加密货币交易员。你的专长是在价格大幅移动后冷静评估局势，拒绝追逐已发生的行情。只输出JSON，不要推理文字。价格保留6位小数。"},
                {"role": "user", "content": prompt},
            ],
            temperature=config.AI_TEMPERATURE,
            max_tokens=400,
            stream=False,
        )

        if not response.choices:
            logger.error("校准AI返回空")
            return None

        content = response.choices[0].message.content
        logger.info(f"校准AI返回: {content[:200]}...")

        json_str = _extract_json(content)
        if json_str:
            result = json.loads(json_str)
            for key in ("entry", "stop_loss", "take_profit1", "take_profit2",
                        "key_support", "key_resistance"):
                val = result.get(key)
                if val is not None and not isinstance(val, (int, float)):
                    try:
                        result[key] = float(val)
                    except (ValueError, TypeError):
                        result[key] = None
            logger.info(f"校准结果: direction={result.get('direction')} "
                        f"entry={result.get('entry')}")
            return result
        else:
            logger.warning("校准AI返回中未找到JSON")
            return None

    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.exception(f"实盘校准异常: {e}")
        return None
