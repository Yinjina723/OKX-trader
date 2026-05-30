"""
市场情绪分析模块 | Sentiment Analysis Module

数据源：
1. Alternative.me Fear & Greed Index（加密货币恐慌贪婪指数，免费无认证）
2. OKX Put/Call Ratio 数据（期权情绪代理）
3. 可扩展：Twitter/Reddit/Discord 文本情绪 NLP 分析（预留接口）

输出：
- fear_greed_score: 0~100 恐慌贪婪指数（越高越贪婪）
- normalized_fear: 0~1 标准化恐慌度（1=极度恐慌，配合洗盘检测使用）
- sentiment_signals: 情绪相关信号描述
"""
import logging
import time
import json
import urllib.request
import urllib.error
from typing import Dict, Optional, List
from collections import deque

logger = logging.getLogger(__name__)

# ==================== 缓存 ====================
_cache_fng: Dict = {"value": None, "timestamp": 0, "ttl": 300}  # 5分钟缓存


# ==================== 1. Fear & Greed Index ====================

def fetch_fear_greed_index() -> Dict:
    """
    从 alternative.me 获取恐惧贪婪指数。
    免费 API，无需认证，限制 ~60次/分钟。
    返回: {"value": 0~100, "classification": "...", "timestamp": ...}
    """
    global _cache_fng

    now = time.time()
    if _cache_fng["value"] is not None and (now - _cache_fng["timestamp"]) < _cache_fng["ttl"]:
        return {
            "value": _cache_fng["value"],
            "classification": _cache_fng.get("classification", "未知"),
            "source": "cache",
        }

    try:
        url = "https://api.alternative.me/fng/?limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "OKX-Anti-Washout/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if data.get("data") and len(data["data"]) > 0:
                item = data["data"][0]
                value = int(item.get("value", 50))
                classification = item.get("value_classification", "Neutral")
                _cache_fng = {
                    "value": value,
                    "classification": classification,
                    "timestamp": now,
                    "ttl": 300,
                }
                logger.info(f"Fear & Greed Index: {value} ({classification})")
                return {"value": value, "classification": classification, "source": "api"}
    except Exception as e:
        logger.debug(f"Fear & Greed API 获取失败: {e}，回退缓存/默认值")
        if _cache_fng["value"] is not None:
            return {
                "value": _cache_fng["value"],
                "classification": _cache_fng.get("classification", "未知"),
                "source": "stale_cache",
            }
        return {"value": 50, "classification": "Neutral", "source": "fallback_default"}


# ==================== 2. PCR 情绪代理 ====================

def compute_pcr_sentiment(
    option_pcr_data: Optional[List] = None,
    current_pcr_oi: Optional[float] = None,
) -> Dict:
    """
    将 Put/Call Ratio 映射到情绪分（0~1 恐慌度）。
    
    PCR > 1.5 → 极度恐慌（底部信号）
    PCR < 0.5 → 极度贪婪（顶部信号）
    
    返回: {"fear_score": 0~1, "extreme": bool, "signal": str}
    """
    fear_score = 0.5  # 默认中性
    signal = ""
    extreme = False

    # 优先使用直接传入的 PCR 值
    pcr_val = current_pcr_oi
    if pcr_val is None and option_pcr_data and len(option_pcr_data) >= 1:
        try:
            pcr_val = float(option_pcr_data[0].get("openInterestRatio", 0))
        except (ValueError, TypeError, AttributeError):
            pass

    if pcr_val is None or pcr_val <= 0:
        return {"fear_score": 0.5, "extreme": False, "signal": "PCR数据缺失，使用默认值"}

    # 分段映射到 0~1
    if pcr_val >= 2.0:
        fear_score = 0.95
        signal = f"PCR-IO极高({pcr_val:.2f})--市场极度恐慌"
        extreme = True
    elif pcr_val >= 1.5:
        fear_score = 0.80
        signal = f"PCR-IO很高({pcr_val:.2f})--市场恐慌"
    elif pcr_val >= 1.2:
        fear_score = 0.65
        signal = f"PCR-IO偏高({pcr_val:.2f})--偏恐慌"
    elif pcr_val >= 0.8:
        fear_score = 0.50
        signal = f"PCR-IO中性({pcr_val:.2f})"
    elif pcr_val >= 0.6:
        fear_score = 0.30
        signal = f"PCR-IO偏低({pcr_val:.2f})--偏贪婪"
    elif pcr_val >= 0.4:
        fear_score = 0.15
        signal = f"PCR-IO很低({pcr_val:.2f})--市场贪婪"
    else:
        fear_score = 0.05
        signal = f"PCR-IO极低({pcr_val:.2f})--市场极度贪婪"
        extreme = True

    return {"fear_score": fear_score, "extreme": extreme, "signal": signal}


# ==================== 3. 资金费率情绪代理 ====================

def compute_funding_sentiment(
    funding_rate: float = 0.0,
    funding_rate_history: Optional[List] = None,
) -> Dict:
    """
    将资金费率映射到情绪分。
    
    极高正费率 → 市场过热贪婪
    极高负费率 → 市场恐慌
    """
    fear_score = 0.5
    signal = ""

    if abs(funding_rate) > 0.002:
        if funding_rate > 0:
            fear_score = 0.10  # 狂热贪婪
            signal = f"资金费率极高({funding_rate:.4%})--市场过热贪婪"
        else:
            fear_score = 0.90  # 极度恐慌
            signal = f"资金费率极负({funding_rate:.4%})--市场恐慌"
    elif abs(funding_rate) > 0.001:
        if funding_rate > 0:
            fear_score = 0.25
            signal = f"资金费率偏高({funding_rate:.4%})--偏贪婪"
        else:
            fear_score = 0.75
            signal = f"资金费率偏低({funding_rate:.4%})--偏恐慌"
    elif abs(funding_rate) > 0.0005:
        if funding_rate > 0:
            fear_score = 0.40
            signal = f"资金费率略高({funding_rate:.4%})"
        else:
            fear_score = 0.60
            signal = f"资金费率略低({funding_rate:.4%})"

    # 费率趋势：快速切换说明情绪不稳定
    trend_signal = ""
    if funding_rate_history and len(funding_rate_history) >= 4:
        try:
            fr_vals = []
            for item in funding_rate_history[:4]:
                try:
                    fr_vals.append(float(item.get("fundingRate", 0)))
                except (ValueError, TypeError):
                    continue
            if len(fr_vals) >= 2:
                signs = [1 if v > 0.0001 else (-1 if v < -0.0001 else 0) for v in fr_vals]
                changes = sum(1 for i in range(1, len(signs)) if signs[i] != signs[i-1])
                if changes >= 2:
                    trend_signal = "费率方向频繁切换--情绪不稳定"
        except Exception:
            pass

    return {"fear_score": fear_score, "signal": signal, "trend_signal": trend_signal}


# ==================== 4. 综合情绪打分 ====================

def compute_sentiment_fear_gauge(
    funding_rate: float = 0.0,
    funding_rate_history: Optional[List] = None,
    option_pcr_data: Optional[List] = None,
    ls_ratio: float = 0.0,
) -> Dict:
    """
    综合情绪恐慌指数：0~1，1=极度恐慌（配合洗盘检测的诱空信号）。
    
    权重分配：
    - Fear & Greed Index: 35%
    - PCR 代理: 25%
    - 资金费率情绪: 20%
    - 散户拥挤度代理: 20%
    
    返回标准化结果:
    {
        "fear_gauge": 0~1,
        "fear_greed_value": 0~100,
        "classification": "极度恐慌/恐慌/中性/贪婪/极度贪婪",
        "signals": [...],
        "components": {...}
    }
    """
    signals = []
    components = {}

    # 1. Fear & Greed Index (0~100, 越低越恐慌)
    fng = fetch_fear_greed_index()
    fng_value = fng.get("value", 50)
    fng_class = fng.get("classification", "Neutral")
    # 转换为 0~1 恐慌度：0=greedy, 1=fearful
    fng_fear = 1.0 - (fng_value / 100.0)
    components["fear_greed_index"] = {
        "raw_value": fng_value,
        "fear_score": round(fng_fear, 3),
    }
    if fng_value <= 25:
        signals.append(f"Fear&Greed极度恐慌({fng_value})")
    elif fng_value >= 75:
        signals.append(f"Fear&Greed极度贪婪({fng_value})")

    # 2. PCR 情绪代理
    pcr_sent = compute_pcr_sentiment(option_pcr_data=option_pcr_data)
    pcr_fear = pcr_sent["fear_score"]
    components["pcr_sentiment"] = {"fear_score": round(pcr_fear, 3)}
    if pcr_sent.get("extreme"):
        signals.append(pcr_sent["signal"])

    # 3. 资金费率情绪
    fund_sent = compute_funding_sentiment(funding_rate, funding_rate_history)
    fund_fear = fund_sent["fear_score"]
    components["funding_sentiment"] = {"fear_score": round(fund_fear, 3)}
    if fund_sent["signal"]:
        signals.append(fund_sent["signal"])
    if fund_sent.get("trend_signal"):
        signals.append(fund_sent["trend_signal"])

    # 4. 散户拥挤度代理 (ls_ratio)
    # 散户极度做多 = 贪婪；散户极度做空 = 恐慌
    if ls_ratio > 3.0:
        retail_fear = 0.10  # 散户疯狂做多 = 市场贪婪
        signals.append(f"散户拥挤做多(ls={ls_ratio:.1f})--情绪贪婪")
    elif ls_ratio > 2.0:
        retail_fear = 0.25
    elif ls_ratio < 0.33:
        retail_fear = 0.85  # 散户疯狂做空 = 市场恐慌
        signals.append(f"散户拥挤做空(ls={ls_ratio:.1f})--情绪恐慌")
    elif ls_ratio < 0.5:
        retail_fear = 0.70
    else:
        retail_fear = 0.50
    components["retail_crowd"] = {"fear_score": round(retail_fear, 3)}

    # 综合权重
    fear_gauge = (
        fng_fear * 0.35
        + pcr_fear * 0.25
        + fund_fear * 0.20
        + retail_fear * 0.20
    )
    fear_gauge = max(0.0, min(1.0, fear_gauge))

    # 分类
    if fear_gauge >= 0.80:
        classification = "极度恐慌"
    elif fear_gauge >= 0.60:
        classification = "恐慌"
    elif fear_gauge >= 0.40:
        classification = "中性"
    elif fear_gauge >= 0.20:
        classification = "贪婪"
    else:
        classification = "极度贪婪"

    return {
        "fear_gauge": round(fear_gauge, 3),
        "fear_greed_value": fng_value,
        "classification": classification,
        "signals": signals,
        "components": components,
    }


# ==================== 5. 测试入口 ====================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = compute_sentiment_fear_gauge()
    print(json.dumps(result, ensure_ascii=False, indent=2))
