# order_utils.py
"""
下单数量换算：根据名义金额（USDT）与合约信息计算可下单张数。

供 main.try_auto_trade 与 web_app.api_place_order 共用，避免重复实现 ctVal/lotSz 对齐逻辑。
"""
from __future__ import annotations

from typing import Dict


def calc_contracts_from_amount(amount_usdt: float, price: float, inst: Dict) -> str:
    """
    根据下单名义金额（USDT）、价格和合约信息，计算张数（sz 字符串）。
    - amount_usdt: 本次计划开仓的名义金额（USDT）
    - price: 下单价格（限价单为用户价格，市价单为当前最新价）
    - inst: /public/instruments 返回的合约信息，需至少包含 ctVal 和 lotSz
    """
    if amount_usdt <= 0:
        raise ValueError("amount_usdt 必须大于 0")
    try:
        ct_val = float(inst.get("ctVal") or 0)
    except (TypeError, ValueError):
        ct_val = 0.0
    if ct_val <= 0:
        raise ValueError("合约面值 ctVal 无效")

    try:
        lot_sz = inst.get("lotSz") or "1"
        lot_f = float(lot_sz)
    except (TypeError, ValueError):
        lot_f = 1.0

    if price <= 0:
        raise ValueError("price 必须大于 0")

    per_contract = price * ct_val
    if per_contract <= 0:
        raise ValueError("合约规格异常，无法计算张数")

    contracts_f = amount_usdt / per_contract
    if contracts_f <= 0:
        raise ValueError("金额过小，无法下至少 1 张合约")

    # 按 lotSz 对齐张数，兼容整数 / 小数张
    if lot_f >= 1:
        # 向下取整到 lotSz 的整数倍，至少 1 张
        contracts = max(1, int(contracts_f // lot_f) * int(lot_f))
    else:
        # 允许小数张的情况下，按 lotSz 对齐
        steps = max(1, int(contracts_f / lot_f))
        contracts = steps * lot_f

    return str(contracts if lot_f < 1 else int(contracts))

