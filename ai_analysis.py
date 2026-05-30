# ai_analysis.py
"""
AI 信号模块：将 K 线、指标与市场数据组装成提示词，调用 DeepSeek API 得到交易建议。

使用 config 中的 PROMPT_TEMPLATE_ADVANCED（或 PROMPT_TEMPLATE），
要求模型返回 JSON：direction、entry、stop_loss、take_profit1/2、strength、market_state、key_support/key_resistance 等。
返回前经 signal_utils.normalize_signal 标准化。
"""
import json
import re
import logging
import pandas as pd
import numpy as np
from collections import defaultdict, Counter
from typing import Dict, List, Optional

from openai import OpenAI

from config import Config


def _build_prompt(config, df, symbol, **kwargs) -> str:
    """构建发送给 AI 的完整提示词（不含系统 prompt）。

    若启用 AI_ECO_MODE，采用精简模式：不传原始K线数据，只传结论摘要。
    """
    eco_mode = getattr(config, 'AI_ECO_MODE', True)

    if not eco_mode:
        return _build_full_prompt(config, df, symbol, **kwargs)
    else:
        return _build_compact_prompt(config, df, symbol, **kwargs)


def _build_full_prompt(config, df, symbol, **kwargs) -> str:
    """完整版 prompt（原始数据，兼容传统模式）。"""
    recent = df.tail(config.LOOKBACK).dropna()
    cols_for_ai = ['open_time', 'open', 'high', 'low', 'close', 'vol',
                   'MA5', 'MA10', 'MA30', 'RSI14', 'MACD', 'Signal', 'MACD_Hist', 'K', 'D', 'J']
    if getattr(config, 'ADVANCED_INDICATORS', False):
        advanced_cols = ['BB_upper', 'BB_lower', 'BB_width', 'BB_position', 'ATR', 'VWAP']
        cols_for_ai.extend([c for c in advanced_cols if c in recent.columns])
    available_cols = [c for c in cols_for_ai if c in recent.columns]
    data_text = recent[available_cols].to_string(index=False)

    prompt_template = getattr(config, 'PROMPT_TEMPLATE_ADVANCED', '') or config.PROMPT_TEMPLATE

    format_dict = {
        'symbol': symbol, 'target_tf': config.TARGET_TIMEFRAME, 'lookback': len(recent),
        'data_text': data_text,
        'long_tf': getattr(config, 'LONG_TERM_TIMEFRAME', '1H'),
        'long_ma60': kwargs.get('long_ma60', 0), 'long_trend': kwargs.get('long_trend', '未知'),
        'funding_rate': kwargs.get('funding_rate', 0), 'oi': kwargs.get('latest_oi', 0),
        'oi_change': kwargs.get('oi_change', 0), 'ls_ratio': kwargs.get('ls_ratio', 0),
        'elite_ratio': kwargs.get('elite_ratio', 0), 'net_taker': kwargs.get('net_taker', 0),
        'premium': kwargs.get('premium', 0),
        'bid_price': kwargs.get('bid_price', 0), 'bid_size': kwargs.get('bid_size', 0),
        'ask_price': kwargs.get('ask_price', 0), 'ask_size': kwargs.get('ask_size', 0),
        'bid_ask_ratio': kwargs.get('bid_ask_ratio', 0),
        'bb_position': kwargs.get('bb_position', 0.5), 'atr': kwargs.get('atr', 0),
        'rsi': kwargs.get('rsi', 50), 'macd_hist': kwargs.get('macd_hist', 0),
        'k': kwargs.get('k', 50), 'd': kwargs.get('d', 50), 'j': kwargs.get('j', 50),
    }
    from collections import defaultdict
    safe_dict = defaultdict(str, format_dict)
    prompt = prompt_template.format_map(safe_dict)

    # 追加外部分析结果
    prompt += _build_analysis_append(kwargs)
    return prompt


def _build_compact_prompt(config, df, symbol, **kwargs) -> str:
    """
    精简版 prompt（AI 节能模式）：
    - 不传原始 K 线数据（省 ~600 tokens）
    - 只传递结构化的结论摘要
    - 要求 AI 直接输出 JSON 决策
    """
    recent = df.tail(config.LOOKBACK).dropna()
    latest = recent.iloc[-1] if not recent.empty else pd.Series()

    # ── 核心价格信息（1行）──
    cur_close = latest.get('close', 0)
    cur_open = latest.get('open', 0)
    cur_high = latest.get('high', 0)
    cur_low = latest.get('low', 0)
    cur_vol = latest.get('vol', 0)

    # ── 技术指标摘要（精简）──
    rsi = latest.get('RSI14', 50)
    macd_hist = latest.get('MACD_Hist', 0)
    bb_pos = latest.get('BB_position', 0.5)
    atr = latest.get('ATR', 0)
    k, d, j = latest.get('K', 50), latest.get('D', 50), latest.get('J', 50)
    ma5 = latest.get('MA5', 0)
    ma10 = latest.get('MA10', 0)
    ma30 = latest.get('MA30', 0)

    # ── 5根K线涨跌序列 ──
    price_series = recent['close'].tail(5).values.astype(float) if len(recent) >= 5 else []
    price_changes = [round((price_series[i] - price_series[i-1]) / price_series[i-1] * 100, 2)
                     for i in range(1, len(price_series))] if len(price_series) >= 2 else []

    parts = [f"# {symbol} {config.TARGET_TIMEFRAME} 交易信号分析\n"]

    # 价格快照
    parts.append(f"## 当前价格\n"
                 f"最新价: {cur_close:.4f} | 开盘: {cur_open:.4f} | "
                 f"高: {cur_high:.4f} | 低: {cur_low:.4f} | 量: {cur_vol:.2f}\n"
                 f"近5根涨跌(%): {price_changes}")

    # 技术指标
    parts.append(f"## 技术指标\n"
                 f"RSI14={rsi:.1f} | MACD_Hist={macd_hist:.4f} | BB位置={bb_pos:.2f} | "
                 f"ATR={atr:.4f} | KDJ={k:.1f}/{d:.1f}/{j:.1f}\n"
                 f"MA5={ma5:.4f} MA10={ma10:.4f} MA30={ma30:.4f}")

    # 长周期
    long_trend = kwargs.get('long_trend', '未知')
    long_ma60 = kwargs.get('long_ma60', 0)
    parts.append(f"## 长周期\n"
                 f"周期: {getattr(config, 'LONG_TERM_TIMEFRAME', '1H')} | "
                 f"MA60: {long_ma60:.2f} | 趋势: {long_trend}")

    # 市场情绪
    funding_rate = kwargs.get('funding_rate', 0)
    ls_ratio = kwargs.get('ls_ratio', 0)
    elite_ratio = kwargs.get('elite_ratio', 0)
    oi_change = kwargs.get('oi_change', 0)
    net_taker = kwargs.get('net_taker', 0)
    parts.append(f"## 市场情绪\n"
                 f"费率: {funding_rate:.6f} | 多空比: {ls_ratio:.2f} | "
                 f"精英比: {elite_ratio:.2f} | OI变动: {oi_change:.2%} | "
                 f"净Taker: {net_taker:.2f}")

    # 订单簿
    bid_ask_ratio = kwargs.get('bid_ask_ratio', 0)
    bid_price = kwargs.get('bid_price', 0)
    ask_price = kwargs.get('ask_price', 0)
    parts.append(f"## 订单簿\n"
                 f"买卖比: {bid_ask_ratio:.2f} | 买一: {bid_price:.4f} | 卖一: {ask_price:.4f}")

    # ── 分析结论摘要（核心：只传结论，不传原始数据）──
    parts.append(f"\n## 各分析模块结论\n")
    parts.append(_build_analysis_summary(kwargs, latest, cur_close))

    # ── 精简 JSON 输出要求 ──
    parts.append(f"""\n## 任务
综合以上所有结论，给出交易决策。直接输出 JSON:
{{"direction":"long|short|neutral","entry":价格,"stop_loss":价格,
 "take_profit1":价格,"take_profit2":价格,
 "strength":"strong|medium|weak","market_state":"描述",
 "key_support":价格或null,"key_resistance":价格或null}}""")

    return "\n".join(parts)


def _build_analysis_summary(kwargs: Dict, latest, current_price: float) -> str:
    """构建各模块的分析结论摘要（精简，只传关键结论）。"""
    lines = []

    # 规则策略结论
    rule_signal = kwargs.get('rule_signal')
    if rule_signal and rule_signal.get('direction') != 'neutral':
        lines.append(f"- 规则策略: 方向={rule_signal['direction']} "
                     f"入场={rule_signal.get('entry','N/A')} "
                     f"止损={rule_signal.get('stop_loss','N/A')} "
                     f"强度={rule_signal.get('strength','medium')}")
    elif rule_signal:
        lines.append(f"- 规则策略: 无明确方向 (market_state={rule_signal.get('market_state','未知')})")
    else:
        lines.append(f"- 规则策略: 未启用")

    # 技术分析结论（背离 + 形态）
    tech_batch = kwargs.get('tech_batch')
    if tech_batch:
        rsi_div = tech_batch.get('rsi_divergence', {})
        macd_div = tech_batch.get('macd_divergence', {})
        patterns = tech_batch.get('candlestick_patterns', [])
        tech_items = []
        if rsi_div.get('type', 'none') != 'none':
            tech_items.append(f"RSI{rsi_div['type']}")
        if macd_div.get('type', 'none') != 'none':
            tech_items.append(f"MACD{macd_div['type']}")
        if patterns:
            p_names = [f"{p['name']}({p.get('direction','?')})" for p in patterns[:3]]
            tech_items.append(f"形态:{','.join(p_names)}")
        if tech_items:
            lines.append(f"- 技术分析: {'; '.join(tech_items)}")
        else:
            lines.append(f"- 技术分析: 无明显背离/形态信号")

    # 操盘检测结论
    manipulation = kwargs.get('manipulation')
    if manipulation:
        phase = manipulation.get('phase_result', {})
        next_mv = manipulation.get('next_move', {})
        wyckoff = manipulation.get('wyckoff', {})
        lines.append(f"- 操盘阶段: {phase.get('phase_cn','未知')} "
                     f"(评分:{phase.get('score',0)} 置信:{phase.get('confidence',0)})")
        if phase.get('signals'):
            lines.append(f"  信号: {'; '.join(phase['signals'][:3])}")
        if wyckoff.get('schematic', 'none') != 'none':
            lines.append(f"  威科夫: {wyckoff['schematic']} | {wyckoff.get('detail','')}")
        if next_mv.get('next_action'):
            lines.append(f"  庄家预估: {next_mv['next_action']} "
                         f"(方向:{next_mv.get('direction','?')} "
                         f"目标:{next_mv.get('target_price','?')})")

    return "\n".join(lines)


def _build_analysis_append(kwargs: Dict) -> str:
    """追加完整版分析内容（legacy）。"""
    result = ""

    rule_signal = kwargs.get('rule_signal')
    if rule_signal and rule_signal.get('direction') != 'neutral':
        result += f"""
【规则策略初步建议】
方向：{rule_signal.get('direction', 'neutral')}
入场价：{rule_signal.get('entry', 'N/A')}
止损价：{rule_signal.get('stop_loss', 'N/A')}
止盈1：{rule_signal.get('take_profit1', 'N/A')}
止盈2：{rule_signal.get('take_profit2', 'N/A')}
强度：{rule_signal.get('strength', 'N/A')}
市场状态判断：{rule_signal.get('market_state', 'N/A')}
"""

    tech_batch = kwargs.get('tech_batch')
    if tech_batch:
        tech_lines = []
        rsi_div = tech_batch.get('rsi_divergence', {})
        macd_div = tech_batch.get('macd_divergence', {})
        patterns = tech_batch.get('candlestick_patterns', [])
        ob_features = tech_batch.get('orderbook_features', {})
        if rsi_div.get('type') != 'none':
            tech_lines.append(f"- RSI背离: {rsi_div.get('detail')}")
        if macd_div.get('type') != 'none':
            tech_lines.append(f"- MACD背离: {macd_div.get('detail')}")
        if patterns:
            p_names = ", ".join(f"{p['name']}({p['direction']})" for p in patterns)
            tech_lines.append(f"- K线形态: {p_names}")
        if ob_features:
            ob_parts = [f"{k}={v}" for k, v in list(ob_features.items())[:6]]
            tech_lines.append(f"- 订单簿特征: {', '.join(ob_parts)}")
        if tech_lines:
            result += "\n\n" + "【高级技术分析检测结果】\n" + "\n".join(tech_lines)

    manipulation = kwargs.get('manipulation')
    if manipulation:
        phase = manipulation.get('phase_result', {})
        next_mv = manipulation.get('next_move', {})
        wyckoff = manipulation.get('wyckoff', {})
        manip_lines = []
        if phase.get('phase_cn'):
            manip_lines.append(f"【庄家操盘阶段】{phase['phase_cn']} (评分: {phase.get('score', 0)})")
        if phase.get('signals'):
            manip_lines.append(f"- 操纵信号: {'; '.join(phase['signals'])}")
        if wyckoff.get('schematic') and wyckoff['schematic'] != 'none':
            manip_lines.append(f"- 威科夫结构: {wyckoff['schematic']} | 事件: {wyckoff.get('events', [])}")
        if next_mv.get('next_action'):
            manip_lines.append(f"- 预计下一步: {next_mv['next_action']} (偏向: {next_mv.get('direction','N/A')})")
            manip_lines.append(f"- 庄家目标价: {next_mv.get('target_price', 'N/A')}")
            manip_lines.append(f"- 推演逻辑: {next_mv.get('reasoning', '')}")
        if manip_lines:
            result += "\n\n" + "\n".join(manip_lines)

    return result


def _should_skip_ai(rule_signal: Dict, manipulation_result: Dict, config) -> bool:
    """
    AI 节能模式判定：当规则信号与操盘检测高度一致时，跳过 AI 调用。

    条件：
      1. AI_ECO_MODE = True
      2. rule_signal 与 manipulation 方向一致
      3. 规则信号强度 >= medium
      4. 操盘置信度 >= 0.6（新增：避免低置信操盘导致的误判）
    """
    eco_mode = getattr(config, 'AI_ECO_MODE', True)
    if not eco_mode:
        return False

    if not rule_signal or rule_signal.get('direction') == 'neutral':
        return False

    if not manipulation_result:
        return False

    rule_dir = rule_signal.get('direction')
    rule_strength = rule_signal.get('strength', 'medium')
    if rule_strength == 'weak':
        return False  # 规则信号太弱，需要 AI 二次确认

    manip_phase_data = manipulation_result.get('phase_result', {})
    manip_phase = manip_phase_data.get('phase', '')
    manip_conf = manip_phase_data.get('confidence', 0)

    # 🆕 操盘置信度不足，不能跳过AI（避免低质量操盘信号误判）
    if manip_conf < 0.6:
        logger.info(f"[AI节能] 操盘置信度{manip_conf:.0%} < 0.6，不跳过AI")
        return False

    bullish_phases = ('accumulation', 'markup', 'shakeout')
    bearish_phases = ('distribution', 'markdown')

    if rule_dir == 'long' and manip_phase in bullish_phases:
        logger.info(f"[AI节能] 规则看多 + 操盘{manip_phase}(置信{manip_conf:.0%}) → 高度一致，跳过AI调用")
        return True
    if rule_dir == 'short' and manip_phase in bearish_phases:
        logger.info(f"[AI节能] 规则看空 + 操盘{manip_phase}(置信{manip_conf:.0%}) → 高度一致，跳过AI调用")
        return True

    return False


def _call_ai_once(client, prompt: str, temperature: float = 0.3) -> Dict:
    """单次调用 AI 并解析 JSON 信号。"""
    messages = [
        {"role": "system", "content": COT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=messages,
        temperature=temperature,
        max_tokens=1500,
        stream=False
    )
    if not response.choices:
        logger.error("API 返回空 choices")
        return {}
    analysis = response.choices[0].message.content
    logger.info(f"AI返回原始内容: {analysis}")
    json_str = _extract_json(analysis)
    if json_str:
        return json.loads(json_str)
    logger.warning("未找到JSON格式信号")
    return {}


# Chain-of-Thought 系统提示词
COT_SYSTEM_PROMPT = """你是一位资深量化交易员。请按以下步骤逐步推理，最后给出 JSON 交易计划：

第一步【趋势判断】：当前处于什么趋势？依据是什么？
第二步【关键位置】：最近的关键支撑位和压力位在哪里？依据是什么？
第三步【量价关系】：当前成交量、订单簿、资金费率给出了什么信号？
第四步【多空力量】：综合判断当前多空哪方占优？
第五步【交易计划】：基于以上分析，给出具体的入场、止损、止盈。

最后以 JSON 格式输出交易计划（不要包含推理过程的文字，只输出 JSON）：
{
  "direction": "long" 或 "short" 或 "neutral",
  "entry": 价格数值,
  "stop_loss": 价格数值,
  "take_profit1": 第一目标价,
  "take_profit2": 第二目标价,
  "strength": "strong" 或 "medium" 或 "weak",
  "market_state": "例如：多头主导、空头主导、震荡整理",
  "key_support": 关键支撑位价格（可以为 null）,
  "key_resistance": 关键压力位价格（可以为 null）
}"""


def ensemble_ai_analysis(config: Config, prompt: str, n_calls: int = 3) -> Dict:
    """
    AI 集成投票：调用 DeepSeek 3 次，取多数方向的平均点位，提高可靠性。
    参数 n_calls 控制调用次数（建议 3）。
    """
    if not config.DEEPSEEK_API_KEY:
        logger.error("未设置 DeepSeek API 密钥")
        return {}

    client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    signals = []

    for i in range(n_calls):
        try:
            messages = [
                {"role": "system", "content": COT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ]
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                temperature=0.2 + i * 0.15,  # 逐步递增温度以增加多样性 (0.2→0.35→0.5)
                max_tokens=1500,
                stream=False
            )
            if not response.choices:
                logger.warning(f"集成投票: 第 {i+1} 次调用返回空 choices")
                continue

            analysis = response.choices[0].message.content
            json_str = _extract_json(analysis)
            if json_str:
                sig = json.loads(json_str)
                signals.append(sig)
                logger.info(f"集成投票: 第 {i+1} 次 direction={sig.get('direction')}")
            else:
                logger.warning(f"集成投票: 第 {i+1} 次未提取到 JSON")
        except Exception as e:
            logger.warning(f"集成投票: 第 {i+1} 次调用失败: {e}")

    if not signals:
        logger.error("集成投票: 所有调用失败，无有效信号")
        return {}

    # 统计方向投票
    directions = [s.get('direction', 'neutral') for s in signals]
    from collections import Counter
    votes = Counter(directions)

    # 取最多票的方向
    top_direction, top_count = votes.most_common(1)[0]
    if top_direction == 'neutral' or top_count < n_calls / 2:
        logger.info(f"集成投票: 无多数方向（{dict(votes)}），观望")
        return {"direction": "neutral", "market_state": "信号分歧, 观望"}

    # 取同方向信号的平均点位
    same_dir = [s for s in signals if s.get('direction') == top_direction]
    avg_signal = {
        'direction': top_direction,
        'entry': round(np.mean([s.get('entry') for s in same_dir if s.get('entry')]), 6),
        'stop_loss': round(np.mean([s.get('stop_loss') for s in same_dir if s.get('stop_loss')]), 6),
        'take_profit1': round(np.mean([s.get('take_profit1') for s in same_dir if s.get('take_profit1')]), 6),
        'take_profit2': round(np.mean([s.get('take_profit2') for s in same_dir if s.get('take_profit2')]), 6),
        'strength': max(same_dir, key=lambda s: ['weak', 'medium', 'strong'].index(s.get('strength', 'weak'))).get('strength', 'medium'),
        'market_state': same_dir[0].get('market_state', '未知'),
        'key_support': same_dir[0].get('key_support'),
        'key_resistance': same_dir[0].get('key_resistance'),
        'confidence': top_count / n_calls,
        'agreement': top_count,
    }
    logger.info(f"集成投票完成: direction={top_direction} agreement={top_count}/{n_calls}")
    return avg_signal


def _extract_json(text: str):
    """从文本中提取第一个完整的 JSON 对象（使用平衡括号匹配，比贪婪正则更鲁棒）。"""
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None

logger = logging.getLogger(__name__)


def ai_analysis(
    config,
    df: pd.DataFrame,
    symbol: str,
    rule_signal: Dict = None,
    orderbook_data: Dict = None,
    oi_data: List = None,
    funding_data: Dict = None,
    taker_volume_data: List = None,
    long_short_ratio_data: List = None,
    elite_ratio_data: List = None,
    mark_price_data: Dict = None,
    price_limit_data: Dict = None,
    premium_history: List = None,
    long_term_df: pd.DataFrame = None,
    long_trend: str = "未知",
    long_ma60: float = 0,
    tech_batch: Dict = None,
    manipulation: Dict = None,
) -> Dict:
    """
    调用 DeepSeek API 进行分析，返回信号字典。
    若 AI_ENSEMBLE_ENABLED=True，则使用 3 次集成投票提升可靠性。

    🆕 AI 节能模式 (AI_ECO_MODE):
      当规则信号与操盘检测高度一致时，直接复用规则信号，跳过 AI 调用，
      节省 Token 成本和响应时间。
    """
    # ── AI 节能模式判定 ──
    if _should_skip_ai(rule_signal, manipulation, config):
        merged = dict(rule_signal) if rule_signal else {}
        merged["source"] = "eco_merge"
        merged["ai_skipped"] = True
        # 补充操盘目标
        if manipulation:
            next_mv = manipulation.get("next_move", {})
            if next_mv.get("direction") == merged.get("direction"):
                if not merged.get("take_profit1"):
                    merged["take_profit1"] = next_mv.get("target_price")
            merged["eco_manipulation"] = manipulation
        logger.info(f"[AI节能] 跳过AI，共识信号: direction={merged.get('direction')} "
                     f"strength={merged.get('strength')}")
        return merged

    if not config.DEEPSEEK_API_KEY:
        logger.error("未设置 DeepSeek API 密钥")
        return {}

    recent = df.tail(config.LOOKBACK).dropna()
    if recent.empty:
        logger.error("数据不足，无法生成分析")
        return {}

    # ====== 提取市场数据 ======
    bid_price = 0; bid_size = 0; ask_price = 0; ask_size = 0; bid_ask_ratio = 0
    net_taker = 0; ls_ratio = 0; elite_ratio = 0; funding_rate = 0
    latest_oi = 0; oi_change = 0; premium = 0
    rsi = 50; macd_hist = 0; k_val = 50; d_val = 50; j_val = 50; bb_position = 0.5; atr = 0

    if orderbook_data and orderbook_data.get('bids') and orderbook_data.get('asks'):
        bids = orderbook_data.get('bids', [])
        asks = orderbook_data.get('asks', [])
        if bids and asks:
            bid_price = float(bids[0][0]); bid_size = float(bids[0][1])
            ask_price = float(asks[0][0]); ask_size = float(asks[0][1])
            bid_ask_ratio = (bid_size / ask_size) if ask_size > 0 else 0

    if taker_volume_data and len(taker_volume_data) >= 1:
        latest_taker = taker_volume_data[0]
        if len(latest_taker) >= 3:
            net_taker = float(latest_taker[2]) - float(latest_taker[1])

    if long_short_ratio_data and len(long_short_ratio_data) >= 1:
        ls_ratio = float(long_short_ratio_data[0][1])

    if elite_ratio_data and len(elite_ratio_data) >= 1:
        elite_ratio = float(elite_ratio_data[0][1])

    if funding_data:
        funding_rate = float(funding_data.get('fundingRate', 0))

    if oi_data and len(oi_data) >= 1:
        latest_oi = float(oi_data[0][1])
        if len(oi_data) >= 2:
            prev_oi = float(oi_data[1][1])
            if prev_oi != 0:
                oi_change = (latest_oi - prev_oi) / prev_oi

    if premium_history and len(premium_history) >= 1:
        premium = float(premium_history[0][1])

    latest_row = df.iloc[-1] if not df.empty else pd.Series()
    rsi = latest_row.get('RSI14', 50)
    macd_hist = latest_row.get('MACD_Hist', 0)
    k_val = latest_row.get('K', 50); d_val = latest_row.get('D', 50); j_val = latest_row.get('J', 50)
    bb_position = latest_row.get('BB_position', 0.5)
    atr = latest_row.get('ATR', 0)

    # ====== 构建提示词 ======
    prompt = _build_prompt(config, df, symbol,
        long_ma60=long_ma60, long_trend=long_trend,
        funding_rate=funding_rate, latest_oi=latest_oi, oi_change=oi_change,
        ls_ratio=ls_ratio, elite_ratio=elite_ratio, net_taker=net_taker, premium=premium,
        bid_price=bid_price, bid_size=bid_size, ask_price=ask_price, ask_size=ask_size,
        bid_ask_ratio=bid_ask_ratio, bb_position=bb_position, atr=atr,
        rsi=rsi, macd_hist=macd_hist, k=k_val, d=d_val, j=j_val,
        rule_signal=rule_signal, tech_batch=tech_batch,
        manipulation=manipulation)

    logger.info("正在请求DeepSeek API...")

    # ====== 集成投票（如果启用）或单次调用 ======
    ensemble_enabled = getattr(config, 'AI_ENSEMBLE_ENABLED', True)
    if ensemble_enabled:
        signal = ensemble_ai_analysis(config, prompt, n_calls=3)
        return signal

    # 单次调用（使用配置的温度参数）
    client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    ai_temp = getattr(config, 'AI_TEMPERATURE', 0.3)
    signal = _call_ai_once(client, prompt, temperature=ai_temp)
    return signal  # 标准化由 main.py 统一处理
