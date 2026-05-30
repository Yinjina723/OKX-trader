# trade_client.py
"""
交易下单封装：设置杠杆、下单（市价/限价），不直接放在 okx_client 中以便职责分离。

依赖 OKXClient 的 _request 做签名与请求；posSide 由调用方根据账户 posMode 决定是否传入。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from okx_client import OKXClient


class TradeClient:
    """交易客户端：杠杆设置、下单。内部复用 OKXClient 的请求与签名。"""

    def __init__(self, okx_client: OKXClient):
        self._client = okx_client

    def set_leverage(self, instId: str, lever: str, mgnMode: str = "cross") -> Dict[str, Any]:
        """设置杠杆倍数。mgnMode: cross=全仓, isolated=逐仓。"""
        endpoint = "/api/v5/account/set-leverage"
        body = {"instId": instId, "lever": str(lever), "mgnMode": mgnMode}
        return self._client._request("POST", endpoint, body=body)

    def place_order(
        self,
        instId: str,
        side: str,
        ordType: str,
        sz: str,
        tdMode: str = "cross",
        px: Optional[str] = None,
        posSide: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        下单。适用于 SWAP 永续等。
        side: buy / sell
        ordType: market / limit
        sz: 张数（合约数），字符串
        tdMode: cross / isolated
        px: 限价单时必填
        posSide: 账户为 long_short_mode 时必传 long/short，net_mode 时不传。
        """
        body = {
            "instId": instId,
            "tdMode": tdMode,
            "side": side,
            "ordType": ordType,
            "sz": str(sz),
        }
        if posSide:
            body["posSide"] = posSide
        if ordType == "limit" and px is not None:
            body["px"] = str(px)
        return self._client._request("POST", "/api/v5/trade/order", body=body)
