# BehaviorTag 行为标签 & 检测器参考

## 标签枚举 (detectors.py)

```python
class BehaviorTag(Enum):
    # ── 位置 ──
    POSITION_LOW = "低位区"
    POSITION_MID = "中位区"
    POSITION_HIGH = "高位区"

    # ── 成交量 ──
    VOL_DILIANG = "地量"           # 当前量 < 20均量 × 0.5
    VOL_BEILIANG = "倍量"          # 当前量 > 20均量 × 2
    VOL_TIANLIANG = "天量"         # 当前量 > 20均量 × 4
    VOL_DUIDAO = "疑似对倒"        # 放量 + 极小振幅

    # ── 夹板战术 ──
    SANDWICH_ACCUMULATION = "夹板吸筹"  # 卖一压单 + 持续吃掉
    SANDWICH_FAKE = "虚假压单"          # 压单突然消失

    # ── 拖拉机单 ──
    TRACTOR_ACCUMULATION = "隐蔽吸筹(拆单)"  # 大量小额买单

    # ── 插针 ──
    WICK_DOWN_TEST = "向下试盘"           # 下影线测试支撑
    WICK_SHAKE_OUT = "洗盘插针"            # 下影线 > 实体 × ratio，跌破支撑后收回
    WICK_UP_DISTRIBUTION = "向上插针(诱多出货)"  # 上影线 > 实体 × ratio

    # ── 放量滞涨 ──
    STAGNATION_DISTRIBUTION = "高位派发"   # 高位 + 倍量 + 小实体长上影

    # ── 大单砸盘 ──
    WHALE_SELL = "大单主动出货"            # 连续大单主动卖
```

## 7 大检测器

| # | 检测器 | 类名 | 检测对象 |
|---|--------|------|---------|
| 1 | 位置判断器 | `PositionDetector` | 日线高位/低位百分位 |
| 2 | 成交量异常 | `VolumeAnomalyDetector` | 地量/倍量/天量/对倒 |
| 3 | 夹板战术 | `SandwichDetector` | 卖一挂单 vs 买一挂单 |
| 4 | 拖拉机单 | `TractorOrderDetector` | 小额买单模式识别 |
| 5 | 插针检测器 | `WickDetector` | 1m/5m K线影线异常 |
| 6 | 放量滞涨 | `StagnationDetector` | 高位 + 放量 + 不涨 |
| 7 | 大单砸盘 | `WhaleSellDetector` | 连续大额主动卖 |

## 插针检测关键参数

### 实时检测器 (WickDetector)
- `wick_shadow_ratio`: 影线/实体比例 (默认 3.0)
- `wick_support_break_pct`: 跌破支撑百分比
- 检测周期: 1m 和 5m K 线

### 离线辅助函数 (kline_helpers.py)
```python
detect_wick(df, symbol, wick_shadow_ratio=3.0) -> List[DetectionResult]
# 对 DataFrame 中的K线逐个检测插针
# wick_shadow_ratio: 可配置，config.json 中 WICK_SHADOW_RATIO 字段
```

## 修改标签

在 `detectors.py` 中添加新标签：
```python
class BehaviorTag(Enum):
    # ... 现有标签 ...
    MY_NEW_TAG = "新标签描述"
```

然后在对应检测器中添加触发逻辑，再在 `synthesis.py` 的 `determine_phase_and_direction()` 中处理新标签对阶段的影响。

## DetectionResult 数据结构

```python
@dataclass
class DetectionResult:
    tag: BehaviorTag           # 标签枚举
    symbol: str                # 交易对
    timestamp: float           # 检测时间戳
    confidence: float = 1.0    # 置信度 0~1
    detail: str = ""           # 详细描述
    data: dict = {}            # 附加数据
```
