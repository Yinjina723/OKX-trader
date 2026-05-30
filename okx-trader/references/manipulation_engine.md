# 庄家博弈检测引擎 V3 — 详解

## 架构

```
manipulation_v2.py (入口) → manipulation/engine.py (主引擎)
    └── run_manipulation_analysis(df, price, oi, taker, funding, ...)
        ├── [维度1] crowd.py      — 散户拥挤度（反向指标）
        ├── [维度2] elite.py      — 精英/散户背离 + 多周期趋向
        ├── [维度3] oi_flow.py    — OI 持仓流向四象限
        ├── [维度4] taker.py      — Taker 聪明钱方向
        ├── [维度5] funding.py    — 资金费率极端值
        ├── [维度6] basis.py      — 多周期基差分析
        ├── [维度7] elite.py::analyze_elite_trend — 精英多周期趋向
        ├── synthesis.py          — 7维加权合成 + 波动率自适应阈值
        ├── kline_helpers.py      — K线位置/量异常/插针/滞涨
        ├── wyckoff.py            — 威科夫模式识别
        └── predict.py            — 下一步预测 + 综合点位
```

## 7 维权重配置

默认权重（可通过 `config.MANIPULATION_WEIGHTS` 覆盖）：

| 维度 | 权重 | 逻辑 |
|------|------|------|
| 精英vs散户背离 | **30%** | 最可靠，精英仓位 vs 散户仓位背离 |
| 精英多周期趋向 | **15%** | 精英在15m/1H/4H的仓位趋势 |
| 散户拥挤度(反向) | **20%** | 散户过度做多→看空，过度做空→看多 |
| OI持仓流向 | **15%** | OI+价格四象限判定资金方向 |
| Taker主动买卖 | **10%** | 主动吃单方向反映聪明钱 |
| 资金费率 | **5%** | 极端费率预示反转 |
| 基差多周期 | **5%** | 期货-现货价差异常 |

## 方向判定的波动率自适应阈值

```
ATR/Price 比例 → 动态调整判定阈值
  高波动 (ATR/Price > 0.02): 阈值↑ (最高 0.45)
  低波动 (ATR/Price < 0.005): 阈值↓ (最低 0.18)
  默认基准: 0.30

net_score >= threshold → long
net_score <= -threshold → short
其他 → neutral（但给出弱倾向）
```

## 阶段判定逻辑

| 方向 | 权重范围 | 阶段 |
|------|---------|------|
| long | bull_w ≥ 0.8 | 拉升期 (markup) |
| long | bull_w ≥ 0.4 | 吸筹期 (accumulation) |
| long | 有洗盘插针 | 洗盘震仓 (shakeout) |
| short | bear_w ≥ 0.8 | 砸盘期 (markdown) |
| short | bear_w ≥ 0.4 | 派发期 (distribution) |
| short | 有诱多出货 | 派发期(诱多出货) |
| neutral | 低位区 | 吸筹期(盘整待突破) |
| neutral | 高位区 | 派发期(盘整待选择) |

## 返回结构

```python
{
    "phase_result": {            # 阶段判定结果
        "phase": "accumulation", # 英文阶段名
        "phase_cn": "吸筹期",    # 中文阶段名
        "score": 6,              # 得分 (net_score * 10)
        "confidence": 0.75,      # 置信度
        "signals": [...],        # 信号描述列表
        "dimension_scores": {...},# 各维度得分
        "weighted_bull": 0.45,   # 多头权重
        "weighted_bear": 0.15,   # 空头权重
        "oi_change_pct": 0.02,   # OI变化%
        "net_taker": 123.45,     # 净Taker量
    },
    "elite_panel": {             # 精英面板
        "position_ratio": 1.2,   # 仓位比(>1多,<1空)
        "position_direction": "long",
        "elite_summary": "🟢 精英正在做多",
        ...
    },
    "wyckoff": {                 # 威科夫分析
        "schematic": "accumulation_schematic_1",
        "events": [...],
    },
    "next_move": {               # 庄家下一步预测
        "next_action": "震荡蓄力后拉升",
        "direction": "long",
        "target_price": 0.1234,
        "stop_price": 0.1100,
        "time_frame": "4-12小时",
    },
    "predicted_point": {         # 综合预测点位
        "ensemble_target": 0.1250,
        "ensemble_direction": "long",
        "distance_pct": 2.5,
        "confidence": 0.70,
    },
}
```

## 插针检测参数

`WICK_SHADOW_RATIO` (默认 3.0): 影线/实体比例阈值
- 下影线 > 实体 × ratio → 向下插针（洗盘/试盘）
- 上影线 > 实体 × ratio → 向上插针（诱多出货）

## BehaviorTag 枚举

见 `references/behavior_tags.md`。
