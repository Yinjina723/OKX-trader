"""
特征与行为检测层 - 7个独立检测器
每个检测器持续扫描数据，触发时打上行为标签。

检测器清单：
1. 位置判断器 (PositionDetector)
2. 成交量异常检测器 (VolumeAnomalyDetector)
3. 夹板战术检测器 (SandwichDetector)
4. 拖拉机单检测器 (TractorOrderDetector)
5. 插针检测器 (WickDetector)
6. 放量滞涨检测器 (StagnationDetector)
7. 大单主动砸盘检测器 (WhaleSellDetector)
"""
import time
import logging
import threading
from collections import deque
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from enum import Enum

from detector_config import SymbolConfig, DetectorConfig
from data_collector import DataCollector, CandleData, TradeData, DepthData, TickerData

logger = logging.getLogger(__name__)


# ==================== 行为标签 ====================

class BehaviorTag(Enum):
    """庄家行为标签"""
    # 位置
    POSITION_LOW = "低位区"
    POSITION_MID = "中位区"
    POSITION_HIGH = "高位区"

    # 成交量
    VOL_DILIANG = "地量"
    VOL_BEILIANG = "倍量"
    VOL_TIANLIANG = "天量"
    VOL_DUIDAO = "疑似对倒"

    # 夹板战术
    SANDWICH_ACCUMULATION = "夹板吸筹"
    SANDWICH_FAKE = "虚假压单"

    # 拖拉机单
    TRACTOR_ACCUMULATION = "隐蔽吸筹(拆单)"

    # 插针
    WICK_DOWN_TEST = "向下试盘"
    WICK_SHAKE_OUT = "洗盘插针"
    WICK_UP_DISTRIBUTION = "向上插针(诱多出货)"

    # 放量滞涨
    STAGNATION_DISTRIBUTION = "高位派发"

    # 大单砸盘
    WHALE_SELL = "大单主动出货"


@dataclass
class DetectionResult:
    """检测结果"""
    tag: BehaviorTag
    symbol: str
    timestamp: float
    confidence: float = 1.0       # 置信度 0~1
    detail: str = ""              # 详细描述
    data: dict = field(default_factory=dict)  # 附加数据

    def to_dict(self) -> dict:
        return {
            "tag": self.tag.value,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "detail": self.detail,
            "data": self.data,
        }


# ==================== 检测器基类 ====================

class BaseDetector:
    """检测器基类"""

    def __init__(self, symbol: SymbolConfig, collector: DataCollector,
                 detector_cfg: DetectorConfig):
        self.symbol = symbol
        self.collector = collector
        self.cfg = detector_cfg
        self.inst_id = symbol.inst_id
        self._last_detect_time = 0
        self._last_signal: Optional[DetectionResult] = None
        self._cooldown = detector_cfg.signal_cooldown_sec

    def _can_detect(self) -> bool:
        """检查是否过了冷却时间"""
        now = time.time()
        if now - self._last_detect_time < self._cooldown:
            return False
        return True

    def _emit(self, result: DetectionResult) -> Optional[DetectionResult]:
        """发出检测结果"""
        now = time.time()
        if now - self._last_detect_time < self._cooldown:
            return None
        self._last_detect_time = now
        self._last_signal = result
        return result

    def detect(self) -> List[DetectionResult]:
        """执行检测，返回检测结果列表"""
        raise NotImplementedError


# ==================== 1. 位置判断器 ====================

class PositionDetector(BaseDetector):
    """
    位置判断器
    取当前价格在近期（如30天）的百分位
    输出：低位区 / 中位区 / 高位区
    """

    def __init__(self, symbol: SymbolConfig, collector: DataCollector,
                 detector_cfg: DetectorConfig):
        super().__init__(symbol, collector, detector_cfg)
        self._current_position: Optional[BehaviorTag] = None

    @property
    def current_position(self) -> Optional[BehaviorTag]:
        return self._current_position

    def detect(self) -> List[DetectionResult]:
        daily = self.collector.get_historical_daily()
        if not daily or len(daily) < 5:
            return []

        # 取近期日线的最高价和最低价区间
        highs = [d["h"] for d in daily]
        lows = [d["l"] for d in daily]
        range_high = max(highs)
        range_low = min(lows)
        price_range = range_high - range_low
        if price_range <= 0:
            return []

        # 当前价格（用最新 Ticker 或 最新日线收盘价）
        ticker = self.collector.get_latest_ticker()
        if ticker:
            current_price = ticker.last
        else:
            current_price = daily[-1]["c"]

        # 计算百分位
        percentile = (current_price - range_low) / price_range

        # 判定位置
        if percentile >= self.symbol.high_position_percentile:
            position = BehaviorTag.POSITION_HIGH
        elif percentile <= self.symbol.low_position_percentile:
            position = BehaviorTag.POSITION_LOW
        else:
            position = BehaviorTag.POSITION_MID

        old_pos = self._current_position
        self._current_position = position

        results = []
        if position != old_pos:
            results.append(DetectionResult(
                tag=position,
                symbol=self.inst_id,
                timestamp=time.time(),
                confidence=0.9,
                detail=f"当前价格 {current_price:.2f} 处于 {range_low:.2f}-{range_high:.2f}"
                       f" 的 {percentile:.1%} 分位 → {position.value}",
                data={"price": current_price, "percentile": percentile,
                      "range_low": range_low, "range_high": range_high}
            ))
        return results


# ==================== 2. 成交量异常检测器 ====================

class VolumeAnomalyDetector(BaseDetector):
    """
    成交量异常检测器
    - 地量: 当前量 < 20均量 * 0.5
    - 倍量: 当前量 > 20均量 * 2
    - 天量: 当前量 > 20均量 * 4
    - 对倒: 倍量/天量 + 价格振幅极小(<0.3%)
    """

    def detect(self) -> List[DetectionResult]:
        candles_5m = self.collector.candle_5m_cache.get_all()
        if len(candles_5m) < self.symbol.volume_ma_periods + 1:
            return []

        # 计算近N根K线的均量
        recent = candles_5m[-(self.symbol.volume_ma_periods + 1):]
        current = recent[-1]
        ma_candles = recent[:-1]
        avg_vol_ccy = sum(c.vol_ccy for c in ma_candles) / len(ma_candles)
        if avg_vol_ccy <= 0:
            return []

        results = []
        vol_ratio = current.vol_ccy / avg_vol_ccy

        # 地量
        if vol_ratio < self.symbol.di_liang_ratio:
            results.append(DetectionResult(
                tag=BehaviorTag.VOL_DILIANG,
                symbol=self.inst_id,
                timestamp=time.time(),
                confidence=0.85,
                detail=f"地量: 当前量 {current.vol_ccy:.2f} < 均量 {avg_vol_ccy:.2f} * "
                       f"{self.symbol.di_liang_ratio} (比值 {vol_ratio:.2f})",
                data={"vol_ccy": current.vol_ccy, "avg_vol_ccy": avg_vol_ccy,
                      "ratio": vol_ratio}
            ))

        # 倍量 / 天量 + 对倒检测
        if vol_ratio >= self.symbol.bei_liang_ratio:
            tag = (BehaviorTag.VOL_TIANLIANG if vol_ratio >= self.symbol.tian_liang_ratio
                   else BehaviorTag.VOL_BEILIANG)

            # 振幅计算
            amplitude = (current.high - current.low) / current.open if current.open > 0 else 0

            detail = (f"{tag.value}: 当前量 {current.vol_ccy:.2f} > 均量 {avg_vol_ccy:.2f} * "
                      f"{self.symbol.bei_liang_ratio} (比值 {vol_ratio:.2f})")

            results.append(DetectionResult(
                tag=tag,
                symbol=self.inst_id,
                timestamp=time.time(),
                confidence=0.8,
                detail=detail,
                data={"vol_ccy": current.vol_ccy, "avg_vol_ccy": avg_vol_ccy,
                      "ratio": vol_ratio, "amplitude": amplitude}
            ))

            # 对倒判断：量很大但振幅极小
            if amplitude < self.symbol.duidao_amplitude_max:
                results.append(DetectionResult(
                    tag=BehaviorTag.VOL_DUIDAO,
                    symbol=self.inst_id,
                    timestamp=time.time(),
                    confidence=0.7,
                    detail=f"疑似对倒: 放量{vol_ratio:.1f}倍但振幅仅{amplitude:.3%}",
                    data={"vol_ratio": vol_ratio, "amplitude": amplitude}
                ))

        return results


# ==================== 3. 夹板战术检测器 ====================

class SandwichDetector(BaseDetector):
    """
    夹板战术检测器
    卖一挂单 >> 买一挂单，且价差极小，持续一定时间。
    - 如果不断有小买单吃掉卖一但卖一快速补回 → 夹板吸筹
    - 如果压单长时间不成交突然撤单 → 虚假压单
    """

    def __init__(self, symbol: SymbolConfig, collector: DataCollector,
                 detector_cfg: DetectorConfig):
        super().__init__(symbol, collector, detector_cfg)
        # 记录夹板状态持续时间
        self._sandwich_start_ts: Optional[float] = None
        self._sandwich_active = False
        # 记录卖一被吃的情况
        self._eat_count = 0  # 被吃次数

    def detect(self) -> List[DetectionResult]:
        ticker = self.collector.get_latest_ticker()
        depth = self.collector.get_latest_depth()
        if not ticker or not depth:
            return []

        ask_px = ticker.ask_px
        ask_sz = ticker.ask_sz
        bid_px = ticker.bid_px
        bid_sz = ticker.bid_sz

        if ask_px <= 0 or bid_px <= 0 or ask_sz <= 0 or bid_sz <= 0:
            return []

        # 价差（tick 数）
        spread = ask_px - bid_px
        ticker_info = self.collector.rest.get_ticker(self.inst_id)
        # tick size 从深度簿中推断，或使用常见值
        tick_sz = 0.1  # 默认，BTC-USDT 是 0.1
        spread_ticks = spread / tick_sz if tick_sz > 0 else spread

        # 判断是否处于夹板状态
        is_sandwich = (
            ask_sz / bid_sz >= self.symbol.sandwich_ask_bid_ratio
            and spread_ticks <= self.symbol.sandwich_spread_ticks
            and ask_sz > 0
        )

        now = time.time()

        if is_sandwich:
            if not self._sandwich_active:
                self._sandwich_active = True
                self._sandwich_start_ts = now
                self._eat_count = 0
            else:
                # 检查卖一是否被吃掉（价格变化）
                # 通过逐笔成交看近期是否有主动买单打到卖一价
                recent_trades = self.collector.trade_cache.get_recent(50)
                for t in reversed(recent_trades):
                    if t.side == "buy" and t.price >= ask_px - tick_sz * 0.1:
                        self._eat_count += 1
                        break  # 每次只计一次

            duration = now - self._sandwich_start_ts
            if duration >= self.symbol.sandwich_duration_sec:
                if self._eat_count >= 3:
                    # 夹板吸筹
                    return [DetectionResult(
                        tag=BehaviorTag.SANDWICH_ACCUMULATION,
                        symbol=self.inst_id,
                        timestamp=now,
                        confidence=0.75,
                        detail=f"夹板吸筹: 卖一挂单{ask_sz:.1f} >> 买一{bid_sz:.1f}, "
                               f"价差{spread}，持续{duration:.0f}秒，已检测{self._eat_count}次吸收",
                        data={"ask_sz": ask_sz, "bid_sz": bid_sz, "spread": spread,
                              "duration": duration, "eat_count": self._eat_count}
                    )]
        else:
            # 之前处于夹板状态，现在退出了
            if self._sandwich_active and self._sandwich_start_ts:
                duration = now - self._sandwich_start_ts
                if duration >= self.symbol.sandwich_duration_sec:
                    # 压单消失 → 可能是虚假压单（撤单了）
                    result = DetectionResult(
                        tag=BehaviorTag.SANDWICH_FAKE,
                        symbol=self.inst_id,
                        timestamp=now,
                        confidence=0.65,
                        detail=f"虚假压单: 持续{duration:.0f}秒的大压单已消失，"
                               f"疑似诱空后撤单",
                        data={"duration": duration}
                    )
                    self._sandwich_active = False
                    self._sandwich_start_ts = None
                    self._eat_count = 0
                    return [result]
            self._sandwich_active = False
            self._sandwich_start_ts = None
            self._eat_count = 0

        return []


# ==================== 4. 拖拉机单（拆单吸筹）检测器 ====================

class TractorOrderDetector(BaseDetector):
    """
    拖拉机单检测器
    在逐笔成交流中，短时间窗口内出现大量小额买单，且价格无明显上涨。
    """

    def detect(self) -> List[DetectionResult]:
        window_sec = self.symbol.tractor_window_sec
        now_ms = time.time() * 1000
        window_start_ms = now_ms - window_sec * 1000

        recent_trades = self.collector.trade_cache.get_all()
        if not recent_trades:
            return []

        # 筛选窗口内的买单
        window_buys = []
        prices_before = []
        prices_after = []

        found_start = False
        for t in recent_trades:
            if t.ts >= window_start_ms:
                if not found_start:
                    found_start = True
                if t.side == "buy":
                    window_buys.append(t)
            else:
                if not found_start:
                    prices_before.append(t.price)

        if len(window_buys) < self.symbol.tractor_min_orders:
            return []

        # 检查单笔金额是否相近且较小
        buy_amounts = []
        for t in window_buys:
            amt = t.price * t.size  # 近似USD金额
            buy_amounts.append(amt)

        avg_amt = sum(buy_amounts) / len(buy_amounts)
        if avg_amt > self.symbol.tractor_max_amount_usd:
            return []

        # 检查金额的离散程度（相近性）
        if len(buy_amounts) > 1:
            variance = sum((x - avg_amt) ** 2 for x in buy_amounts) / len(buy_amounts)
            std = variance ** 0.5
            if std > avg_amt * 0.5:  # 标准差太大，金额不够相近
                return []

        # 检查价格是否无明显上涨
        if prices_before:
            price_before = sum(prices_before) / len(prices_before)
        else:
            price_before = window_buys[0].price

        price_after = window_buys[-1].price
        price_change = (price_after - price_before) / price_before if price_before > 0 else 0

        if abs(price_change) > self.symbol.tractor_price_rise_max:
            return []

        return [DetectionResult(
            tag=BehaviorTag.TRACTOR_ACCUMULATION,
            symbol=self.inst_id,
            timestamp=time.time(),
            confidence=0.7,
            detail=f"隐蔽吸筹: {window_sec}秒内{len(window_buys)}笔小额买单，"
                   f"均价{avg_amt:.1f}U，涨幅仅{price_change:.4%}",
            data={"order_count": len(window_buys), "avg_amount": avg_amt,
                  "price_change": price_change, "window_sec": window_sec}
        )]


# ==================== 5. 插针检测器 ====================

class WickDetector(BaseDetector):
    """
    插针检测器
    监控1m/5m K线，下影线 >> 实体，最低价短期跌破支撑并快速收回。
    """

    def detect(self) -> List[DetectionResult]:
        candles_5m = self.collector.candle_5m_cache.get_all()
        candles_1m = self.collector.candle_1m_cache.get_all()

        results = []

        # 检查最近的 5m K线
        for bar in ["5m", "1m"]:
            if bar == "5m":
                recent = candles_5m[-5:] if len(candles_5m) >= 5 else []
            else:
                recent = candles_1m[-10:] if len(candles_1m) >= 10 else []

            if not recent:
                continue

            candle = recent[-1]
            body = abs(candle.close - candle.open)
            total_range = candle.high - candle.low
            if total_range <= 0:
                continue

            # 下影线
            lower_shadow = min(candle.open, candle.close) - candle.low

            # 下影线 > 实体 * N
            if body > 0 and lower_shadow > body * self.symbol.wick_shadow_ratio:
                # 是否有支撑位（用近期低点或均线）
                prev_lows = [c.low for c in recent[:-1]]
                support = min(prev_lows) if prev_lows else candle.low

                # 最低价跌破支撑位 > 1%
                if (support - candle.low) / support > self.symbol.wick_support_break_pct:
                    # 收盘收回支撑上方
                    if candle.close > support:
                        vol_tag = ""
                        if candle.vol_ccy > 0:
                            avg_vol = sum(c.vol_ccy for c in recent[:-1]) / len(recent[:-1]) if len(recent) > 1 else candle.vol_ccy
                            vol_ratio = candle.vol_ccy / avg_vol if avg_vol > 0 else 1
                            vol_tag = f"，量能{'放大' if vol_ratio > 1.5 else '缩小'}({vol_ratio:.1f}x)"

                        tag = BehaviorTag.WICK_SHAKE_OUT
                        detail = (f"洗盘插针({bar}): 下影线{lower_shadow:.2f} > "
                                  f"实体{body:.2f}*{self.symbol.wick_shadow_ratio}，"
                                  f"最低{candle.low:.2f}跌破支撑{support:.2f}后收回{candle.close:.2f}{vol_tag}")

                        results.append(DetectionResult(
                            tag=tag,
                            symbol=self.inst_id,
                            timestamp=time.time(),
                            confidence=0.75,
                            detail=detail,
                            data={"bar": bar, "low": candle.low, "close": candle.close,
                                  "support": support, "shadow": lower_shadow,
                                  "body": body, "vol_ccy": candle.vol_ccy}
                        ))

        return results


# ==================== 6. 放量滞涨检测器 ====================

class StagnationDetector(BaseDetector):
    """
    放量滞涨检测器（最危险出货信号）
    高位区 + 倍量以上 + 阳线实体很短 + 上影线很长 / 横盘窄区间
    """

    def detect(self, position: Optional[BehaviorTag] = None) -> List[DetectionResult]:
        # 只在高位区检测
        if position != BehaviorTag.POSITION_HIGH:
            return []

        candles = self.collector.candle_5m_cache.get_all()
        if len(candles) < self.symbol.volume_ma_periods + 3:
            return []

        # 取最近3根K线检查
        recent = candles[-3:]
        historical = candles[-(self.symbol.volume_ma_periods + 3):-3]
        if not historical:
            return []

        avg_vol = sum(c.vol_ccy for c in historical) / len(historical)
        if avg_vol <= 0:
            return []

        results = []
        for c in recent:
            vol_ratio = c.vol_ccy / avg_vol
            if vol_ratio < self.symbol.bei_liang_ratio:
                continue

            # 是阳线
            if c.close <= c.open:
                continue

            total_range = c.high - c.low
            if total_range <= 0:
                continue

            body = c.close - c.open
            upper_shadow = c.high - c.close
            body_ratio = body / total_range
            shadow_ratio = upper_shadow / total_range

            # 实体短 + 上影线长 = 放量滞涨
            if (body_ratio < self.symbol.stagnation_body_max_ratio
                    and shadow_ratio > self.symbol.stagnation_shadow_min_ratio):
                results.append(DetectionResult(
                    tag=BehaviorTag.STAGNATION_DISTRIBUTION,
                    symbol=self.inst_id,
                    timestamp=time.time(),
                    confidence=0.8,
                    detail=f"高位派发: 放量{vol_ratio:.1f}倍，实体仅{body_ratio:.1%}，"
                           f"上影线{shadow_ratio:.1%} → 滞涨出货信号",
                    data={"vol_ratio": vol_ratio, "body_ratio": body_ratio,
                          "shadow_ratio": shadow_ratio, "close": c.close}
                ))

        # 横盘窄区间放量检查
        if len(candles) >= 5:
            recent_5 = candles[-5:]
            high = max(c.high for c in recent_5)
            low = min(c.low for c in recent_5)
            range_pct = (high - low) / low if low > 0 else 0

            if range_pct < self.symbol.stagnation_range_pct:
                # 窄幅横盘，检查是否放量
                total_vol = sum(c.vol_ccy for c in recent_5)
                if total_vol > avg_vol * 5 * self.symbol.bei_liang_ratio:
                    results.append(DetectionResult(
                        tag=BehaviorTag.STAGNATION_DISTRIBUTION,
                        symbol=self.inst_id,
                        timestamp=time.time(),
                        confidence=0.75,
                        detail=f"高位横盘派发: 5根K线振幅仅{range_pct:.2%}，"
                               f"但放量{total_vol/avg_vol/5:.1f}倍",
                        data={"range_pct": range_pct, "vol_ratio": total_vol / avg_vol / 5}
                    ))

        return results


# ==================== 7. 大单主动砸盘检测器 ====================

class WhaleSellDetector(BaseDetector):
    """
    大单主动砸盘检测器
    逐笔成交中出现单笔大卖单（金额远超近期平均），方向是主动吃买一。
    连续出现N次触发。
    """

    def __init__(self, symbol: SymbolConfig, collector: DataCollector,
                 detector_cfg: DetectorConfig):
        super().__init__(symbol, collector, detector_cfg)
        self._consecutive_count = 0
        self._whale_trades: deque = deque(maxlen=20)

    def detect(self) -> List[DetectionResult]:
        recent_trades = self.collector.trade_cache.get_recent(200)
        if len(recent_trades) < 50:
            return []

        # 计算近期平均单笔成交金额
        amounts = [t.price * t.size for t in recent_trades[-100:]]
        avg_amount = sum(amounts) / len(amounts) if amounts else 0
        if avg_amount <= 0:
            return []

        threshold = avg_amount * self.symbol.whale_multiplier

        # 检查最新的交易
        ticker = self.collector.get_latest_ticker()
        latest = recent_trades[-10:]  # 最新10条

        for t in latest:
            amt = t.price * t.size
            if t.side == "sell" and amt > threshold:
                # 确认是主动吃买一
                if ticker and t.price <= ticker.bid_px * 1.001:
                    self._consecutive_count += 1
                    self._whale_trades.append({
                        "price": t.price, "size": t.size,
                        "amount": amt, "ts": t.ts
                    })
            else:
                if self._consecutive_count > 0:
                    self._consecutive_count = max(0, self._consecutive_count - 1)

        if self._consecutive_count >= self.symbol.whale_consecutive:
            self._consecutive_count = 0
            total_amount = sum(w["amount"] for w in self._whale_trades)
            return [DetectionResult(
                tag=BehaviorTag.WHALE_SELL,
                symbol=self.inst_id,
                timestamp=time.time(),
                confidence=0.85,
                detail=f"大单主动出货: 连续{self.symbol.whale_consecutive}笔大卖单，"
                       f"合计{total_amount:.0f}U，单笔>均量{self.symbol.whale_multiplier}倍"
                       f"({threshold:.0f}U)",
                data={"consecutive": self.symbol.whale_consecutive,
                      "total_amount": total_amount, "avg_amount": avg_amount,
                      "threshold": threshold}
            )]

        return []


# ==================== 检测器管理器 ====================

class DetectorManager:
    """
    检测器管理器 - 统一管理所有检测器，定期执行检测
    """

    def __init__(self, symbol: SymbolConfig, collector: DataCollector,
                 detector_cfg: DetectorConfig):
        self.symbol = symbol
        self.collector = collector
        self.cfg = detector_cfg

        # 创建所有检测器
        self.position_detector = PositionDetector(symbol, collector, detector_cfg)
        self.volume_detector = VolumeAnomalyDetector(symbol, collector, detector_cfg)
        self.sandwich_detector = SandwichDetector(symbol, collector, detector_cfg)
        self.tractor_detector = TractorOrderDetector(symbol, collector, detector_cfg)
        self.wick_detector = WickDetector(symbol, collector, detector_cfg)
        self.stagnation_detector = StagnationDetector(symbol, collector, detector_cfg)
        self.whale_detector = WhaleSellDetector(symbol, collector, detector_cfg)

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._on_detection: Optional[callable] = None  # 回调函数

    def set_callback(self, callback: callable):
        """设置检测结果的回调函数"""
        self._on_detection = callback

    def run_once(self) -> List[DetectionResult]:
        """执行一轮检测"""
        all_results = []

        # 1. 位置判断（必须先执行，作为后续判断的背景板）
        pos_results = self.position_detector.detect()
        all_results.extend(pos_results)

        # 2. 成交量异常检测
        vol_results = self.volume_detector.detect()
        all_results.extend(vol_results)

        # 3. 夹板战术检测
        sandwich_results = self.sandwich_detector.detect()
        all_results.extend(sandwich_results)

        # 4. 拖拉机单检测
        tractor_results = self.tractor_detector.detect()
        all_results.extend(tractor_results)

        # 5. 插针检测
        wick_results = self.wick_detector.detect()
        all_results.extend(wick_results)

        # 6. 放量滞涨检测（需要位置信息）
        stagnation_results = self.stagnation_detector.detect(
            self.position_detector.current_position
        )
        all_results.extend(stagnation_results)

        # 7. 大单砸盘检测
        whale_results = self.whale_detector.detect()
        all_results.extend(whale_results)

        # 触发回调
        if all_results and self._on_detection:
            for r in all_results:
                try:
                    self._on_detection(r)
                except Exception as e:
                    logger.error(f"检测回调异常: {e}")

        return all_results

    def start(self, interval: float = None):
        """启动定期检测"""
        if interval is None:
            interval = self.cfg.detection_interval
        self._running = True
        self._thread = threading.Thread(target=self._run_loop,
                                        args=(interval,), daemon=True)
        self._thread.start()
        logger.info(f"[{self.symbol.inst_id}] 检测器已启动，间隔 {interval}s")

    def _run_loop(self, interval: float):
        while self._running:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"检测循环异常: {e}", exc_info=True)
            time.sleep(interval)

    def stop(self):
        self._running = False
        logger.info(f"[{self.symbol.inst_id}] 检测器已停止")
