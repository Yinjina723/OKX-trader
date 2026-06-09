# config.py
"""精简配置加载器 —— 日线分析专用"""

import json
import os
from typing import List


class Config:
    """从 config.json 加载配置，提供类型安全的属性访问。"""

    def __init__(self, path: str = "config.json"):
        self._path = path
        self._data: dict = {}
        self.reload()

    def reload(self):
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)

    # ── 交易对 ──
    @property
    def SYMBOLS(self) -> List[str]:
        return self._data.get("SYMBOL", ["LAB-USDT-SWAP"])

    # ── API 密钥 ──
    @property
    def DEEPSEEK_API_KEY(self) -> str:
        return self._data.get("DEEPSEEK_API_KEY", "")

    @property
    def OKX_API_KEY(self) -> str:
        return self._data.get("OKX_API_KEY", "")

    @property
    def OKX_SECRET_KEY(self) -> str:
        return self._data.get("OKX_SECRET_KEY", "")

    @property
    def OKX_PASSPHRASE(self) -> str:
        return self._data.get("OKX_PASSPHRASE", "")

    @property
    def SIMULATED(self) -> str:
        return self._data.get("SIMULATED", "0")

    # ── 路径 ──
    @property
    def OUTPUT_DIR(self) -> str:
        return self._data.get("OUTPUT_DIR", "./output")

    @property
    def HISTORY_DIR(self) -> str:
        return self._data.get("HISTORY_DIR", "./data/history")

    # ── 日线参数 ──
    @property
    def DAILY_LOOKBACK(self) -> int:
        return self._data.get("DAILY_LOOKBACK", 150)

    @property
    def ADVANCED_INDICATORS(self) -> bool:
        return self._data.get("ADVANCED_INDICATORS", True)

    @property
    def WICK_SHADOW_RATIO(self) -> float:
        return self._data.get("WICK_SHADOW_RATIO", 3.0)

    # ── AI 参数 ──
    @property
    def AI_TEMPERATURE(self) -> float:
        return self._data.get("AI_TEMPERATURE", 0.3)

    @property
    def AI_MAX_TOKENS(self) -> int:
        return self._data.get("AI_MAX_TOKENS", 1500)

    @property
    def TAKE_PROFIT_RR(self) -> float:
        """止盈盈亏比，默认 1.0（1:1）"""
        return self._data.get("TAKE_PROFIT_RR", 1.0)

    @property
    def HIGH_FREQ_MODE(self) -> bool:
        """启用1H高频机械信号模式"""
        return self._data.get("HIGH_FREQ_MODE", False)

    @property
    def TP_PCT(self) -> float:
        """固定止盈百分比，默认 0.8%"""
        return self._data.get("TP_PCT", 0.008)

    @property
    def SL_PCT(self) -> float:
        """固定止损百分比，默认 0.5%"""
        return self._data.get("SL_PCT", 0.005)

    @property
    def MAX_SIGNALS_PER_DAY(self) -> int:
        """每天最多生成多少个机械信号"""
        return self._data.get("MAX_SIGNALS_PER_DAY", 10)

    @property
    def ENABLE_TRAILING_STOP(self) -> bool:
        """是否启用移动止损"""
        return self._data.get("ENABLE_TRAILING_STOP", True)

    @property
    def TRAIL_ACTIVATION_PCT(self) -> float:
        """移动止损激活阈值（盈利百分比）"""
        return self._data.get("TRAIL_ACTIVATION_PCT", 0.005)

    # ── 猎杀爆仓策略 ──
    @property
    def LIQUIDATION_HUNT_ENABLED(self) -> bool:
        """是否启用猎杀爆仓策略"""
        return self._data.get("LIQUIDATION_HUNT_ENABLED", True)

    @property
    def LS_LONG_EXTREME(self) -> float:
        """多空比高于此值触发猎杀多头"""
        return self._data.get("LS_LONG_EXTREME", 2.0)

    @property
    def LS_SHORT_EXTREME(self) -> float:
        """多空比低于此值触发猎杀空头"""
        return self._data.get("LS_SHORT_EXTREME", 0.7)

    @property
    def HUNT_LEVERAGE_LEVELS(self) -> list:
        """猎杀策略计算的杠杆档位"""
        return self._data.get("HUNT_LEVERAGE_LEVELS", [20, 10, 5])

    # ── 资金卫士 ──
    @property
    def CAPITAL_GUARD_ENABLED(self) -> bool:
        """是否启用资金卫士（阶梯入场+联合爆仓价计算）"""
        return self._data.get("CAPITAL_GUARD_ENABLED", True)

    @property
    def TOTAL_CAPITAL(self) -> float:
        """总资金（美元）"""
        return self._data.get("TOTAL_CAPITAL", 220.0)

    @property
    def TRADING_CAPITAL_RATIO(self) -> float:
        """首段入场资金比例"""
        return self._data.get("TRADING_CAPITAL_RATIO", 0.5)

    @property
    def RESERVE_CAPITAL_RATIO(self) -> float:
        """备用金比例"""
        return self._data.get("RESERVE_CAPITAL_RATIO", 0.5)

    @property
    def DEFAULT_LEVERAGE(self) -> int:
        """默认杠杆倍数"""
        return self._data.get("DEFAULT_LEVERAGE", 1)

    @property
    def UNREACHABLE_THRESHOLD_PCT(self) -> float:
        """安全距离阈值（%），超过此值视为不可触及"""
        return self._data.get("UNREACHABLE_THRESHOLD_PCT", 200.0)

    # ── 网络 ──
    @property
    def REMOTE_SERVER(self) -> str:
        """新加坡服务器地址，本地代理面板使用。"""
        return self._data.get("REMOTE_SERVER", "http://127.0.0.1:8488")

    @property
    def SITE(self) -> str:
        return self._data.get("SITE", "global")
