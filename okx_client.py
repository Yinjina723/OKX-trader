# okx_client.py
"""
OKX API 客户端：封装与 OKX 的 REST 通信（签名、重试、熔断）及各类接口。

- 行情/公共：K 线、Ticker、订单簿、资金费率、标记价、涨跌停、合约信息等
- 账户/交易相关：余额、账户配置(posMode)、杠杆信息、手续费(trade-fee)、强平价(adjust-leverage-info)
- 辅助数据：持仓量、Taker 成交量、多空比、精英多空比、溢价历史等
- 部分接口会缓存到 AUX_DATA_DIR 的 CSV，减少重复请求
- P2: 网络熔断器 — 连续失败自动熔断，避免 GFW 阻断时反复重试浪费时间
"""
import hmac
import base64
import json
import time
import requests
import logging
import os
import csv
import shutil
import glob
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
from threading import Lock

from config import Config

logger = logging.getLogger(__name__)


# ==================== P2: 网络熔断器 ====================

class CircuitBreaker:
    """
    轻量级熔断器，防止连续网络故障时反复重试浪费时间和 API 额度。
    
    状态机: CLOSED → (连续失败 N 次) → OPEN → (冷却 T 秒) → HALF_OPEN → (成功) → CLOSED
    """
    
    def __init__(self, name: str = "default", failure_threshold: int = 5, cooldown_seconds: float = 60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._state = "CLOSED"  # CLOSED / OPEN / HALF_OPEN
        self._lock = Lock()
    
    @property
    def is_open(self) -> bool:
        """熔断器是否开启（阻止请求）。"""
        with self._lock:
            if self._state == "CLOSED":
                return False
            if self._state == "OPEN":
                if time.time() - self._last_failure_time >= self.cooldown_seconds:
                    self._state = "HALF_OPEN"
                    logger.info(f"[熔断器:{self.name}] 冷却完成，进入半开状态，尝试恢复")
                    return False
                return True
            # HALF_OPEN: 允许通过
            return False
    
    def record_success(self):
        """记录一次成功，关闭熔断器。"""
        with self._lock:
            if self._state != "CLOSED":
                logger.info(f"[熔断器:{self.name}] 请求成功，熔断器关闭")
            self._failure_count = 0
            self._state = "CLOSED"
    
    def record_failure(self):
        """记录一次失败，累计到阈值则熔断。"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                if self._state != "OPEN":
                    logger.warning(
                        f"[熔断器:{self.name}] 连续失败 {self._failure_count} 次，"
                        f"熔断 {self.cooldown_seconds}s"
                    )
                self._state = "OPEN"
    
    def status(self) -> Dict:
        """返回熔断器状态快照。"""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state,
                "failure_count": self._failure_count,
                "cooldown_remaining": max(0, self.cooldown_seconds - (time.time() - self._last_failure_time)),
            }


# 全局熔断器实例（可按 endpoint 分组）
_circuit_breakers: Dict[str, CircuitBreaker] = {}
_cb_lock = Lock()


def get_circuit_breaker(name: str = "default") -> CircuitBreaker:
    """获取或创建指定名称的熔断器。"""
    with _cb_lock:
        if name not in _circuit_breakers:
            _circuit_breakers[name] = CircuitBreaker(name=name)
        return _circuit_breakers[name]

class OKXClient:
    """OKX 官方 REST API 封装。支持实盘/模拟盘(SIMULATED)，请求带签名与重试。"""
    BASE_URL = "https://www.okx.com"

    def __init__(self, config: Config):
        self.config = config
        self.api_key = config.OKX_API_KEY
        self.secret_key = config.OKX_SECRET_KEY
        self.passphrase = config.OKX_PASSPHRASE

        raw_sim = config.SIMULATED
        if isinstance(raw_sim, bool):
            self.simulated = "1" if raw_sim else "0"
        elif isinstance(raw_sim, (int, float)):
            self.simulated = "1" if raw_sim == 1 else "0"
        else:
            self.simulated = str(raw_sim).strip()
            if self.simulated not in ("0", "1"):
                logger.warning(f"SIMULATED 配置值 '{raw_sim}' 无效，将使用默认值 '0'（实盘）")
                self.simulated = "0"

        self.max_retries = 5
        self.retry_delay = 2
        # 网络请求超时(秒)，中国大陆访问 OKX 建议 30s
        self.request_timeout = getattr(config, 'REQUEST_TIMEOUT', 30)

        # P2: 熔断器（全局共享，区分公共接口 / 私有接口）
        self._public_breaker = get_circuit_breaker("okx_public")
        self._private_breaker = get_circuit_breaker("okx_private")

        # 使用连接池复用 TCP/SSL 连接，降低 SSL 握手被干扰概率
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) OKX-Client/1.0'
        })

        self.history_dir = config.HISTORY_DIR
        self.aux_dir = config.AUX_DATA_DIR
        os.makedirs(self.history_dir, exist_ok=True)
        os.makedirs(self.aux_dir, exist_ok=True)

        # 辅助数据滚动配置
        self.max_aux_rows = getattr(config, 'MAX_AUX_ROWS', 5000)   # 每个文件最大行数（含表头）
        self.max_aux_files = getattr(config, 'MAX_AUX_FILES', 10)   # 最多保留的文件数

        logger.info(f"OKXClient 初始化完成，模拟盘模式: {self.simulated}")
        logger.info(f"数据缓存目录: {self.history_dir} (K线), {self.aux_dir} (辅助数据)")
        logger.info(f"辅助数据滚动配置: 最大行数 {self.max_aux_rows}, 最多保留 {self.max_aux_files} 个文件")

    # ================== 内部辅助方法 ==================
    def _generate_signature(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(
            bytes(self.secret_key, encoding='utf8'),
            bytes(message, encoding='utf-8'),
            digestmod='sha256'
        )
        return base64.b64encode(mac.digest()).decode()

    def _build_request_headers(self, method: str, request_path: str, body: str = "") -> Dict[str, str]:
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        headers = {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': self._generate_signature(timestamp, method, request_path, body),
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }
        if self.simulated == "1":
            headers['x-simulated-trading'] = '1'
        logger.debug(f"请求头: {headers}")
        return headers

    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, body: Optional[Dict] = None) -> Dict:
        request_path = endpoint
        if params and method.upper() == 'GET':
            query_string = '&'.join([f"{k}={v}" for k, v in params.items() if v is not None])
            if query_string:
                request_path += '?' + query_string

        body_str = json.dumps(body) if body and method.upper() == 'POST' else ""

        if self.api_key and self.secret_key and self.passphrase:
            headers = self._build_request_headers(method, request_path, body_str)
            breaker = self._private_breaker
        else:
            headers = {'Content-Type': 'application/json'}
            breaker = self._public_breaker

        url = self.BASE_URL + endpoint
        request_params = {k: v for k, v in (params or {}).items() if v is not None}

        # P2: 熔断器检查 — 已熔断则快速失败
        if breaker.is_open:
            raise requests.exceptions.ConnectionError(
                f"[熔断器:{breaker.name}] 已熔断，跳过请求 {endpoint}"
            )

        for attempt in range(self.max_retries):
            try:
                if method.upper() == 'GET':
                    resp = self.session.get(url, params=request_params, headers=headers,
                                           timeout=self.request_timeout)
                elif method.upper() == 'POST':
                    resp = self.session.post(url, json=body, headers=headers,
                                            timeout=self.request_timeout)
                else:
                    raise ValueError(f"不支持的请求方法: {method}")

                if resp.status_code != 200:
                    error_msg = f"HTTP {resp.status_code}: {resp.text}"
                    logger.error(error_msg)
                    raise requests.exceptions.HTTPError(error_msg, response=resp)

                data = resp.json()
                if data.get("code") != "0":
                    err_code = data.get("code")
                    err_msg = data.get("msg", "未知错误")
                    # 尝试从 data.data[0].sCode/sMsg 中拿到更具体的错误
                    detail = ""
                    try:
                        first = (data.get("data") or [])[0] or {}
                        s_code = first.get("sCode")
                        s_msg = first.get("sMsg")
                        if s_code not in (None, "", "0") or s_msg:
                            detail = f"；子错误 [{s_code}]: {s_msg}"
                    except Exception:
                        detail = ""
                    full_msg = f"API 错误 [{err_code}]: {err_msg}{detail}"
                    if err_code == "50101":
                        full_msg += "。可能原因：API Key 环境与请求头不匹配，请检查 config.json 中的 SIMULATED 值（1=模拟盘，0=实盘）以及 API Key 是否在正确的环境中创建。"
                    logger.error(full_msg)
                    raise Exception(full_msg)

                # P2: 请求成功 → 关闭熔断器
                breaker.record_success()
                return data

            except requests.exceptions.RequestException as e:
                logger.warning(f"请求失败 (尝试 {attempt+1}/{self.max_retries}): {e}")
                if hasattr(e, 'response') and e.response is not None:
                    logger.warning(f"响应状态码: {e.response.status_code}")
                    logger.warning(f"响应内容: {e.response.text}")
                if attempt < self.max_retries - 1:
                    wait = self.retry_delay * (2 ** attempt)  # 2,4,8,16秒
                    logger.debug(f"重试冷却 {wait}s ...")
                    time.sleep(wait)
                else:
                    # P2: 所有重试耗尽 → 记录失败到熔断器
                    breaker.record_failure()
                    raise
            except Exception as e:
                logger.warning(f"未知错误 (尝试 {attempt+1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    wait = self.retry_delay * (2 ** attempt)
                    time.sleep(wait)
                else:
                    # P2: 所有重试耗尽 → 记录失败到熔断器
                    breaker.record_failure()
                    raise

    # ================== 账户相关接口 ==================

    def get_account_balance(self, ccy: Optional[str] = None) -> Dict[str, Any]:
        """
        获取交易账户余额信息，对应 GET /api/v5/account/balance。
        返回 OKX 原始 JSON 结构。
        """
        endpoint = "/api/v5/account/balance"
        params: Dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        return self._request("GET", endpoint, params)

    def get_account_config(self) -> Dict[str, Any]:
        """
        获取账户配置，对应 GET /api/v5/account/config。
        主要用来读取 posMode（long_short_mode 或 net_mode）。
        """
        endpoint = "/api/v5/account/config"
        return self._request("GET", endpoint, {})

    def get_adjust_leverage_info(
        self,
        instType: str,
        mgnMode: str,
        lever: float | str,
        instId: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取指定杠杆倍数下的预估信息（包括预估强平价等），对应
        GET /api/v5/account/adjust-leverage-info。
        """
        endpoint = "/api/v5/account/adjust-leverage-info"
        params: Dict[str, Any] = {
            "instType": instType,
            "mgnMode": mgnMode,
            "lever": str(lever),
        }
        if instId:
            params["instId"] = instId
        return self._request("GET", endpoint, params)

    def get_leverage_info(self, instId: str, mgnMode: str) -> Dict[str, Any]:
        """
        获取账户当前在某合约上的实际杠杆信息，
        对应 GET /api/v5/account/leverage-info。
        返回中通常包含 lever、mgnMode 等字段。
        """
        endpoint = "/api/v5/account/leverage-info"
        params: Dict[str, Any] = {"instId": instId, "mgnMode": mgnMode}
        return self._request("GET", endpoint, params)

    def get_trade_fee(self, instType: str = "SWAP", instId: Optional[str] = None) -> Dict[str, Any]:
        """
        获取当前账户在指定品种上的实际交易手续费率，对应
        GET /api/v5/account/trade-fee。
        典型返回字段包括 maker / taker（如 0.0002 表示 0.02%）。
        """
        endpoint = "/api/v5/account/trade-fee"
        params: Dict[str, Any] = {"instType": instType}
        if instId:
            params["instId"] = instId
        return self._request("GET", endpoint, params)

    def _get_cache_filepath(self, data_type: str, instId: str) -> str:
        safe_inst = instId.replace('-', '_').replace('/', '_')
        if data_type == 'klines':
            return os.path.join(self.history_dir, f"klines_{safe_inst}.csv")
        else:
            return os.path.join(self.aux_dir, f"{data_type}_{safe_inst}.csv")

    def _save_to_csv(self, data_type: str, instId: str, data: List, headers: List[str]):
        if not data:
            return
        filepath = self._get_cache_filepath(data_type, instId)
        base_path = Path(filepath)

        current_rows = 0  # 初始化

        # 获取当前文件行数（如果存在）
        if base_path.exists():
            with open(base_path, 'r', encoding='utf-8') as f:
                current_rows = sum(1 for _ in f)  # 包括表头

        # 非K线数据且需要滚动
        if data_type != 'klines' and self.max_aux_rows > 0:
            total_rows = current_rows + len(data)
            if total_rows > self.max_aux_rows:
                # 滚动文件：重命名当前文件，添加时间戳
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                new_name = base_path.stem + f"_{timestamp}" + base_path.suffix
                new_path = base_path.with_name(new_name)
                shutil.move(filepath, new_path)
                logger.info(f"辅助数据文件达到阈值，已重命名为 {new_name}")

                # 清理旧文件，只保留最近 max_aux_files 个文件
                if self.max_aux_files > 0:
                    pattern = base_path.stem + "_*" + base_path.suffix
                    files = sorted(base_path.parent.glob(pattern))
                    # 删除最旧的文件，直到数量小于 max_aux_files
                    while len(files) >= self.max_aux_files:
                        files[0].unlink()
                        logger.debug(f"删除旧辅助数据文件: {files[0]}")
                        files.pop(0)

                # 重置 current_rows 为0（新文件即将创建）
                current_rows = 0

        # 追加写入（新文件时写入表头）
        file_exists = base_path.exists() and current_rows > 0
        with open(filepath, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(headers)
            for row in data:
                if isinstance(row, dict):
                    row = [row.get(h, '') for h in headers]
                writer.writerow(row)
        logger.debug(f"已追加 {len(data)} 条记录到 {filepath}")

    def _load_latest_from_csv(self, data_type: str, instId: str, limit: int = 1,
                              max_age_hours: float = 24) -> List[List]:
        """
        从本地CSV文件中读取最新的 limit 条记录。
        注意：如果文件已滚动，只读取最新文件（即当前主文件）的数据。
        若最新文件数据不足，不会从历史文件中补充。
        """
        filepath = self._get_cache_filepath(data_type, instId)
        if not os.path.isfile(filepath):
            return []

        try:
            rows = []
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                headers = next(reader)  # 跳过表头
                for row in reader:
                    rows.append(row)

            if not rows:
                return []

            ts_col_idx = 0
            valid_rows = []
            now_ms = time.time() * 1000
            for row in rows:
                try:
                    ts = int(row[ts_col_idx])
                    if (now_ms - ts) <= max_age_hours * 3600 * 1000:
                        valid_rows.append(row)
                except (ValueError, IndexError):
                    continue

            valid_rows.sort(key=lambda x: int(x[ts_col_idx]), reverse=True)
            return valid_rows[:limit]

        except Exception as e:
            logger.warning(f"读取本地CSV失败 {filepath}: {e}")
            return []

    # ================== K线数据 ==================
    def get_klines(self, instId: str, bar: str, limit: int = 300, after: Optional[str] = None) -> List[List]:
        endpoint = "/api/v5/market/candles"
        params = {"instId": instId, "bar": bar, "limit": limit}
        if after:
            params["after"] = after
        data = self._request("GET", endpoint, params)
        result = data.get("data", [])

        if result:
            headers = ["open_time", "open", "high", "low", "close", "vol", "vol_ccy", "vol_quote", "confirm"]
            self._save_to_csv("klines", instId, result, headers)

        return result

    def get_orderbook(self, instId: str, sz: int = 1) -> Dict[str, Any]:
        endpoint = "/api/v5/market/books"
        params = {"instId": instId, "sz": sz}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [{}])[0] if data.get("data") else {}

        if result:
            save_data = [{
                'ts': result.get('ts', ''),
                'bids': json.dumps(result.get('bids', [])),
                'asks': json.dumps(result.get('asks', [])),
            }]
            headers = ['ts', 'bids', 'asks']
            self._save_to_csv("orderbook", instId, save_data, headers)

        return result

    def get_ticker(self, instId: str) -> Dict[str, Any]:
        endpoint = "/api/v5/market/ticker"
        params = {"instId": instId}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [{}])[0] if data.get("data") else {}
        return result

    def get_instrument_info(self, instId: str) -> Dict:
        endpoint = "/api/v5/public/instruments"
        params = {"instType": "SWAP", "instId": instId}
        data = self._request("GET", endpoint, params)
        if data.get("data"):
            return data["data"][0]
        return {}

    def get_funding_rate(self, instId: str) -> Dict[str, Any]:
        local = self._load_latest_from_csv('funding_rate', instId, limit=1, max_age_hours=24)
        if local:
            headers = ['ts', 'fundingRate', 'fundingTime', 'nextFundingRate', 'nextFundingTime']
            latest = dict(zip(headers, local[0]))
            logger.info(f"使用本地缓存资金费率数据，时间戳: {latest['ts']}")
            return latest

        endpoint = "/api/v5/public/funding-rate"
        params = {"instId": instId}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [{}])[0] if data.get("data") else {}

        if result:
            save_data = [{
                'ts': result.get('ts', ''),
                'fundingRate': result.get('fundingRate', ''),
                'fundingTime': result.get('fundingTime', ''),
                'nextFundingRate': result.get('nextFundingRate', ''),
                'nextFundingTime': result.get('nextFundingTime', ''),
            }]
            headers = ['ts', 'fundingRate', 'fundingTime', 'nextFundingRate', 'nextFundingTime']
            self._save_to_csv('funding_rate', instId, save_data, headers)

        return result

    def get_mark_price(self, instId: str) -> Dict[str, Any]:
        endpoint = "/api/v5/public/mark-price"
        params = {"instType": "SWAP", "instId": instId}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [{}])[0] if data.get("data") else {}

        if result:
            save_data = [{
                'ts': result.get('ts', ''),
                'markPx': result.get('markPx', ''),
            }]
            headers = ['ts', 'markPx']
            self._save_to_csv('mark_price', instId, save_data, headers)

        return result

    def get_price_limit(self, instId: str) -> Dict[str, Any]:
        local = self._load_latest_from_csv('price_limit', instId, limit=1, max_age_hours=1)
        if local:
            headers = ['ts', 'buyLmt', 'sellLmt', 'enabled']
            latest = dict(zip(headers, local[0]))
            logger.info(f"使用本地缓存限价数据，时间戳: {latest['ts']}")
            return latest

        endpoint = "/api/v5/public/price-limit"
        params = {"instId": instId}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [{}])[0] if data.get("data") else {}

        if result:
            save_data = [{
                'ts': result.get('ts', ''),
                'buyLmt': result.get('buyLmt', ''),
                'sellLmt': result.get('sellLmt', ''),
                'enabled': result.get('enabled', False),
            }]
            headers = ['ts', 'buyLmt', 'sellLmt', 'enabled']
            self._save_to_csv('price_limit', instId, save_data, headers)

        return result

    def get_open_interest_history(self, instId: str, period: str = "5m", limit: int = 2) -> List[List]:
        local = self._load_latest_from_csv('open_interest_history', instId, limit=limit, max_age_hours=24)
        if len(local) >= limit:
            logger.info(f"使用本地缓存持仓量历史数据")
            return local

        endpoint = "/api/v5/rubik/stat/contracts/open-interest-history"
        params = {"instId": instId, "period": period, "limit": limit}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [])

        if result:
            headers = ['ts', 'oi', 'oiCcy', 'oiUsd']
            self._save_to_csv('open_interest_history', instId, result, headers)

        return result

    def get_taker_volume_contract(self, instId: str, period: str = "5m", limit: int = 2) -> List[List]:
        local = self._load_latest_from_csv('taker_volume', instId, limit=limit, max_age_hours=24)
        if len(local) >= limit:
            logger.info(f"使用本地缓存主动买卖量数据")
            return local

        endpoint = "/api/v5/rubik/stat/taker-volume-contract"
        params = {"instId": instId, "period": period, "limit": limit}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [])

        if result:
            headers = ['ts', 'sellVol', 'buyVol']
            self._save_to_csv('taker_volume', instId, result, headers)

        return result

    def get_long_short_account_ratio(self, instId: str, period: str = "5m", limit: int = 2) -> List[List]:
        local = self._load_latest_from_csv('long_short_ratio', instId, limit=limit, max_age_hours=24)
        if len(local) >= limit:
            logger.info(f"使用本地缓存多空比数据")
            return local

        endpoint = "/api/v5/rubik/stat/contracts/long-short-account-ratio-contract"
        params = {"instId": instId, "period": period, "limit": limit}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [])

        if result:
            headers = ['ts', 'ratio']
            self._save_to_csv('long_short_ratio', instId, result, headers)

        return result

    def get_top_trader_long_short_account_ratio(self, instId: str, period: str = "5m", limit: int = 2) -> List[List]:
        local = self._load_latest_from_csv('elite_ratio', instId, limit=limit, max_age_hours=24)
        if len(local) >= limit:
            logger.info(f"使用本地缓存精英多空比数据")
            return local

        endpoint = "/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader"
        params = {"instId": instId, "period": period, "limit": limit}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [])

        if result:
            headers = ['ts', 'ratio']
            self._save_to_csv('elite_ratio', instId, result, headers)

        return result

    def get_premium_history(self, instId: str, limit: int = 1) -> List[List]:
        local = self._load_latest_from_csv('premium', instId, limit=limit, max_age_hours=24)
        if len(local) >= limit:
            logger.info(f"使用本地缓存溢价指数数据")
            return local

        endpoint = "/api/v5/public/premium-history"
        params = {"instId": instId, "limit": limit}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [])

        if result:
            headers = ['ts', 'premium']
            converted = [[item['ts'], item['premium']] for item in result if 'ts' in item and 'premium' in item]
            self._save_to_csv('premium', instId, converted, headers)
            return converted

        return []

    def get_insurance_fund(self, instType: str, instFamily: str,
                           type_filter: str = None, limit: int = 5) -> List[Dict]:
        """GET /api/v5/public/insurance-fund
        获取保险基金数据，用于间接推算爆仓量。
        type_filter 可选: liquidation_balance_deposit(爆仓罚金), bankruptcy_loss(穿仓亏损),
                          platform_revenue, regular_update, adl
        instFamily 格式: "BTC-USD", "AI-USDT" 等
        """
        local = self._load_latest_from_csv('insurance_fund', instFamily.replace('-', '_'), limit=limit, max_age_hours=12)
        headers = ['ts', 'type', 'amt', 'balance', 'instFamily', 'adlType']
        if len(local) >= limit:
            logger.info(f"使用本地缓存保险基金数据")
            result = []
            for row in local:
                result.append(dict(zip(headers[:len(row)], row)))
            return result

        endpoint = "/api/v5/public/insurance-fund"
        params: Dict[str, Any] = {"instType": instType, "instFamily": instFamily, "limit": str(limit)}
        if type_filter:
            params["type"] = type_filter
        data = self._request("GET", endpoint, params)
        raw = data.get("data", [])

        result = []
        save_rows = []
        for item in raw:
            inst_family = item.get('instFamily', instFamily)
            for detail in item.get('details', []):
                row = {
                    'ts': detail.get('ts', ''),
                    'type': detail.get('type', ''),
                    'amt': detail.get('amt', ''),
                    'balance': detail.get('balance', ''),
                    'instFamily': inst_family,
                    'adlType': detail.get('adlType', ''),
                }
                result.append(row)
                save_rows.append([row['ts'], row['type'], row['amt'], row['balance'], row['instFamily'], row['adlType']])

        if save_rows:
            safe_key = instFamily.replace('-', '_')
            self._save_to_csv('insurance_fund', safe_key, save_rows, headers)

        return result

    def get_funding_rate_history(self, instId: str, limit: int = 24) -> List[Dict]:
        """
        获取资金费率历史（多点数据），用于检测"费率收割周期"。
        GET /api/v5/public/funding-rate-history
        返回: [{instId, instType, fundingRate, realizedRate, fundingTime, ts}, ...]
        """
        local = self._load_latest_from_csv('funding_rate_hist', instId, limit=limit, max_age_hours=8)
        headers = ['ts', 'fundingRate', 'realizedRate', 'fundingTime']
        if len(local) >= limit:
            logger.info(f"使用本地缓存资金费率历史数据 ({len(local)} 条)")
            result = []
            for row in local:
                result.append(dict(zip(headers[:len(row)], row)))
            return result

        endpoint = "/api/v5/public/funding-rate-history"
        params = {"instId": instId, "limit": str(limit)}
        data = self._request("GET", endpoint, params)
        if isinstance(data, dict):
            raw = data.get("data", [])
        elif isinstance(data, list):
            raw = data
        else:
            raw = []
        if raw:
            save_rows = []
            for item in raw:
                if isinstance(item, dict):
                    save_rows.append([
                        item.get('ts', ''), item.get('fundingRate', ''),
                        item.get('realizedRate', ''), item.get('fundingTime', '')
                    ])
            self._save_to_csv('funding_rate_hist', instId, save_rows, headers)
        return raw

    def get_index_tickers(self, instId: str) -> Dict[str, Any]:
        """
        获取指数行情（现货基准价），用于计算期货-现货基差。
        GET /api/v5/market/index-tickers
        注意: instId 需要是指数产品 ID，如 "BTC-USDT"（不是 SWAP）。
        返回: {instId, idxPx, high24h, low24h, ts, ...}
        """
        local = self._load_latest_from_csv('index_tickers', instId, limit=1, max_age_hours=1)
        if local:
            headers = ['ts', 'idxPx', 'high24h', 'low24h']
            latest = dict(zip(headers, local[0]))
            logger.info(f"使用本地缓存指数行情数据")
            return latest

        endpoint = "/api/v5/market/index-tickers"
        params = {"instId": instId}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [{}])[0] if data.get("data") else {}
        if result:
            save_data = [{
                'ts': result.get('ts', ''),
                'idxPx': result.get('idxPx', ''),
                'high24h': result.get('high24h', ''),
                'low24h': result.get('low24h', ''),
            }]
            headers = ['ts', 'idxPx', 'high24h', 'low24h']
            safe_id = instId.replace('-', '_').replace('/', '_')
            self._save_to_csv('index_tickers', safe_id, save_data, headers)
        return result

    def get_position_ratio_top_trader(self, instId: str, period: str = "5m", limit: int = 12) -> List[Dict]:
        """
        获取精英交易员持仓比（仓位比，非人数比）—— 真实资金方向。
        GET /api/v5/rubik/stat/contracts/long-short-position-ratio-contract-top-trader
        返回: [{ts, longPosition, shortPosition, longShortPositionRatio}, ...]
        区别于现有的 long-short-account-ratio-contract-top-trader（账户数比），
        此接口反映的是实际持仓仓位占比，代表大资金真实多空倾向。
        """
        local = self._load_latest_from_csv('elite_position_ratio', instId, limit=limit, max_age_hours=24)
        headers = ['ts', 'longPosition', 'shortPosition', 'longShortPositionRatio']
        if len(local) >= limit:
            logger.info(f"使用本地缓存精英持仓比数据 ({len(local)} 条)")
            result = []
            for row in local:
                result.append(dict(zip(headers[:len(row)], row)))
            return result

        endpoint = "/api/v5/rubik/stat/contracts/long-short-position-ratio-contract-top-trader"
        params = {"instId": instId, "period": period, "limit": str(limit)}
        data = self._request("GET", endpoint, params)
        if isinstance(data, dict):
            raw = data.get("data", [])
        elif isinstance(data, list):
            raw = data
        else:
            raw = []
        if raw:
            save_rows = []
            for item in raw:
                if isinstance(item, dict):
                    save_rows.append([
                        item.get('ts', ''), item.get('longPosition', ''),
                        item.get('shortPosition', ''), item.get('longShortPositionRatio', '')
                    ])
            self._save_to_csv('elite_position_ratio', instId, save_rows, headers)
        return raw

    def get_option_oi_strike(self, ccy: str, expTime: str = "") -> List[Dict]:
        """
        获取期权按行权价的持仓量/成交量分布，用于计算 Max Pain。
        GET /api/v5/rubik/stat/option/open-interest-volume-strike
        参数:
          ccy: 币种如 "BTC", "ETH"（期权底层资产，非合约ID）
          expTime: 到期日，如 "20250627"，空则返回最近到期日
        返回: [{strike, callOI, putOI, callVol, putVol}, ...]
        Max Pain 原理: 期权卖方（做市商）会在到期日推动价格到"最大痛点"——
        即 (callOI + putOI) 最小的行权价，使买方权利金归零。
        """
        cache_key = f"{ccy}_{expTime}" if expTime else ccy
        local = self._load_latest_from_csv('option_oi_strike', cache_key, limit=1, max_age_hours=2)
        if local:
            logger.info(f"使用本地缓存期权持仓分布数据")
            return []

        endpoint = "/api/v5/rubik/stat/option/open-interest-volume-strike"
        params = {"ccy": ccy}
        if expTime:
            params["expTime"] = expTime
        data = self._request("GET", endpoint, params)
        raw = data.get("data", [])
        if raw:
            save_data = [{'ts': raw[0].get('ts', datetime.now().isoformat())}]
            headers = ['ts']
            self._save_to_csv('option_oi_strike', cache_key, save_data, headers)
        return raw

    def get_option_oi_ratio(self, ccy: str, period: str = "1H") -> List[Dict]:
        """
        获取期权 Put/Call Ratio（PCR），市场情绪温度计。
        GET /api/v5/rubik/stat/option/open-interest-volume-ratio
        参数:
          ccy: 币种如 "BTC", "ETH"
          period: 周期 "5m"/"1H"/"1D"
        返回: [{ts, openInterestRatio, volumeRatio}, ...]
        PCR > 1 = 看跌情绪浓（put 持仓多于 call），PCR > 1.5 = 极端恐慌/底部
        PCR < 0.7 = 看涨情绪浓（call 持仓多于 put），PCR < 0.5 = 过度乐观/顶部
        """
        cache_key = f"{ccy}_{period}"
        local = self._load_latest_from_csv('option_oi_ratio', cache_key, limit=24, max_age_hours=8)
        headers = ['ts', 'openInterestRatio', 'volumeRatio']
        if len(local) >= 12:
            logger.info(f"使用本地缓存 Put/Call Ratio 数据 ({len(local)} 条)")
            result = []
            for row in local:
                result.append(dict(zip(headers[:len(row)], row)))
            return result

        endpoint = "/api/v5/rubik/stat/option/open-interest-volume-ratio"
        params = {"ccy": ccy, "period": period}
        data = self._request("GET", endpoint, params)
        if isinstance(data, dict):
            raw = data.get("data", [])
        elif isinstance(data, list):
            raw = data
        else:
            raw = []
        if raw:
            save_rows = []
            for item in raw:
                if isinstance(item, dict):
                    save_rows.append([
                        item.get('ts', ''), item.get('openInterestRatio', ''),
                        item.get('volumeRatio', '')
                    ])
            self._save_to_csv('option_oi_ratio', cache_key, save_rows, headers)
        return raw

    def get_elite_position_trend(self, instId: str) -> Dict[str, List[Dict]]:
        """
        多周期精英持仓比趋势数据，用于 AI 精英多空趋向指标（维度25）。
        返回: {
            '5m': [{ts, longPosition, shortPosition, longShortPositionRatio}, ...] x 48条,
            '1H': [...] x 24条,
            '1D': [...] x 7条,
        }
        分析精英仓位比在不同时间周期上的趋势方向，判断大资金是在持续建仓
        还是悄悄转向，而不仅是看单一时刻的值。
        NOTE: 若某周期 API 不支持则跳过该周期，不影响其他周期。
        """
        result = {}
        periods = [('5m', 48), ('1H', 24), ('1D', 7)]
        for period, limit in periods:
            try:
                result[period] = self.get_position_ratio_top_trader(instId, period=period, limit=limit)
            except Exception as e:
                logger.warning(f"精英持仓趋势 {period} 拉取失败 (将跳过此周期): {e}")
        return result

    def get_position_tiers(self, instType: str = "SWAP", instFamily: str = "", tdMode: str = "cross") -> List[Dict]:
        """
        获取持仓档位信息，用于分析爆仓连锁反应风险。
        GET /api/v5/public/position-tiers
        参数:
          instType: "SWAP" / "FUTURES" / "OPTION"
          instFamily: 如 "BTC-USDT", "ETH-USDT"
          tdMode: 交易模式 "cross"(全仓) / "isolated"(逐仓)，必填参数
        返回: [{uly, instFamily, tier, minSz, maxSz, mmr, imr, maxLever, optMgnFactor}, ...]
        """
        local = self._load_latest_from_csv('position_tiers', instFamily, limit=1, max_age_hours=12)
        if local:
            logger.info(f"使用本地缓存持仓档位数据")
            return []

        endpoint = "/api/v5/public/position-tiers"
        params = {"instType": instType, "tdMode": tdMode}
        if instFamily:
            params["instFamily"] = instFamily
        data = self._request("GET", endpoint, params)
        if isinstance(data, dict):
            raw = data.get("data", [])
        elif isinstance(data, list):
            raw = data
        else:
            raw = []
        if raw:
            ts_str = raw[0].get('ts', str(int(time.time() * 1000))) if isinstance(raw[0], dict) else str(int(time.time() * 1000))
            save_data = [{'ts': ts_str}]
            headers = ['ts']
            self._save_to_csv('position_tiers', instFamily, save_data, headers)
        return raw

    def get_mark_price_candles(self, instId: str, bar: str, limit: int = 100) -> List[List]:
        endpoint = "/api/v5/market/mark-price-candles"
        params = {"instId": instId, "bar": bar, "limit": limit}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [])

        if result:
            headers = ['open_time', 'open', 'high', 'low', 'close', 'confirm']
            self._save_to_csv('mark_candles', instId, result, headers)

        return result

    def get_index_candles(self, instId: str, bar: str, limit: int = 100) -> List[List]:
        endpoint = "/api/v5/market/index-candles"
        params = {"instId": instId, "bar": bar, "limit": limit}
        data = self._request("GET", endpoint, params)
        result = data.get("data", [])

        if result:
            headers = ['open_time', 'open', 'high', 'low', 'close', 'confirm']
            self._save_to_csv('index_candles', instId, result, headers)

        return result

    def download_history_data(self, module: str, instType: str, instIdList: str,
                              dateAggrType: str, begin: str, end: str) -> List[Dict]:
        endpoint = "/api/v5/public/market-data-history"
        params = {
            "module": module,
            "instType": instType,
            "instIdList": instIdList,
            "dateAggrType": dateAggrType,
            "begin": begin,
            "end": end
        }
        data = self._request("GET", endpoint, params)
        return data.get("data", [])