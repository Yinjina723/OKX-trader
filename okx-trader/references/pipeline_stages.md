# 5 阶段流水线详解

## 总览

```
Stage 1: _stage_load_data()     → K线加载/合并/指标计算
Stage 2: _stage_fetch_aux()     → 并行拉取15+数据源
Stage 3: _stage_extract_metrics() → 预提取公共指标
Stage 4: _stage_run_analysis()  → 并行规则/技术/操盘分析
Stage 5: _stage_post_process()  → AI/共振/冲突/过滤/写入
```

## Stage 1: 数据加载

**函数**: `_stage_load_data(config, client, symbol) -> pd.DataFrame`

**流程**:
1. 先尝试 `get_or_update_kline()` — P1 内存缓存，增量更新
2. 缓存未命中 → 降级：`load_history_data()` + `fetch_target_klines()` + `merge_data()`
3. 计算技术指标: `calculate_indicators(df, config)`

**输出**: 包含 MA/RSI/MACD/KDJ/BB/ATR/VWAP 的 DataFrame

## Stage 2: 辅助数据拉取

**函数**: `_stage_fetch_aux(config, client, symbol) -> Dict`

**并行拉取的数据源** (使用模块级线程池 8 workers):

| 类别 | 数据源 | TTL |
|------|--------|-----|
| 实时 | orderbook, funding, mark, limit | 0s(不缓存) |
| 快速变化 | oi_data, taker_vol, long_short, elite | 300s |
| 多周期 | oi/taker/ls × 15m/1H/4H | 300s |
| 慢速 | insurance, premium, funding_hist, index_tickers, index_candles, mark_candles, elite_pos, elite_trend, option_oi_strike, option_pcr, position_tiers | 300s |

**超时**: 每个请求 20s，失败则降级为 None

## Stage 3: 指标预提取

**函数**: `_stage_extract_metrics(results, config) -> Dict`

一次性提取所有分析模块需要的公共数值，避免重复计算：
- `funding_rate`, `ls_ratio`, `elite_ratio`
- `latest_oi`, `oi_change`
- `net_taker`, `bid_ask_ratio`
- `premium_val`, `idx_price`
- `sentiment_data` (Fear Gauge)
- 多周期数据结构整理

## Stage 4: 并行分析

**函数**: `_stage_run_analysis(config, df, price, metrics, long_trend, long_ma60, symbol) -> Dict`

**3 个并行分析模块** (使用模块级线程池 4 workers):

1. **规则策略** (`RULE_STRATEGY_ENABLED`): `generate_rule_signal()`
2. **技术分析** (`TECHNICAL_BATCH_ENABLED`): `run_technical_batch()`
3. **庄家操盘** (`MANIPULATION_ENABLED`): `run_manipulation_analysis()`

**超时**: 每个模块 60s

## Stage 5: 后处理

**函数**: `_stage_post_process(config, client, df, analysis, price, symbol, long_term_df, long_trend, long_ma60, metrics) -> Dict`

**6 步后处理**:
1. **AI 分析**: `ai_analysis()` → DeepSeek 多模型共识
2. **多周期共振**: `multi_timeframe_confluence()` → 趋势/结构/入场共振
3. **操盘预测更新**: `calculate_manipulation_target()` → ATR 驱动综合点位
4. **信号标准化**: `normalize_signal()` → 统一格式
5. **冲突仲裁**: `resolve_signal_conflicts()` → 多信号冲突时强制 neutral
6. **后过滤**: `signal_post_filter()` → 极值过滤

**写入**: `save_signals_history()` + `save_signal_to_txt()`

## 主入口: prepare_data_and_get_signal()

```python
def prepare_data_and_get_signal(config, client, symbol) -> Dict:
    df = _stage_load_data(...)
    results = _stage_fetch_aux(...)
    price = client.get_ticker(symbol)['last']
    long_df, trend, ma60 = _stage_get_long_term(...)
    metrics = _stage_extract_metrics(...)
    analysis = _stage_run_analysis(...)
    signal = _stage_post_process(...)
    return signal
```

## 修改流水线

修改某个 stage 时，其他 stage 不受影响。例如：
- 新增数据源 → 改 Stage 2 的 `_stage_fetch_aux`
- 新增分析维度 → 改 Stage 4 的 `_stage_run_analysis`
- 调整 AI 逻辑 → 改 Stage 5 的 `ai_analysis` 调用
