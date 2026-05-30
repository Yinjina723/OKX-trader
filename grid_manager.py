# grid_manager.py
"""
网格管理：根据当前价格与信号（含方向、强度、可选 ATR）计算网格上下界与格数，
生成等差/等比网格价格列表，并做成本检查（手续费+滑点）与 tick 对齐。

输出为可追加到「点位+网格」文件的文本（买单/卖单价格列表）。
"""
import logging
import math
from datetime import datetime
from typing import Dict, List

from config import Config

logger = logging.getLogger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """将 OKX 合约 symbol（如 BTC-USDT-SWAP）转为配置键（如 BTCUSDT）。"""
    s = symbol.replace("-SWAP", "").replace("-", "").replace("/", "")
    return s or symbol


class GridManager:
    """
    网格管理器：支持按币种配置、ATR 动态宽度、等比/等差网格与成本检查。
    signal 中需提供 symbol 和可选 atr。
    """

    def __init__(self, config: Config, default_range_percent=0.2, default_grid_count=10):
        self.config = config
        self.default_range_percent = default_range_percent
        self.default_grid_count = default_grid_count

        # 全局默认值（若无币种特定配置则使用）
        self.default_min_price = getattr(config, 'MIN_PRICE', 0.01)
        self.default_tick_size = getattr(config, 'TICK_SIZE', 0.0001)
        self.default_fee_rate = getattr(config, 'FEE_RATE', 0.001)  # 0.1%
        self.default_slippage = getattr(config, 'SLIPPAGE', 0.0005)  # 0.05%
        self.default_grid_type = getattr(config, 'GRID_TYPE', 'geometric')  # 'arithmetic' 或 'geometric'
        self.default_atr_multiplier_center = getattr(config, 'ATR_MULTIPLIER_CENTER', 0.5)
        self.default_atr_multiplier_width = getattr(config, 'ATR_MULTIPLIER_WIDTH', 2.5)
        self.default_max_range_factor = getattr(config, 'MAX_RANGE_FACTOR', 0.3)  # 最大偏离现价 ±30%

        # 币种特定配置（从 config 中读取，对应 config.PER_COIN_GRID_CONFIG）
        self.per_coin_config = getattr(config, 'PER_COIN_GRID_CONFIG', {})

        logger.info(f"GridManager 初始化完成，默认网格类型: {self.default_grid_type}")

    def _get_coin_config(self, symbol: str) -> dict:
        """获取指定币种的配置；支持 symbol 或标准化键（如 BTCUSDT）。"""
        cfg = self.per_coin_config.get(symbol, {})
        if not cfg:
            cfg = self.per_coin_config.get(_normalize_symbol(symbol), {})
        return cfg

    def calculate_grid_params(self, current_price: float, signal: Dict):
        """
        根据信号和当前价格计算网格上下边界和格数
        返回 (lower, upper, grid_count)
        """
        # 从信号中提取 symbol 和 atr（若无则用默认）
        symbol = signal.get("symbol", "DEFAULT")
        atr = signal.get("atr")  # 可选，若提供则启用动态自适应

        # 获取该币种的个性化配置
        coin_cfg = self._get_coin_config(symbol)
        range_percent = coin_cfg.get('range_percent', self.default_range_percent)
        grid_count = coin_cfg.get('grid_count', self.default_grid_count)
        min_price = coin_cfg.get('min_price', self.default_min_price)
        tick_size = coin_cfg.get('tick_size', self.default_tick_size)
        fee_rate = coin_cfg.get('fee_rate', self.default_fee_rate)
        slippage = coin_cfg.get('slippage', self.default_slippage)
        grid_type = coin_cfg.get('grid_type', self.default_grid_type)
        atr_mult_center = coin_cfg.get('atr_multiplier_center', self.default_atr_multiplier_center)
        atr_mult_width = coin_cfg.get('atr_multiplier_width', self.default_atr_multiplier_width)
        max_range_factor = coin_cfg.get('max_range_factor', self.default_max_range_factor)

        # --- 1. 确定网格中心 ---
        direction = signal.get("direction") if signal else None
        strength = signal.get("strength", "medium") if signal else "medium"

        if atr is not None and atr > 0:
            # 动态中心：根据方向偏移 ATR 倍数
            if direction == "long":
                center = current_price + atr * atr_mult_center
            elif direction == "short":
                center = current_price - atr * atr_mult_center
            else:
                center = current_price
        else:
            # 固定比例偏移
            if direction == "long":
                center = current_price * 1.02
            elif direction == "short":
                center = current_price * 0.98
            else:
                center = current_price

        # 中心点不能低于最小价格（但通常不会）
        center = max(center, min_price)

        # --- 2. 确定区间宽度 ---
        if atr is not None and atr > 0:
            # 动态宽度：基于 ATR
            width = atr * atr_mult_width
        else:
            # 固定百分比宽度
            width = center * range_percent

        # 根据信号强度调整宽度
        strength_factor = {'strong': 1.2, 'medium': 1.0, 'weak': 0.8}.get(strength, 1.0)
        width *= strength_factor

        # 保存原始宽度（用于后续校正）
        original_width = width

        # --- 3. 计算初始上下边界 ---
        lower = center - width / 2
        upper = center + width / 2

        # --- 4. 边界有效性校正 ---
        # 4.1 下限不得低于最小价格
        if lower < min_price:
            logger.debug(f"下限 {lower:.6f} 低于最小价格 {min_price}，调整为 {min_price}")
            lower = min_price

        # 4.2 确保上边界 > 下边界（若相等或颠倒，则基于原始宽度重新计算上边界）
        if upper <= lower:
            upper = lower + original_width
            logger.warning(f"上下边界无效，已重新计算上边界为 {upper:.6f}，宽度保持 {original_width:.6f}")

        # 4.3 上限保护：不超过现价 + max_range_factor
        max_upper = current_price * (1 + max_range_factor)
        if upper > max_upper:
            upper = max_upper
            # 重新调整下限，保持宽度（但不能低于最小价格）
            lower = max(upper - original_width, min_price)
            logger.debug(f"上限超过保护线，调整为 {upper:.6f}，下限同步调整为 {lower:.6f}")

        # 4.4 下限保护：不低于现价 - max_range_factor（可选，防止过低）
        min_lower = current_price * (1 - max_range_factor)
        if lower < min_lower:
            lower = min_lower
            upper = lower + original_width
            logger.debug(f"下限低于保护线，调整为 {lower:.6f}，上限同步调整为 {upper:.6f}")

        # 对齐边界到 tick_size
        lower = self._align_price(lower, tick_size)
        upper = self._align_price(upper, tick_size)

        # 确保上下边界仍有效
        if upper <= lower:
            upper = lower + tick_size  # 至少一个 tick 的宽度
            logger.warning(f"对齐后边界仍无效，强制设为至少一个 tick: lower={lower}, upper={upper}")

        # --- 5. 成本检查与格数调整 ---
        min_profit_ratio = fee_rate * 2 + slippage  # 双向手续费 + 滑点
        if grid_type == 'arithmetic':
            # 等差网格：检查步长是否足够
            step = (upper - lower) / grid_count
            if step / current_price < min_profit_ratio:
                # 重新计算最大可行格数
                max_grids_by_cost = int((upper - lower) / (current_price * min_profit_ratio))
                new_grid_count = max(1, max_grids_by_cost)
                if new_grid_count < grid_count:
                    logger.info(f"等差步长过小，格数由 {grid_count} 调整为 {new_grid_count}")
                    grid_count = new_grid_count
        else:  # geometric
            # 等比网格：检查相邻比例差是否足够
            if lower > 0:
                ratio = (upper / lower) ** (1.0 / grid_count)
                if ratio - 1 < min_profit_ratio:
                    # 重新计算最大可行格数
                    max_grids_by_cost = int(math.log(upper / lower) / math.log(1 + min_profit_ratio))
                    new_grid_count = max(1, max_grids_by_cost)
                    if new_grid_count < grid_count:
                        logger.info(f"等比步长过小，格数由 {grid_count} 调整为 {new_grid_count}")
                        grid_count = new_grid_count

        logger.info(f"计算网格参数: center={center:.4f}, width={original_width:.4f}, "
                    f"lower={lower:.4f}, upper={upper:.4f}, grid_count={grid_count}, type={grid_type}")
        return lower, upper, grid_count

    def get_grid_suggestions(self, current_price: float, signal: Dict) -> str:
        """
        生成简化的网格建议，只包含价格列表，无保证金信息
        """
        lower, upper, grid_count = self.calculate_grid_params(current_price, signal)

        # 根据配置的网格类型生成价格列表
        symbol = signal.get("symbol", "DEFAULT")
        coin_cfg = self._get_coin_config(symbol)
        grid_type = coin_cfg.get('grid_type', self.default_grid_type)

        if grid_type == 'arithmetic':
            grid_prices = self._generate_arithmetic_prices(lower, upper, grid_count, coin_cfg)
        else:
            grid_prices = self._generate_geometric_prices(lower, upper, grid_count, coin_cfg)

        buy_prices = grid_prices[:-1]
        sell_prices = grid_prices[1:]

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        direction_cn = "做多" if signal.get("direction") == "long" else "做空" if signal.get(
            "direction") == "short" else "中性"

        lines = [
            f"{timestamp} | 网格详细建议 (方向 {direction_cn}, 类型 {grid_type}):",
            f"买单 (共{len(buy_prices)}格):"
        ]
        for price in buy_prices:
            lines.append(f"    价格:{price:.8f}")  # 使用更多小数位显示低价币
        lines.append(f"卖单 (共{len(sell_prices)}格):")
        for price in sell_prices:
            lines.append(f"    价格:{price:.8f}")
        lines.append("")
        return "\n".join(lines)

    def _generate_arithmetic_prices(self, lower: float, upper: float, count: int, coin_cfg: dict) -> List[float]:
        """生成等差网格价格，并对齐到 tick_size"""
        tick_size = coin_cfg.get('tick_size', self.default_tick_size)
        step = (upper - lower) / count
        # 为了对齐更好，可以先对齐 lower 和 upper，然后计算步长使其能被 tick_size 整除
        lower_aligned = self._align_price(lower, tick_size)
        upper_aligned = self._align_price(upper, tick_size)
        total_ticks = int(round((upper_aligned - lower_aligned) / tick_size))
        if total_ticks < count:
            count = total_ticks  # 格数不能多于可对齐的步数
        if count <= 0:
            return [lower_aligned, upper_aligned]
        step_ticks = total_ticks // count
        prices = [lower_aligned + i * step_ticks * tick_size for i in range(count + 1)]
        # 确保最后一个价格接近 upper_aligned
        if abs(prices[-1] - upper_aligned) > tick_size / 2:
            prices[-1] = upper_aligned
        return prices

    def _generate_geometric_prices(self, lower: float, upper: float, count: int, coin_cfg: dict) -> List[float]:
        """生成等比网格价格，并对齐到 tick_size，去重"""
        tick_size = coin_cfg.get('tick_size', self.default_tick_size)
        if lower <= 0 or upper <= 0:
            logger.error("等比网格要求上下边界为正数，回退到等差")
            return self._generate_arithmetic_prices(lower, upper, count, coin_cfg)

        ratio = (upper / lower) ** (1.0 / count)
        prices = [lower * (ratio ** i) for i in range(count + 1)]
        # 对齐并去重
        aligned = []
        for p in prices:
            ap = self._align_price(p, tick_size)
            if not aligned or abs(ap - aligned[-1]) > tick_size / 2:
                aligned.append(ap)
        return aligned

    def _align_price(self, price: float, tick_size: float) -> float:
        """将价格对齐到最小变动单位的整数倍"""
        if tick_size <= 0:
            return price
        return round(price / tick_size) * tick_size