"""
庄家行为检测系统 - 全局配置模块
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class OKXConfig:
    """OKX API 连接配置（实盘）"""
    # WebSocket 地址
    #   /ws/v5/public : tickers, books, trades(聚合)
    #   /ws/v5/business : trades-all(逐笔), candle1m/5m(K线)
    #   /ws/v5/private : 账户/持仓/订单(需 API Key 登录)
    ws_public_url: str = "wss://ws.okx.com:8443/ws/v5/public"
    ws_business_url: str = "wss://ws.okx.com:8443/ws/v5/business"
    ws_private_url: str = "wss://ws.okx.com:8443/ws/v5/private"

    # REST API
    rest_url: str = "https://openapi.okx.com"

    # API Key（仅私有频道/私有 REST 需要，公有行情不需要）
    api_key: str = ""
    secret_key: str = ""
    passphrase: str = ""


@dataclass
class SymbolConfig:
    """单个币种监控配置"""
    inst_id: str = "BTC-USDT-SWAP"     # 永续: BTC-USDT-SWAP / 现货: BTC-USDT
    inst_type: str = "SWAP"            # 产品类型: SPOT / SWAP / FUTURES

    # 位置判断
    lookback_days: int = 30
    high_position_percentile: float = 0.80
    low_position_percentile: float = 0.20

    # 成交量异常
    volume_ma_periods: int = 20
    di_liang_ratio: float = 0.5
    bei_liang_ratio: float = 2.0
    tian_liang_ratio: float = 4.0
    duidao_amplitude_max: float = 0.003

    # 夹板战术
    sandwich_ask_bid_ratio: float = 5.0
    sandwich_spread_ticks: int = 2
    sandwich_duration_sec: float = 30.0

    # 拖拉机单
    tractor_window_sec: float = 15.0
    tractor_min_orders: int = 20
    tractor_max_amount_usd: float = 100.0
    tractor_price_rise_max: float = 0.001

    # 插针
    wick_shadow_ratio: float = 2.5
    wick_support_break_pct: float = 0.01

    # 放量滞涨
    stagnation_body_max_ratio: float = 0.3
    stagnation_shadow_min_ratio: float = 0.5
    stagnation_range_pct: float = 0.01

    # 大单砸盘
    whale_multiplier: float = 3.0
    whale_consecutive: int = 3


@dataclass
class DetectorConfig:
    """检测器全局配置"""
    depth_cache_size: int = 500
    trade_cache_size: int = 5000
    kline_cache_size: int = 2000
    detection_interval: float = 1.0
    signal_cooldown_sec: float = 60.0


@dataclass
class SystemConfig:
    """系统总配置"""
    okx: OKXConfig = field(default_factory=OKXConfig)
    symbols: List[SymbolConfig] = field(default_factory=lambda: [
        SymbolConfig(inst_id="BTC-USDT-SWAP", inst_type="SWAP"),
    ])
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    log_level: str = "INFO"


default_config = SystemConfig()
