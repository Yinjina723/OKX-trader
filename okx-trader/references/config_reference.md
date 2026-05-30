# config.json 配置项完全参考

## 核心配置

```json
{
  "API_KEY": "你的OKX API Key",
  "SECRET_KEY": "你的OKX Secret Key",
  "PASSPHRASE": "你的OKX Passphrase",
  "SITE": "https://www.okx.com",
  "PROXY_URL": "",

  "SYMBOLS": ["AI-USDT-SWAP", "BTC-USDT-SWAP"],
  "TARGET_TIMEFRAME": "15m",
  "LONG_TERM_TIMEFRAME": "1H",
  "INTERVAL_MINUTES": 30,
  "DAYS": 7,
  "LOOKBACK": 100,
  "OUTPUT_DIR": "./output",

  "AI_API_KEY": "DeepSeek API Key",
  "AI_BASE_URL": "https://api.deepseek.com/v1",
  "AI_MODEL": "deepseek-chat"
}
```

## 分析模块开关

```json
{
  "RULE_STRATEGY_ENABLED": false,
  "TECHNICAL_BATCH_ENABLED": true,
  "MANIPULATION_ENABLED": true,
  "SENTIMENT_ENABLED": true,
  "MTF_CONFLUENCE_ENABLED": true,
  "ENABLE_PREMIUM": false
}
```

## 庄家检测权重

```json
{
  "MANIPULATION_WEIGHTS": {
    "elite_divergence": 0.30,
    "elite_multi_tf": 0.15,
    "crowd": 0.20,
    "oi_flow": 0.15,
    "taker": 0.10,
    "funding": 0.05,
    "basis": 0.05
  }
}
```

## 波动率与多周期

```json
{
  "VOLATILITY_ADAPTIVE_THRESHOLD": true,
  "MTF_TREND_TIMEFRAME": "4H",
  "MTF_STRUCTURE_TIMEFRAME": "1H",
  "WICK_SHADOW_RATIO": 3.0
}
```

## AI 配置

```json
{
  "AI_ECO_MODE": true,
  "AI_TEMPERATURE": 0.7,
  "AI_ECO_CONSENSUS_THRESHOLD": 2,
  "AI_ENSEMBLE_ENABLED": true
}
```

## 网格策略

```json
{
  "GRID_DEFAULT_RANGE_PERCENT": 0.2,
  "GRID_DEFAULT_COUNT": 10,
  "GRID_TYPE": "geometric",
  "ATR_MULTIPLIER_CENTER": 0.5,
  "ATR_MULTIPLIER_WIDTH": 2.5
}
```

## 自动交易

```json
{
  "AUTO_TRADE_ENABLED": false,
  "AUTO_TRADE_SIZE_USDT": 10.0,
  "AUTO_TRADE_ORDER_TYPE": "market",
  "AUTO_TRADE_TD_MODE": "cross",
  "DEFAULT_LEVERAGE": 2,
  "FEE_RATE": 0.001
}
```

## 回测

```json
{
  "BACKTEST_ENABLED": false,
  "BACKTEST": {
    "ENABLED": false,
    "INITIAL_CAPITAL": 1000.0
  }
}
```

## 扩展数据源

```json
{
  "ENABLE_NEW_DIMENSIONS": {
    "FUNDING_RATE_HISTORY": true,
    "INDEX_TICKERS_BASIS": true,
    "ELITE_POSITION_RATIO": true,
    "OPTION_MAX_PAIN": true,
    "OPTION_PUT_CALL_RATIO": true,
    "POSITION_TIERS_ANALYSIS": true,
    "ELITE_TREND_MULTI_TF": true
  }
}
```

## 数据参数

```json
{
  "OI_LIMIT": 20,
  "BIGDATA_LIMIT": 20,
  "ORDERBOOK_DEPTH": 20,
  "OPEN_INTEREST_PERIOD": "5m",
  "BIGDATA_PERIOD": "5m"
}
```

## 修改配置后

修改 `config.json` 后无需重启：
- Web 面板：通过 `/api/config` POST 动态更新
- 命令行模式：重新运行 `python main.py` 即可
