# 模块 API 参考

## 核心入口

### main.py — 主流程

```python
# 主入口：单币种管道化流程
prepare_data_and_get_signal(config: Config, client: OKXClient, symbol: str) -> Dict

# 主循环：多币种轮询
main_loop(config: Config) -> None

# 单币种处理（信号 + 网格 + 自动交易）
process_symbol(config, client, grid_manager, symbol) -> None

# 自动交易
try_auto_trade(config, client, symbol, signal, price) -> (bool, str|None)
```

### okx_client.py — API 客户端

```python
class OKXClient:
    # K线
    get_klines(instId, bar, limit=300, after=None) -> List[List]
    
    # 实时数据
    get_ticker(instId) -> Dict
    get_orderbook(instId, sz=1) -> Dict
    get_mark_price(instId) -> Dict
    get_funding_rate(instId) -> Dict
    
    # 历史数据
    get_open_interest_history(instId, period, limit) -> List[List]
    get_taker_volume_contract(instId, period, limit) -> List[List]
    get_long_short_account_ratio(instId, period, limit) -> List[List]
    get_top_trader_long_short_account_ratio(instId, period, limit) -> List[List]
    get_funding_rate_history(instId, limit) -> List[Dict]
    
    # 扩展
    get_insurance_fund(type, uly, limit) -> List
    get_premium_history(instId, limit) -> List
    get_index_tickers(quoteCcy) -> Dict
    get_index_candles(instId, bar, limit) -> List
    get_mark_price_candles(instId, bar, limit) -> List
    get_position_ratio_top_trader(instId, period, limit) -> List
    get_elite_position_trend(instId) -> Dict
    get_option_oi_strike(uly) -> List
    get_option_oi_ratio(uly, period) -> List
    get_position_tiers(instType, uly, mgnMode) -> List
    
    # 交易相关
    get_instrument_info(instId) -> Dict
    get_leverage_info(instId, mgnMode) -> Dict
    get_account_config() -> Dict
    get_trade_fee(instType, instId) -> Dict
```

### data_utils.py — 数据处理

```python
# K线加载
load_history_data(history_dir, symbol) -> pd.DataFrame
resample_ohlcv(raw, target_freq, symbol) -> pd.DataFrame
fetch_target_klines(config, client, symbol, existing_df) -> pd.DataFrame
merge_data(hist, new) -> pd.DataFrame
parse_kline_df(raw, symbol) -> pd.DataFrame
get_or_update_kline(config, client, symbol, bar, limit, cache_key) -> pd.DataFrame

# 指标计算
calculate_indicators(df, config) -> pd.DataFrame
# 添加: MA5, MA10, MA30, RSI14, MACD, Signal, MACD_Hist, K, D, J
# 可选: BB_upper, BB_lower, BB_width, BB_position, ATR, VWAP
```

### manipulation/engine.py — 庄家检测引擎

```python
# Dataclass 参数封装
@dataclass
class ManipulationInput:
    df: pd.DataFrame
    current_price: float
    oi_data, taker_vol, funding_rate, ls_ratio, elite_ratio, ...
    wick_shadow_ratio: float = 3.0

# 主函数（支持两种调用方式）
run_manipulation_analysis(
    df=..., current_price=..., ...  # 传统方式
    # 或 _input=ManipulationInput(df=..., ...)  # Dataclass方式
) -> Dict
```

### manipulation/synthesis.py — 7维合成

```python
# 加权合成方向
synthesize_direction(crowd, elite, oi_flow, taker, funding, 
                     mark_deviation, index_price, current_price,
                     elite_trend, basis_tf, atr, config) -> Dict

# 阶段与方向综合
determine_phase_and_direction(synthesis, position, detections) -> tuple
# 返回: (phase_old, phase_cn, direction, confidence, summary_signals)
```

### web_app.py — Web API

```python
Flask 应用:
  GET  /             — 暗色仪表盘首页
  GET  /api/config   — 获取 config.json
  POST /api/config   — 更新配置
  POST /api/generate_signal — 生成信号（symbol 参数）
  POST /api/backtest — 运行回测（symbol 参数）
```

### backtest.py — 回测

```python
class Portfolio:
    def __init__(initial_capital, fee_rate, slippage)
    def open_position(direction, price, size, timestamp, signal)
    def close_position(price, timestamp, reason)

def run_backtest(config, symbol) -> Dict
# 返回: total_return, max_drawdown, sharpe_ratio, final_equity, ...
```

### grid_manager.py — 网格策略

```python
class GridManager:
    def __init__(config, default_range_percent, default_grid_count)
    def get_grid_suggestions(current_price, signal) -> str
```

### ai_analysis.py — AI 分析

```python
def ai_analysis(config, df, symbol, 
                rule_signal, orderbook_data, oi_data, funding_data,
                taker_volume_data, long_short_ratio_data, elite_ratio_data,
                mark_price_data, price_limit_data, premium_history,
                long_term_df, long_trend, long_ma60,
                tech_batch, manipulation) -> Dict
```
