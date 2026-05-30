"""
数据采集层 - OKX WebSocket + REST API 封装
负责：深度快照、逐笔成交、K线数据的实时推送与缓存
"""
import json
import time
import asyncio
import logging
import threading
from collections import deque
from typing import Optional, Callable, Dict, List
from dataclasses import dataclass, field

import websocket
import requests

from detector_config import SymbolConfig, OKXConfig, DetectorConfig

logger = logging.getLogger(__name__)


# ==================== 数据模型 ====================

@dataclass
class TradeData:
    """逐笔成交"""
    inst_id: str
    trade_id: str
    price: float
    size: float           # 数量
    side: str             # buy / sell
    ts: float             # 毫秒时间戳
    source: str = "0"     # 0=普通, 1=ELP


@dataclass
class DepthData:
    """深度快照"""
    inst_id: str
    asks: List[List[float]]   # [[price, qty, _, order_count], ...]
    bids: List[List[float]]
    ts: float
    seq_id: int = 0


@dataclass
class CandleData:
    """K线"""
    inst_id: str
    ts: float      # 毫秒
    open: float
    high: float
    low: float
    close: float
    vol: float         # 张数
    vol_ccy: float     # 币数
    vol_ccy_quote: float  # 计价货币量
    confirm: int = 0   # 0=未完, 1=已完
    bar: str = "1m"


@dataclass
class TickerData:
    """行情快照"""
    inst_id: str
    last: float
    ask_px: float
    ask_sz: float
    bid_px: float
    bid_sz: float
    open_24h: float
    high_24h: float
    low_24h: float
    vol_24h: float
    vol_ccy_24h: float
    ts: float


# ==================== 滚动缓存 ====================

class RollingCache:
    """线程安全的滚动缓存"""

    def __init__(self, maxlen: int):
        self._deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, item):
        with self._lock:
            self._deque.append(item)

    def get_all(self) -> list:
        with self._lock:
            return list(self._deque)

    def get_recent(self, n: int) -> list:
        with self._lock:
            items = list(self._deque)
            return items[-n:] if len(items) >= n else items

    def get_last(self):
        with self._lock:
            return self._deque[-1] if self._deque else None

    def clear(self):
        with self._lock:
            self._deque.clear()

    def __len__(self):
        with self._lock:
            return len(self._deque)


# ==================== OKX REST 客户端 ====================

class OKXRestClient:
    """OKX REST API 客户端（用于拉取历史数据）"""

    def __init__(self, okx_config: OKXConfig):
        self.base_url = okx_config.rest_url

    def _headers(self) -> dict:
        return {"Content-Type": "application/json"}

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        resp = requests.get(url, params=params, headers=self._headers(), timeout=10)
        return resp.json()

    def get_candles(self, inst_id: str, bar: str = "1m",
                    limit: int = 300, after: str = None, before: str = None) -> list:
        """获取历史K线数据
        返回格式: [[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm], ...]
        """
        params = {"instId": inst_id, "bar": bar, "limit": str(limit)}
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        result = self._get("/api/v5/market/candles", params)
        if result.get("code") == "0":
            return result["data"]
        logger.warning(f"获取K线失败: {result}")
        return []

    def get_history_candles(self, inst_id: str, bar: str = "1D",
                            limit: int = 300, after: str = None,
                            before: str = None) -> list:
        """获取历史K线（更长周期）"""
        params = {"instId": inst_id, "bar": bar, "limit": str(limit)}
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        result = self._get("/api/v5/market/history-candles", params)
        if result.get("code") == "0":
            return result["data"]
        logger.warning(f"获取历史K线失败: {result}")
        return []

    def get_orderbook(self, inst_id: str, sz: int = 400) -> dict:
        """获取深度快照"""
        params = {"instId": inst_id, "sz": str(sz)}
        result = self._get("/api/v5/market/books", params)
        if result.get("code") == "0":
            return result["data"][0]
        logger.warning(f"获取深度失败: {result}")
        return {}

    def get_trades(self, inst_id: str, limit: int = 100) -> list:
        """获取最近成交"""
        params = {"instId": inst_id, "limit": str(limit)}
        result = self._get("/api/v5/market/trades", params)
        if result.get("code") == "0":
            return result["data"]
        return []

    def get_ticker(self, inst_id: str) -> dict:
        """获取单个产品行情"""
        params = {"instId": inst_id}
        result = self._get("/api/v5/market/ticker", params)
        if result.get("code") == "0":
            return result["data"][0]
        return {}


# ==================== OKX WebSocket 客户端 ====================

class OKXWebSocket:
    """OKX WebSocket 封装 - 支持自动重连、心跳、多频道订阅"""

    def __init__(self, url: str):
        self.url = url
        self.ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._callbacks: Dict[str, list] = {}   # channel -> [callbacks]
        self._ping_timer: Optional[threading.Timer] = None
        self._last_msg_time = time.time()
        # 指数退避重连
        self._reconnect_delay = 5    # 初始5秒
        self._max_reconnect_delay = 60  # 最大60秒

    def on_message_callback(self, channel: str):
        """注册频道回调的装饰器"""
        def decorator(func: Callable):
            if channel not in self._callbacks:
                self._callbacks[channel] = []
            self._callbacks[channel].append(func)
            return func
        return decorator

    def _on_message(self, ws, message: str):
        self._last_msg_time = time.time()
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            if message == "pong":
                logger.debug("收到 pong")
                return
            logger.warning(f"无法解析消息: {message[:100]}")
            return

        # 处理订阅确认
        event = data.get("event")
        if event:
            if event == "subscribe":
                ch = data.get("arg", {}).get("channel", "")
                logger.info(f"订阅成功: {ch}")
            elif event == "error":
                logger.error(f"WebSocket 错误: {data}")
            elif event == "notice":
                logger.warning(f"WebSocket 通知: {data}")
            return

        # 分发数据到对应频道回调
        arg = data.get("arg", {})
        channel = arg.get("channel", "")
        if channel in self._callbacks:
            for cb in self._callbacks[channel]:
                try:
                    cb(data)
                except Exception as e:
                    logger.error(f"回调异常 [{channel}]: {e}", exc_info=True)

    def _on_error(self, ws, error):
        logger.error(f"WebSocket 错误: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"WebSocket 断开: {close_status_code} {close_msg}")
        self._cancel_ping()

    def _on_open(self, ws):
        logger.info(f"WebSocket 连接成功: {self.url}")
        self._last_msg_time = time.time()
        self._start_ping()

    def _start_ping(self):
        """启动心跳"""
        self._cancel_ping()

        def _ping():
            if self.ws and self.ws.sock and self.ws.sock.connected:
                try:
                    self.ws.send("ping")
                except Exception:
                    pass
            # 检查超时
            if time.time() - self._last_msg_time > 60:
                logger.warning("60秒无消息，主动断开重连")
                try:
                    self.ws.close()
                except Exception:
                    pass
            else:
                self._ping_timer = threading.Timer(25, _ping)
                self._ping_timer.daemon = True
                self._ping_timer.start()

        self._ping_timer = threading.Timer(25, _ping)
        self._ping_timer.daemon = True
        self._ping_timer.start()

    def _cancel_ping(self):
        if self._ping_timer:
            self._ping_timer.cancel()
            self._ping_timer = None

    def subscribe(self, channels: list):
        """订阅频道
        channels: [{"channel": "tickers", "instId": "BTC-USDT"}, ...]
        """
        if self.ws and self.ws.sock and self.ws.sock.connected:
            msg = json.dumps({"op": "subscribe", "args": channels})
            self.ws.send(msg)
            logger.info(f"发送订阅: {channels}")
        else:
            logger.warning("WebSocket 未连接，无法订阅")

    def unsubscribe(self, channels: list):
        """取消订阅"""
        if self.ws and self.ws.sock and self.ws.sock.connected:
            msg = json.dumps({"op": "unsubscribe", "args": channels})
            self.ws.send(msg)

    def connect(self):
        """建立连接（阻塞式，在独立线程中运行）"""
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.ws.run_forever(ping_interval=0, ping_timeout=None, reconnect=0)

    def start(self):
        """在新线程中启动 WebSocket"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._connect_loop, daemon=True)
        self._thread.start()

    def _connect_loop(self):
        """带指数退避的重连循环"""
        while self._running:
            try:
                self.connect()
                # 连接成功退出 run_forever（如被 stop 关闭），重置退避
                self._reconnect_delay = 5
            except Exception as e:
                logger.error(f"WebSocket 异常: {e}")
            if self._running:
                logger.info(f"{self._reconnect_delay}秒后重连...")
                time.sleep(self._reconnect_delay)
                # 指数退避: 5 → 10 → 20 → 40 → 60(max)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    def stop(self):
        """停止 WebSocket"""
        self._running = False
        self._cancel_ping()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass


# ==================== 数据采集管理器 ====================

class DataCollector:
    """
    数据采集管理器
    管理一个交易对的所有数据流：Ticker、K线、深度、逐笔成交
    """

    def __init__(self, symbol: SymbolConfig, okx_config: OKXConfig,
                 detector_cfg: DetectorConfig):
        self.symbol = symbol
        self.okx_config = okx_config
        self.detector_cfg = detector_cfg
        self.inst_id = symbol.inst_id

        # REST 客户端
        self.rest = OKXRestClient(okx_config)

        # WebSocket 客户端（按 OKX 规范分配频道）
        #   public   → tickers, books, trades(聚合)
        #   business → trades-all(逐笔), candle1m/5m(K线)
        self.ws_public = OKXWebSocket(okx_config.ws_public_url)
        self.ws_business = OKXWebSocket(okx_config.ws_business_url)

        # ====== 滚动缓存 ======
        self.trade_cache = RollingCache(detector_cfg.trade_cache_size)
        self.depth_cache = RollingCache(detector_cfg.depth_cache_size)
        self.candle_1m_cache = RollingCache(detector_cfg.kline_cache_size)
        self.candle_5m_cache = RollingCache(detector_cfg.kline_cache_size)

        # 最新 Ticker
        self._latest_ticker: Optional[TickerData] = None
        self._latest_ticker_lock = threading.Lock()

        # 最新深度
        self._latest_depth: Optional[DepthData] = None
        self._latest_depth_lock = threading.Lock()

        # ====== 注册回调 ======
        self._register_callbacks()

        # 初始化完成标志
        self._initialized = threading.Event()

    def _register_callbacks(self):
        """注册各类 WebSocket 数据回调"""

        # ---- Ticker ----
        @self.ws_public.on_message_callback("tickers")
        def on_ticker(data: dict):
            try:
                d = data["data"][0]
                ticker = TickerData(
                    inst_id=d["instId"],
                    last=float(d["last"]),
                    ask_px=float(d["askPx"]),
                    ask_sz=float(d["askSz"]),
                    bid_px=float(d["bidPx"]),
                    bid_sz=float(d["bidSz"]),
                    open_24h=float(d["open24h"]),
                    high_24h=float(d["high24h"]),
                    low_24h=float(d["low24h"]),
                    vol_24h=float(d["vol24h"]),
                    vol_ccy_24h=float(d["volCcy24h"]),
                    ts=float(d["ts"]),
                )
                with self._latest_ticker_lock:
                    self._latest_ticker = ticker
            except Exception as e:
                logger.error(f"Ticker 解析异常: {e}")

        # ---- 深度 (books = 400档增量) ----
        @self.ws_public.on_message_callback("books")
        def on_depth(data: dict):
            try:
                action = data.get("action", "update")
                d = data["data"][0]
                asks_raw = d.get("asks", [])
                bids_raw = d.get("bids", [])
                asks = [[float(x[0]), float(x[1]), float(x[2]), int(x[3])] for x in asks_raw]
                bids = [[float(x[0]), float(x[1]), float(x[2]), int(x[3])] for x in bids_raw]
                depth = DepthData(
                    inst_id=self.inst_id,
                    asks=asks,
                    bids=bids,
                    ts=float(d["ts"]),
                    seq_id=d.get("seqId", 0),
                )
                with self._latest_depth_lock:
                    if action == "snapshot":
                        # 全量快照：重建本地深度簿
                        self._depth_book = {"asks": {}, "bids": {}}
                        for a in asks:
                            self._depth_book["asks"][a[0]] = a
                        for b in bids:
                            self._depth_book["bids"][b[0]] = b
                    else:
                        # 增量更新
                        if not hasattr(self, '_depth_book'):
                            self._depth_book = {"asks": {}, "bids": {}}
                        for a in asks:
                            if a[1] == 0:
                                self._depth_book["asks"].pop(a[0], None)
                            else:
                                self._depth_book["asks"][a[0]] = a
                        for b in bids:
                            if b[1] == 0:
                                self._depth_book["bids"].pop(b[0], None)
                            else:
                                self._depth_book["bids"][b[0]] = b
                    self._latest_depth = depth
                self.depth_cache.append(depth)
            except Exception as e:
                logger.error(f"深度解析异常: {e}")

        # ---- 逐笔成交 (trades-all = 每笔独立推送) ----
        @self.ws_business.on_message_callback("trades-all")
        def on_trade(data: dict):
            try:
                d = data["data"][0]
                trade = TradeData(
                    inst_id=d["instId"],
                    trade_id=d["tradeId"],
                    price=float(d["px"]),
                    size=float(d["sz"]),
                    side=d["side"],
                    ts=float(d["ts"]),
                    source=d.get("source", "0"),
                )
                self.trade_cache.append(trade)
            except Exception as e:
                logger.error(f"逐笔成交解析异常: {e}")

        # ---- 1分钟K线 ----
        @self.ws_business.on_message_callback("candle1m")
        def on_candle_1m(data: dict):
            try:
                arr = data["data"][0]
                candle = CandleData(
                    inst_id=self.inst_id,
                    ts=float(arr[0]),
                    open=float(arr[1]),
                    high=float(arr[2]),
                    low=float(arr[3]),
                    close=float(arr[4]),
                    vol=float(arr[5]),
                    vol_ccy=float(arr[6]),
                    vol_ccy_quote=float(arr[7]),
                    confirm=int(arr[8]),
                    bar="1m",
                )
                self.candle_1m_cache.append(candle)
            except Exception as e:
                logger.error(f"1m K线解析异常: {e}")

        # ---- 5分钟K线 ----
        @self.ws_business.on_message_callback("candle5m")
        def on_candle_5m(data: dict):
            try:
                arr = data["data"][0]
                candle = CandleData(
                    inst_id=self.inst_id,
                    ts=float(arr[0]),
                    open=float(arr[1]),
                    high=float(arr[2]),
                    low=float(arr[3]),
                    close=float(arr[4]),
                    vol=float(arr[5]),
                    vol_ccy=float(arr[6]),
                    vol_ccy_quote=float(arr[7]),
                    confirm=int(arr[8]),
                    bar="5m",
                )
                self.candle_5m_cache.append(candle)
            except Exception as e:
                logger.error(f"5m K线解析异常: {e}")

    # ==================== 公共接口 ====================

    def get_latest_ticker(self) -> Optional[TickerData]:
        with self._latest_ticker_lock:
            return self._latest_ticker

    def get_latest_depth(self) -> Optional[DepthData]:
        with self._latest_depth_lock:
            return self._latest_depth

    def get_depth_book(self) -> dict:
        """返回本地维护的完整深度簿"""
        if not hasattr(self, '_depth_book'):
            return {"asks": {}, "bids": {}}
        return self._depth_book

    def load_historical_data(self):
        """从 REST API 拉取历史K线数据，填入缓存"""
        logger.info(f"[{self.inst_id}] 加载历史K线...")

        # 加载日线（用于高低位判断）
        days = self.symbol.lookback_days
        daily_candles = self.rest.get_history_candles(
            self.inst_id, bar="1D", limit=days + 5
        )
        self._historical_daily = []
        for row in daily_candles:
            self._historical_daily.append({
                "ts": float(row[0]),
                "o": float(row[1]), "h": float(row[2]),
                "l": float(row[3]), "c": float(row[4]),
                "vol": float(row[5]), "vol_ccy": float(row[6]),
            })
        logger.info(f"[{self.inst_id}] 日线加载完成: {len(self._historical_daily)} 条")

        # 加载1分钟K线（用于近期分析）
        m1_candles = self.rest.get_candles(
            self.inst_id, bar="1m", limit=300
        )
        for row in m1_candles:
            candle = CandleData(
                inst_id=self.inst_id,
                ts=float(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                vol=float(row[5]),
                vol_ccy=float(row[6]),
                vol_ccy_quote=float(row[7]),
                confirm=int(row[8]),
                bar="1m",
            )
            self.candle_1m_cache.append(candle)
        logger.info(f"[{self.inst_id}] 1m K线加载完成: {len(m1_candles)} 条")

        # 加载5分钟K线
        m5_candles = self.rest.get_candles(
            self.inst_id, bar="5m", limit=300
        )
        for row in m5_candles:
            candle = CandleData(
                inst_id=self.inst_id,
                ts=float(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                vol=float(row[5]),
                vol_ccy=float(row[6]),
                vol_ccy_quote=float(row[7]),
                confirm=int(row[8]),
                bar="5m",
            )
            self.candle_5m_cache.append(candle)
        logger.info(f"[{self.inst_id}] 5m K线加载完成: {len(m5_candles)} 条")

        # 标记初始化完成
        self._initialized.set()

    def get_historical_daily(self) -> list:
        if not hasattr(self, '_historical_daily'):
            return []
        return self._historical_daily

    def start(self):
        """启动数据采集"""
        # 先加载历史数据
        self.load_historical_data()
        time.sleep(0.5)

        # 启动 Public WebSocket (tickers + books)
        self.ws_public.start()
        time.sleep(2)
        self.ws_public.subscribe([
            {"channel": "tickers", "instId": self.inst_id},
            {"channel": "books", "instId": self.inst_id},
        ])

        # 启动 Business WebSocket (trades-all + candles)
        self.ws_business.start()
        time.sleep(2)
        self.ws_business.subscribe([
            {"channel": "trades-all", "instId": self.inst_id},
            {"channel": "candle1m", "instId": self.inst_id},
            {"channel": "candle5m", "instId": self.inst_id},
        ])

        logger.info(f"[{self.inst_id}] 数据采集已启动 (Public + Business)")

    def stop(self):
        """停止数据采集"""
        self.ws_public.stop()
        self.ws_business.stop()
        logger.info(f"[{self.inst_id}] 数据采集已停止")

    def wait_ready(self, timeout: float = 30.0):
        """等待初始化完成"""
        if self._initialized.wait(timeout):
            return True
        logger.warning(f"[{self.inst_id}] 初始化超时")
        return False
