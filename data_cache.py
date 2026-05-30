# data_cache.py
"""
数据缓存降级模块（P1 优化）

为辅助数据的 15 个接口提供分级缓存与失败降级：
- 每个数据源独立配置缓存 TTL
- 失败时自动返回上周期缓存值，避免因个别接口超时导致分析断流
- 对变化缓慢的数据（保险基金、持仓档位、期权 PCR）延长缓存有效期，减少无效请求

缓存策略分级：
    TTL=0 (不缓存):       orderbook, funding_rate, mark_price, price_limit
    TTL=60s (快速变化):    OI, taker_vol, long_short, elite, premium
    TTL=120s (中速变化):   elite_pos, index_tickers, funding_hist
    TTL=300s (慢速变化):   option_pcr, option_oi_strike
    TTL=600s+ (极慢变化):  insurance, position_tiers, elite_trend
"""
import time
import logging
from typing import Any, Dict, Optional, Tuple
from threading import Lock

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  缓存配置
# ════════════════════════════════════════════════════════════

# key → (ttl_seconds, description)
CACHE_CONFIG: Dict[str, Tuple[int, str]] = {
    # 实时数据（不缓存，每次拉新）
    "orderbook":            (0,   "订单簿"),
    "funding":              (0,   "资金费率"),
    "mark":                 (0,   "标记价格"),
    "limit":                (0,   "涨跌停限制"),

    # 快速变化（60秒缓存）
    "oi_data":              (60,  "持仓量"),
    "taker_vol":            (60,  "Taker买卖量"),
    "long_short":           (60,  "多空人数比"),
    "elite":                (60,  "精英多空人数比"),
    "premium":              (60,  "溢价指数"),

    # 多周期数据（300秒缓存 — P1优化：从0延长，减少API调用）
    "oi_data_15m":          (300, "持仓量(15m)"),
    "oi_data_1H":           (300, "持仓量(1H)"),
    "oi_data_4H":           (300, "持仓量(4H)"),
    "taker_vol_15m":        (300, "Taker买卖量(15m)"),
    "taker_vol_1H":         (300, "Taker买卖量(1H)"),
    "taker_vol_4H":         (300, "Taker买卖量(4H)"),
    "long_short_15m":       (300, "多空人数比(15m)"),
    "long_short_1H":        (300, "多空人数比(1H)"),
    "long_short_4H":        (300, "多空人数比(4H)"),
    # 多周期基差数据（300秒 TTL）
    "index_candles_5m":     (300, "指数K线(5m)"),
    "index_candles_1H":     (300, "指数K线(1H)"),
    "mark_candles_5m":      (300, "标记价K线(5m)"),

    # 中速变化（120秒缓存）
    "elite_pos":            (120, "精英仓位比"),
    "index_tickers":        (120, "指数行情"),
    "funding_hist":         (120, "资金费率历史"),

    # 慢速变化（300秒缓存）
    "option_pcr":           (300, "期权PCR"),
    "option_oi_strike":     (300, "期权OI行权价分布"),

    # 极慢变化（600秒缓存）
    "insurance":            (600, "保险基金"),
    "position_tiers":       (600, "持仓档位"),
    "elite_trend":          (600, "精英多周期趋势"),
}


class DataCache:
    """线程安全的轻量级内存缓存，支持 TTL 过期。"""

    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}  # key → {value, ts, ttl}
        self._lock = Lock()

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值，若过期则返回 None。"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None

            ttl = entry.get("ttl", 0)
            if ttl > 0 and (time.time() - entry.get("ts", 0)) > ttl:
                # 已过期，保留值但标记过期（可作为降级使用）
                entry["expired"] = True
                return None  # 调用方知道你过期了

            return entry.get("value")

    def get_fallback(self, key: str) -> Optional[Any]:
        """获取缓存值（即使已过期），用于降级。"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            return entry.get("value")

    def set(self, key: str, value: Any, ttl: int = 0):
        """写入缓存。ttl=0 表示永不过期（但一般不设0）。"""
        with self._lock:
            self._store[key] = {
                "value": value,
                "ts": time.time(),
                "ttl": ttl,
                "expired": False,
            }

    def status(self) -> Dict[str, Dict]:
        """返回缓存状态快照（供调试/面板使用）。"""
        with self._lock:
            result = {}
            now = time.time()
            for key, entry in self._store.items():
                age = now - entry.get("ts", 0)
                ttl = entry.get("ttl", 0)
                result[key] = {
                    "age_s": round(age, 1),
                    "ttl_s": ttl,
                    "fresh": ttl == 0 or age < ttl,
                    "has_value": entry.get("value") is not None,
                }
            return result


# 模块级单例
_cache = DataCache()


def get_cache() -> DataCache:
    """返回全局缓存单例。"""
    return _cache


def fetch_or_cache(
    key: str,
    fetch_fn,
    *args,
    **kwargs,
) -> Any:
    """
    拉取数据，优先用缓存；失败时降级到过期缓存。

    用法:
        orderbook = fetch_or_cache("orderbook", client.get_orderbook, symbol, depth)

    流程:
        1. 查询缓存（检查 TTL）
        2. 若缓存有效 → 直接返回
        3. 调用 fetch_fn 拉取新数据
        4. 成功 → 写入缓存 + 返回
        5. 失败 → 尝试过期缓存降级 → 均无 → 返回 None
    """
    cache = get_cache()
    ttl, desc = CACHE_CONFIG.get(key, (0, "未知"))

    # TTL=0 表示不缓存，每次直接拉
    if ttl <= 0:
        try:
            val = fetch_fn(*args, **kwargs)
            return val
        except Exception as e:
            logger.debug(f"[缓存降级] {desc}({key}) 拉取失败(无缓存): {e}")
            return None

    # 检查新鲜缓存
    cached = cache.get(key)
    if cached is not None:
        return cached  # 命中

    # 拉取新数据
    try:
        val = fetch_fn(*args, **kwargs)
        cache.set(key, val, ttl=ttl)
        return val
    except Exception as e:
        # 降级：尝试过期缓存
        fallback = cache.get_fallback(key)
        if fallback is not None:
            logger.warning(
                f"[缓存降级] {desc}({key}) 拉取失败，使用过期缓存（{ttl}s TTL已过）: {e}"
            )
            return fallback
        logger.warning(f"[缓存降级] {desc}({key}) 拉取失败且无缓存: {e}")
        return None


def clear_cache(*keys: str):
    """清除指定 key 的缓存。不传参则清空全部。"""
    cache = get_cache()
    if not keys:
        with cache._lock:
            cache._store.clear()
        logger.info("[缓存] 已清空全部缓存")
        return
    with cache._lock:
        for key in keys:
            cache._store.pop(key, None)
    logger.info(f"[缓存] 已清空: {keys}")
