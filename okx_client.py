# okx_client.py
"""简化 OKX REST API 客户端 —— 日线分析专用（K线/Ticker + 资金费率/OI/多空比）"""

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests

from config import Config

logger = logging.getLogger(__name__)

BASE_URLS = {
    "global":     "https://www.okx.com",
    "aws":        "https://aws.okx.com",
    "demo":       "https://www.okx.com",
    "demo-aws":   "https://aws.okx.com",
}


class OKXClient:
    """OKX REST 客户端，带签名，只暴露日线分析需要的接口。"""

    def __init__(self, config: Config):
        self._api_key = config.OKX_API_KEY
        self._secret = config.OKX_SECRET_KEY
        self._passphrase = config.OKX_PASSPHRASE
        self._simulated = config.SIMULATED
        self._base = BASE_URLS.get(config.SITE, BASE_URLS["global"])

        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        if self._simulated == "1":
            self._session.headers["x-simulated-trading"] = "1"

        # 底层连接重试适配器（处理 SSL/DNS/连接池耗尽）
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=5, pool_maxsize=10)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    # ── 签名 ────────────────────────────────────────────

    def _sign(self, method: str, path: str, body: str = "") -> Tuple[str, str]:
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        prehash = ts + method.upper() + path + body
        sign = base64.b64encode(
            hmac.new(
                self._secret.encode("utf-8"),
                prehash.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        return ts, sign

    def _request(self, method: str, path: str, params: dict = None) -> Any:
        """发送签名请求，自动处理分页 + 网络重试。

        适用于返回 List[List] 的接口（如 K 线、资金费率），
        以每行第一个元素作为分页游标。
        """
        all_results: List[Any] = []
        page_before = ""
        url = self._base + path
        if params is None:
            params = {}

        while True:
            p = dict(params)
            if page_before:
                p["before"] = page_before

            body = json.dumps(p) if method.upper() == "POST" else ""
            ts, sign = self._sign(method, path + ("?" + urlencode(p) if p and method == "GET" else ""), body)
            headers = {
                "OK-ACCESS-KEY": self._api_key,
                "OK-ACCESS-SIGN": sign,
                "OK-ACCESS-TIMESTAMP": ts,
                "OK-ACCESS-PASSPHRASE": self._passphrase,
            }

            resp = self._send_retry(method, url, p, body, headers)
            if resp is None:
                return all_results if all_results else None

            if resp.status_code == 429:
                logger.warning("速率限制，等待 2 秒后重试...")
                time.sleep(2)
                continue

            try:
                data = resp.json()
            except Exception:
                logger.error(f"解析响应失败: {resp.text[:300]}")
                return all_results if all_results else None

            code = data.get("code", "")
            if code != "0":
                logger.error(f"API 返回错误 code={code} msg={data.get('msg')}")
                return all_results if all_results else None

            chunk = data.get("data", [])
            if chunk:
                all_results.extend(chunk)

            # ~ 没有更多数据则停止
            if len(chunk) < 100:
                break
            # 分页游标：仅当 chunk 是 list-of-list 时可用
            try:
                page_before = chunk[-1][0] if chunk else ""
            except (KeyError, TypeError, IndexError):
                break  # 非标准列表格式（如 dict 列表），不分页

        return all_results

    def _request_simple(self, method: str, path: str, params: dict = None) -> Any:
        """发送签名请求（不处理分页），适用于全量返回接口（如 tickers）。"""
        url = self._base + path
        if params is None:
            params = {}
        p = dict(params)

        body = json.dumps(p) if method.upper() == "POST" else ""
        ts, sign = self._sign(method, path + ("?" + urlencode(p) if p and method == "GET" else ""), body)
        headers = {
            "OK-ACCESS-KEY": self._api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self._passphrase,
        }

        resp = self._send_retry(method, url, p, body, headers)
        if resp is None:
            return None

        if resp.status_code == 429:
            logger.warning("速率限制，等待 2 秒后重试...")
            time.sleep(2)
            return self._request_simple(method, path, params)

        try:
            data = resp.json()
        except Exception:
            logger.error(f"解析响应失败: {resp.text[:300]}")
            return None

        code = data.get("code", "")
        if code != "0":
            logger.error(f"API 返回错误 code={code} msg={data.get('msg')}")
            return None

        return data.get("data", [])

    def _send_retry(self, method: str, url: str, params: dict,
                    body: str, headers: dict, max_retries: int = 3) -> Any:
        """发送 HTTP 请求，网络错误自动重试（指数退避）。"""
        last_error = None
        for attempt in range(max_retries):
            try:
                if method.upper() == "GET":
                    return self._session.get(url, params=params, headers=headers, timeout=30)
                else:
                    return self._session.post(url, data=body, headers=headers, timeout=30)
            except requests.RequestException as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)  # 2s → 4s → 8s
                    logger.warning(f"网络错误 (第{attempt+1}次), {wait}秒后重试: {e}")
                    time.sleep(wait)
                else:
                    logger.error(f"请求失败(已重试{max_retries}次) {method} {url}: {e}")
        return None

    # ── 公共接口 ────────────────────────────────────────

    def get_klines(self, symbol: str, bar: str = "1D", limit: int = 150) -> List[List[str]]:
        """
        获取 K 线数据。
        bar: 1D, 4H, 1H, 15m, 5m, 1m 等
        """
        return self._request("GET", "/api/v5/market/candles", {
            "instId": symbol,
            "bar": bar,
            "limit": str(min(limit, 300)),
        })

    def get_ticker(self, symbol: str) -> Optional[Dict[str, str]]:
        """获取最新行情。"""
        data = self._request("GET", "/api/v5/market/ticker", {"instId": symbol})
        return data[0] if data else None

    # ── 情绪数据接口（公开API） ──────────────────────────

    def get_funding_rate_history(self, instId: str, limit: int = 90) -> List[Dict]:
        """获取资金费率历史（按时间升序），每期约8小时。"""
        data = self._request("GET", "/api/v5/public/funding-rate-history", {
            "instId": instId,
            "limit": str(min(limit, 100)),
        })
        if not data:
            return []
        return sorted(data, key=lambda x: str(x.get("fundingTime", "")))

    def get_open_interest(self, instId: str, period: str = "1D", limit: int = 90) -> List[Dict]:
        """获取持仓量历史（按时间升序），返回最近N条日级OI数据。"""
        ccy = instId.split("-")[-2] if len(instId.split("-")) >= 2 else "USDT"
        data = self._request("GET", "/api/v5/rubik/stat/contracts/open-interest-volume", {
            "instId": instId,
            "ccy": ccy,
            "period": period,
            "limit": str(min(limit, 100)),
        })
        if not data:
            return []
        return sorted(data, key=lambda x: str(x.get("ts", "")))

    def get_long_short_ratio(self, instId: str, period: str = "1D", limit: int = 90) -> List[Dict]:
        """获取多空人数比历史（按时间升序）。"""
        ccy = instId.split("-")[-2] if len(instId.split("-")) >= 2 else "USDT"
        data = self._request("GET", "/api/v5/rubik/stat/contracts/long-short-account-ratio", {
            "instId": instId,
            "ccy": ccy,
            "period": period,
            "limit": str(min(limit, 100)),
        })
        if not data:
            return []
        return sorted(data, key=lambda x: str(x.get("ts", "")))

    # ── 全市场行情 ──────────────────────────────────────

    def get_tickers(self, instType: str = "SWAP") -> List[Dict]:
        """获取全市场行情快照（全量返回，不需分页）。"""
        return self._request_simple("GET", "/api/v5/market/tickers", {"instType": instType}) or []

    def get_top_swaps(self, min_vol_usd: float = 100_000_000, top_n: int = 15,
                       min_price: float = 0.001) -> List[Dict]:
        """获取 24h 成交额最高的 USDT 永续合约列表。

        Args:
            min_vol_usd: 最小 24h 成交额（美元），默认 1 亿
            top_n: 返回前 N 个
            min_price: 最低价格阈值，排除极低价 meme 币（volCcy24h 值失真）

        Returns:
            [{instId, last, volCcy24h, volCcy24h_usd, vol_fmt}]
        """
        data = self.get_tickers("SWAP")
        if not data:
            return []

        usdt_swaps = []
        for t in data:
            instId = t.get("instId", "")
            if not instId.endswith("-USDT-SWAP"):
                continue

            # 价格过滤：排除 BONK/PEPE/SHIB/SATS 等微价币，它们的 volCcy24h 失真
            try:
                last_price = float(t.get("last", "0"))
            except (ValueError, TypeError):
                last_price = 0.0
            if last_price > 0 and last_price < min_price:
                continue

            # 计算实际 24h 成交额（美元）
            vol_str = t.get("volCcy24h", "0")
            try:
                vol_usd = float(vol_str)
            except (ValueError, TypeError):
                vol_usd = 0.0

            # 极端异常值检测：单币成交额不可能超过 $1 万亿
            if vol_usd > 1_000_000_000_000:
                continue

            if vol_usd >= min_vol_usd:
                usdt_swaps.append({
                    "instId": instId,
                    "last": t.get("last", "0"),
                    "volCcy24h": vol_str,
                    "volCcy24h_usd": vol_usd,
                    "vol_fmt": _fmt_volume(vol_usd),
                })

        usdt_swaps.sort(key=lambda x: x["volCcy24h_usd"], reverse=True)
        return usdt_swaps[:top_n]

    def get_top_swaps_by_market_cap(self, min_mcap: float = 100_000_000,
                                     top_n: int = 15, min_price: float = 0.001) -> List[Dict]:
        """按市值排序获取 USDT 永续合约列表（CoinGecko 市值 + OKX 行情交叉）。

        流程：
          1. CoinGecko 拉取市值 Top 250
          2. 取 OKX 全量 SWAP ticker
          3. 按 symbol 交叉匹配，筛选市值 > min_mcap
          4. 按市值降序取 top_n

        Args:
            min_mcap: 最小市值（美元），默认 1 亿
            top_n: 返回前 N 个
            min_price: 最低价格阈值
        """
        import urllib.request, urllib.error

        # ── Step 1: CoinGecko 市值榜 ──
        cg_url = (
            "https://api.coingecko.com/api/v3/coins/markets"
            "?vs_currency=usd&order=market_cap_desc&per_page=250&page=1"
            "&sparkline=false&price_change_percentage=24h"
        )
        try:
            req = urllib.request.Request(cg_url, headers={"accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                cg_data = json.loads(resp.read().decode())
        except Exception as e:
            logger.warning(f"CoinGecko 市值数据获取失败: {e}")
            # 回退到按 24h 成交额排序
            logger.info("回退到按 24h 成交额排序")
            return self.get_top_swaps(min_vol_usd=100_000_000, top_n=top_n, min_price=min_price)

        # 建立 symbol → market_cap 映射（CG symbol 为小写）
        cg_map: Dict[str, Dict] = {}
        for coin in cg_data:
            sym = coin.get("symbol", "").lower()
            mcap = coin.get("market_cap") or 0
            if sym and mcap >= min_mcap:
                cg_map[sym] = {
                    "market_cap": mcap,
                    "name": coin.get("name", sym),
                    "cg_id": coin.get("id", sym),
                    "price_change_24h": coin.get("price_change_percentage_24h"),
                    "total_volume": coin.get("total_volume") or 0,
                }
        logger.info(f"CoinGecko 返回 {len(cg_data)} 币种, 市值>{min_mcap/1e6:.0f}M 共 {len(cg_map)} 个")

        # ── Step 2: OKX SWAP ticker ──
        okx_data = self.get_tickers("SWAP")
        if not okx_data:
            logger.warning("OKX ticker 获取失败，回退到成交额排序")
            return self.get_top_swaps(top_n=top_n)

        # ── Step 3: 交叉匹配 ──
        matched = []
        for t in okx_data:
            instId = t.get("instId", "")
            if not instId.endswith("-USDT-SWAP"):
                continue
            # 提取 symbol: "DOGE-USDT-SWAP" → "doge"
            okx_sym = instId.replace("-USDT-SWAP", "").lower()

            cg_info = cg_map.get(okx_sym)
            if cg_info is None:
                continue

            # 价格过滤
            try:
                last_price = float(t.get("last", "0"))
            except (ValueError, TypeError):
                last_price = 0.0
            if last_price > 0 and last_price < min_price:
                continue

            vol_str = t.get("volCcy24h", "0")
            try:
                vol_usd = float(vol_str)
            except (ValueError, TypeError):
                vol_usd = 0.0

            if vol_usd > 1_000_000_000_000:
                continue

            matched.append({
                "instId": instId,
                "last": t.get("last", "0"),
                "volCcy24h": vol_str,
                "volCcy24h_usd": vol_usd,
                "vol_fmt": _fmt_volume(vol_usd),
                "market_cap": cg_info["market_cap"],
                "mcap_fmt": _fmt_volume(cg_info["market_cap"]),
                "name": cg_info["name"],
                "price_change_24h": cg_info.get("price_change_24h"),
            })

        if not matched:
            logger.warning("CoinGecko 与 OKX 无匹配币种（市值>100M），回退到成交额排序")
            return self.get_top_swaps(top_n=top_n)

        # ── Step 4: 按市值降序，取 top_n ──
        matched.sort(key=lambda x: x["market_cap"], reverse=True)
        result = matched[:top_n]
        logger.info(f"市值排序匹配: {len(matched)} 个, 返回 Top {len(result)}")
        for i, r in enumerate(result):
            logger.info(f"  {i+1}. {r['instId']}  市值 {r['mcap_fmt']}  24h成交 {r['vol_fmt']}")
        return result

    # ── 解析辅助 ────────────────────────────────────────

    @staticmethod
    def parse_klines(raw: List[List[str]]) -> List[Dict[str, float]]:
        """
        将 OKX K线原始数据转为标准字典列表（按时间升序）。
        原始: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        """
        if not raw:
            return []
        rows = []
        for r in raw:
            rows.append({
                "timestamp": int(r[0]),
                "open":      float(r[1]),
                "high":      float(r[2]),
                "low":       float(r[3]),
                "close":     float(r[4]),
                "vol":       float(r[5]),
                "vol_ccy":   float(r[6]),
            })
        return sorted(rows, key=lambda x: x["timestamp"])


def _fmt_volume(usd: float) -> str:
    """将美元金额格式化为人类可读形式。"""
    if usd >= 1_000_000_000:
        return f"${usd / 1_000_000_000:.2f}B"
    elif usd >= 1_000_000:
        return f"${usd / 1_000_000:.2f}M"
    else:
        return f"${usd:,.0f}"
