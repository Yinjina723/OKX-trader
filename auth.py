"""
OKX API 认证模块 - HMAC-SHA256 签名
"""
import time
import hmac
import hashlib
import base64
from detector_config import OKXConfig


class OKXAuth:
    """OKX V5 API 签名与认证"""

    def __init__(self, config: OKXConfig):
        self.api_key = config.api_key
        self.secret_key = config.secret_key
        self.passphrase = config.passphrase

    @property
    def configured(self) -> bool:
        """是否已配置 API Key"""
        return bool(self.api_key and self.secret_key and self.passphrase)

    # ---- REST 请求头签名 ----
    def sign_request(self, method: str, path: str, body: str = "") -> dict:
        """
        生成 REST 私有请求头（含签名）
        timestamp 格式: ISO 8601, 如 2020-12-08T09:08:57.715Z
        """
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.", time.gmtime()) + \
                    f"{int(time.time() * 1000) % 1000:03d}Z"
        sign_str = timestamp + method.upper() + path
        if body:
            sign_str += body
        sign = base64.b64encode(
            hmac.new(self.secret_key.encode(), sign_str.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "Content-Type": "application/json",
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
        }

    # ---- WebSocket 登录签名 ----
    def ws_login_sign(self) -> dict:
        """
        生成 WebSocket 登录签名
        timestamp: Unix 时间戳，单位秒
        method 固定为 'GET', requestPath 固定为 '/users/self/verify'
        """
        ts = str(int(time.time()))
        sign_str = ts + "GET" + "/users/self/verify"
        sign = base64.b64encode(
            hmac.new(self.secret_key.encode(), sign_str.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "apiKey": self.api_key,
            "passphrase": self.passphrase,
            "timestamp": ts,
            "sign": sign,
        }

    def ws_login_args(self) -> list:
        """生成 WebSocket 登录请求的 args"""
        return [self.ws_login_sign()]


# ---- 独立签名函数（供外部直接调用） ----
def sign_ws_login(api_key: str, secret_key: str, passphrase: str) -> dict:
    ts = str(int(time.time()))
    sign_str = ts + "GET" + "/users/self/verify"
    sign = base64.b64encode(
        hmac.new(secret_key.encode(), sign_str.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "apiKey": api_key,
        "passphrase": passphrase,
        "timestamp": ts,
        "sign": sign,
    }
