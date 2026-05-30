# config.py
"""
配置模块：从 config.json 读取所有运行参数，Config 类属性与 JSON 键一一对应，缺省使用代码内默认值。
详见 Config 类文档字符串中的分组说明。
"""
import json
import os


class Config:
    """
    所有配置参数的集中入口，从同目录下的 config.json 读取，缺失时使用代码里的默认值。

    使用方式：
    - 实际修改时，只需要编辑 config.json，不要改这里的默认值。
    - 这里的属性名（如 HISTORY_DIR、SYMBOLS 等）都对应 config.json 里的同名键。

    主要配置分组（在 config.json 中填写）：
    - 路径相关：
        HISTORY_DIR      历史K线CSV目录，例如 "./data/history"
        OUTPUT_DIR       输出和日志目录，例如 "./output"
    - 交易标的与周期：
        SYMBOLS          交易对列表，逗号分隔或JSON数组，如 ["ESP-USDT-SWAP","BTC-USDT-SWAP"]
        TARGET_TIMEFRAME 分析周期，如 "1m","5m","15m","1H"
        DAYS             启动时补历史数据的天数
    - DeepSeek / AI 分析：
        DEEPSEEK_API_KEY DeepSeek 的 API Key
        LOOKBACK         发送给 AI 的最近K线根数
    - 技术指标参数：
        RSI_PERIOD, MA_FAST, MA_PERIOD, MA_SLOW 等
    - OKX API 与实盘/模拟盘开关：
        OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE 官方提供的三件套
        SIMULATED        "1"=模拟盘，"0"=实盘
    - 自动下单与资金管理：
        AUTO_TRADE_ENABLED      是否启用信号自动下单
        AUTO_TRADE_SIZE_USDT    每笔开仓名义金额（USDT）
        AUTO_TRADE_ORDER_TYPE   "market" 市价 或 "limit" 限价
        AUTO_TRADE_TD_MODE      "cross" 全仓 或 "isolated" 逐仓
        MIN_REWARD_RISK         最低可接受奖惩比（如 1.5）
    - 网格交易：
        GRID_DEFAULT_RANGE_PERCENT, GRID_DEFAULT_COUNT 等全局默认
        PER_COIN_GRID_CONFIG    每个币种的网格单独参数（见下方示例注释）
    - 回测：
        BACKTEST 下的各字段：初始资金、手续费、滑点、起止日期等。
    """
    def __init__(self, config_file="config.json"):
        with open(config_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # ----- 路径设置 -----
        self.HISTORY_DIR = data.get("HISTORY_DIR", "./data/history")
        self.AUX_DATA_DIR = os.path.join(self.HISTORY_DIR, "aux_data")
        self.OUTPUT_DIR = data.get("OUTPUT_DIR", "./output")
        self.STATE_FILE = os.path.join(self.OUTPUT_DIR, "trade_state.json")

        # ----- 交易对与周期 -----
        # 支持单个字符串或列表，统一转换为列表
        symbols = data.get("SYMBOL", data.get("SYMBOLS", []))
        if isinstance(symbols, str):
            self.SYMBOLS = [symbols]
        elif isinstance(symbols, list):
            self.SYMBOLS = symbols
        else:
            self.SYMBOLS = []  # 默认为空列表，后续需检查

        self.SOURCE_TIMEFRAME = "1m"                   # 固定为1分钟原始数据
        self.TARGET_TIMEFRAME = data.get("TARGET_TIMEFRAME", "15m")  # 分析周期
        self.DAYS = data.get("DAYS", 5)                 # 下载最近几天的数据

        # ----- DeepSeek API 配置 -----
        self.DEEPSEEK_API_KEY = data.get("DEEPSEEK_API_KEY", "")

        # ----- AI分析参数 -----
        self.LOOKBACK = data.get("LOOKBACK", 30)        # 发送给AI的K线根数

        # ----- 策略指标参数 -----
        self.RSI_PERIOD = data.get("RSI_PERIOD", 14)
        self.MA_PERIOD = data.get("MA_PERIOD", 10)
        self.MA_FAST = data.get("MA_FAST", 5)
        self.MA_SLOW = data.get("MA_SLOW", 30)

        # ----- 辅助数据配置 -----
        self.ORDERBOOK_DEPTH = data.get("ORDERBOOK_DEPTH", 1)      # 订单簿深度层数
        self.OPEN_INTEREST_PERIOD = data.get("OPEN_INTEREST_PERIOD", "5m")
        self.FUNDING_RATE_INST_ID = data.get("FUNDING_RATE_INST_ID", None)  # 资金费率产品ID，一般与交易对相同
        self.BIGDATA_PERIOD = data.get("BIGDATA_PERIOD", "5m")      # taker/多空比等周期
        self.OI_LIMIT = data.get("OI_LIMIT", 24)                     # 🆕 持仓量历史拉取条数
        self.BIGDATA_LIMIT = data.get("BIGDATA_LIMIT", 12)           # 🆕 大数据指标拉取条数（taker/多空比等）

        # ----- 站点与代理 -----
        self.PROFILE = data.get("PROFILE", "default")
        self.SITE = data.get("SITE", "global")
        self.PROXY_URL = data.get("PROXY_URL", "")

        # ----- OKX API 密钥（模拟盘）-----
        self.OKX_API_KEY = data.get("OKX_API_KEY", "")
        self.OKX_SECRET_KEY = data.get("OKX_SECRET_KEY", "")
        self.OKX_PASSPHRASE = data.get("OKX_PASSPHRASE", "")
        self.SIMULATED = data.get("SIMULATED", "0")                 # 1=模拟盘，0=实盘

        # ----- 交易资金管理参数 -----
        self.MAX_RISK_PER_TRADE = data.get("MAX_RISK_PER_TRADE", 0.02)   # 单笔最大风险占资金比例 (2%)
        self.DEFAULT_LEVERAGE = data.get("DEFAULT_LEVERAGE", 2)          # 默认杠杆倍数
        self.MIN_BALANCE_USDT = data.get("MIN_BALANCE_USDT", 10)         # 最低余额，低于此值不开仓
        self.INTERVAL_MINUTES = data.get("INTERVAL_MINUTES", 15)         # 主循环间隔（分钟）

        # ----- 加仓策略参数 -----
        self.ADD_POSITION_ENABLED = data.get("ADD_POSITION_ENABLED", True)   # 是否启用加仓
        self.MAX_ADD_COUNT = data.get("MAX_ADD_COUNT", 2)                    # 最大加仓次数
        self.ADD_THRESHOLD_PERCENT = data.get("ADD_THRESHOLD_PERCENT", 0.03) # 加仓触发阈值（3%）
        self.ADD_POSITION_FACTOR = data.get("ADD_POSITION_FACTOR", 1.0)      # 加仓数量因子（相对于首次）

        # ----- 滑点控制选项 -----
        self.MAX_SLIPPAGE_POINTS = data.get("MAX_SLIPPAGE_POINTS", 20)      # 最大允许价格偏差点数（tick）
        self.USE_MARKET_STOP = data.get("USE_MARKET_STOP", True)            # 是否使用市价止损

        # ----- 止损检查间隔（分钟）-----
        self.STOP_CHECK_INTERVAL = data.get("STOP_CHECK_INTERVAL", 5)       # 每5分钟检查止损

        # ----- 预测时间范围（分钟）-----
        self.DEFAULT_SIGNAL_VALIDITY = data.get("DEFAULT_SIGNAL_VALIDITY", 30)  # 默认信号有效期（分钟）

        # ----- 资金与仓位管理 -----
        self.BASE_BALANCE = data.get("BASE_BALANCE", 100)  # 总资金 (USDT)
        self.DEFAULT_ALLOCATION_RATIO = data.get("DEFAULT_ALLOCATION_RATIO", 0.25)  # 默认仓位比例
        self.STRENGTH_ALLOCATION = data.get("STRENGTH_ALLOCATION", {
            "strong": 0.5,
            "medium": 0.25,
            "weak": 0.1
        })  # 不同强度对应的仓位比例

        # ----- 信号自动下单（根据信号自动开仓）-----
        self.AUTO_TRADE_ENABLED = data.get("AUTO_TRADE_ENABLED", True)   # 是否启用自动下单，默认关闭
        self.AUTO_TRADE_ORDER_TYPE = data.get("AUTO_TRADE_ORDER_TYPE", "market")  # market=市价，limit=限价
        self.AUTO_TRADE_SIZE_USDT = data.get("AUTO_TRADE_SIZE_USDT", 10.0)  # 每笔开仓名义价值（USDT）
        self.AUTO_TRADE_TD_MODE = data.get("AUTO_TRADE_TD_MODE", "cross")   # cross=全仓，isolated=逐仓
        self.MIN_REWARD_RISK = data.get("MIN_REWARD_RISK", 1.5)  # 最低奖惩比阈值，默认 1.5

        # ----- AI 增强开关 -----
        self.AI_ENSEMBLE_ENABLED = data.get("AI_ENSEMBLE_ENABLED", False)      # 🆕 AI集成投票（3次取多数）
        self.AI_ECO_MODE = data.get("AI_ECO_MODE", True)                       # 🆕 AI节能模式（高共识时跳过）
        self.AI_ECO_CONSENSUS_THRESHOLD = data.get("AI_ECO_CONSENSUS_THRESHOLD", 2)  # 🆕 节能触发：至少N个模块一致
        self.MTF_CONFLUENCE_ENABLED = data.get("MTF_CONFLUENCE_ENABLED", True)       # 🆕 多周期共振
        self.TECHNICAL_BATCH_ENABLED = data.get("TECHNICAL_BATCH_ENABLED", True)     # 🆕 技术分析批处理
        self.MANIPULATION_ENABLED = data.get("MANIPULATION_ENABLED", True)           # 🆕 操盘检测
        self.SENTIMENT_ENABLED = data.get("SENTIMENT_ENABLED", True)                 # 🆕 市场情绪分析
        self.WICK_SHADOW_RATIO = data.get("WICK_SHADOW_RATIO", 3.0)                 # 🆕 插针影体比阈值

        # ----- 基础网格交易参数（原有）-----
        self.GRID_AUTO_TRADE = data.get("GRID_AUTO_TRADE", False)
        self.GRID_DEFAULT_RANGE_PERCENT = data.get("GRID_DEFAULT_RANGE_PERCENT", 0.2)   # 默认区间宽度（固定百分比）
        self.GRID_DEFAULT_COUNT = data.get("GRID_DEFAULT_COUNT", 10)                    # 默认网格格数
        self.GRID_CHECK_INTERVAL = data.get("GRID_CHECK_INTERVAL", 10)
        self.GRID_STOP_ON_BREAKOUT = data.get("GRID_STOP_ON_BREAKOUT", True)

        # ========== 新增：增强版网格参数 ==========
        # 价格与交易所规则
        self.MIN_PRICE = data.get("MIN_PRICE", 0.01)               # 最小下单价格
        self.TICK_SIZE = data.get("TICK_SIZE", 0.0001)             # 最小价格变动单位

        # 交易成本
        self.FEE_RATE = data.get("FEE_RATE", 0.001)                # 单边手续费率（如0.1%）
        self.SLIPPAGE = data.get("SLIPPAGE", 0.0005)               # 预估滑点（0.05%）

        # 网格类型与基本结构
        self.GRID_TYPE = data.get("GRID_TYPE", "geometric")        # "arithmetic"（等差）或 "geometric"（等比）

        # ATR动态调整相关（当信号提供atr时启用）
        self.ATR_MULTIPLIER_CENTER = data.get("ATR_MULTIPLIER_CENTER", 0.5)   # 中心偏移的ATR倍数
        self.ATR_MULTIPLIER_WIDTH = data.get("ATR_MULTIPLIER_WIDTH", 2.5)     # 区间宽度的ATR倍数

        # 边界保护
        self.MAX_RANGE_FACTOR = data.get("MAX_RANGE_FACTOR", 0.3)  # 网格边界相对于当前价格的最大偏移比例（如0.3表示±30%）

        # 币种特定网格配置（覆盖全局默认值）
        # 格式示例：
        # "PER_COIN_GRID_CONFIG": {
        #     "BTCUSDT": {
        #         "grid_type": "geometric",
        #         "range_percent": 0.15,
        #         "grid_count": 15,
        #         "tick_size": 0.01,
        #         "fee_rate": 0.0004,
        #         "atr_multiplier_width": 2.5
        #     },
        #     "SHIBUSDT": {
        #         "grid_type": "arithmetic",
        #         "range_percent": 0.4,
        #         "grid_count": 20,
        #         "min_price": 0.000001,
        #         "tick_size": 0.000001,
        #         "fee_rate": 0.001,
        #         "slippage": 0.002,
        #         "atr_multiplier_width": 4.0
        #     }
        # }
        self.PER_COIN_GRID_CONFIG = data.get("PER_COIN_GRID_CONFIG", {})

        # ----- 高级数据开关与长周期配置 -----
        self.ENABLE_PREMIUM = data.get("ENABLE_PREMIUM", True)
        self.ENABLE_MARK_CANDLES = data.get("ENABLE_MARK_CANDLES", True)
        self.ENABLE_INDEX_CANDLES = data.get("ENABLE_INDEX_CANDLES", False)  # 需要指数ID
        self.ADVANCED_INDICATORS = data.get("ADVANCED_INDICATORS", True)
        self.LONG_TERM_TIMEFRAME = data.get("LONG_TERM_TIMEFRAME", "1H")     # 长周期趋势，如 '1H','4H'

        # 🆕 新增操纵维度数据开关（用于 manipulation_analysis.py）
        new_dim = data.get("ENABLE_NEW_DIMENSIONS", {})
        self.ENABLE_FUNDING_RATE_HISTORY = new_dim.get("FUNDING_RATE_HISTORY", True)
        self.ENABLE_INDEX_TICKERS_BASIS = new_dim.get("INDEX_TICKERS_BASIS", True)
        self.ENABLE_ELITE_POSITION_RATIO = new_dim.get("ELITE_POSITION_RATIO", True)
        self.ENABLE_OPTION_MAX_PAIN = new_dim.get("OPTION_MAX_PAIN", True)
        self.ENABLE_OPTION_PUT_CALL_RATIO = new_dim.get("OPTION_PUT_CALL_RATIO", True)
        self.ENABLE_POSITION_TIERS_ANALYSIS = new_dim.get("POSITION_TIERS_ANALYSIS", True)
        self.ENABLE_ELITE_TREND_MULTI_TF = new_dim.get("ELITE_TREND_MULTI_TF", True)

        # ----- 规则策略配置 -----
        rule_config = data.get("RULE_STRATEGY", {})
        self.RULE_STRATEGY_ENABLED = rule_config.get("ENABLED", False)
        self.RSI_OVERSOLD = rule_config.get("RSI_OVERSOLD", 30)
        self.RSI_OVERBOUGHT = rule_config.get("RSI_OVERBOUGHT", 70)
        self.BB_POSITION_THRESHOLD = rule_config.get("BB_POSITION_THRESHOLD", 0.2)
        self.TREND_MA_FAST = rule_config.get("TREND_MA_FAST", 5)      # 趋势均线快线
        self.TREND_MA_SLOW = rule_config.get("TREND_MA_SLOW", 30)     # 趋势均线慢线
        self.FUNDING_RATE_THRESHOLD = rule_config.get("FUNDING_RATE_THRESHOLD", 0.0001)
        self.LS_RATIO_THRESHOLD = rule_config.get("LS_RATIO_THRESHOLD", 2.0)

        # ----- 回测配置 -----
        backtest_config = data.get("BACKTEST", {})
        self.BACKTEST_ENABLED = backtest_config.get("ENABLED")
        self.BACKTEST_START = backtest_config.get("START_DATE")       # 回测开始日期，如 "2025-01-01"
        self.BACKTEST_END = backtest_config.get("END_DATE")           # 回测结束日期
        self.BACKTEST_INITIAL_CAPITAL = backtest_config.get("INITIAL_CAPITAL", 10000)
        self.BACKTEST_FEE_RATE = backtest_config.get("FEE_RATE", 0.0005)
        self.BACKTEST_SLIPPAGE = backtest_config.get("SLIPPAGE", 0.001)
        self.BACKTEST_SIGNAL_SOURCE = backtest_config.get("SIGNAL_SOURCE", "replay")  # "replay" 或 "rule"

        # ----- 高级提示词模板（如果未配置则使用原有模板）-----
        self.PROMPT_TEMPLATE_ADVANCED = data.get("PROMPT_TEMPLATE_ADVANCED", """你是一位资深的数字货币分析师兼交易员。以下是 {symbol} 永续合约的详细市场数据：

【{target_tf} K线数据（最近 {lookback} 根）】
{data_text}

【更长周期趋势（{long_tf}）】
- {long_tf} MA60: {long_ma60:.2f}
- {long_tf} 趋势: {long_trend}

【市场情绪指标】
- 资金费率: {funding_rate:.6f}
- 持仓量（最新）: {oi:.2f}
- 持仓量变化（较前值）: {oi_change:.2%}
- 多空人数比（全体）: {ls_ratio:.3f}
- 多空人数比（精英）: {elite_ratio:.3f}
- 主动买卖净额: {net_taker:.2f}
- 溢价指数: {premium:.6f}

【订单簿压力（买一/卖一）】
- 买一价: {bid_price:.4f} 数量: {bid_size:.2f}
- 卖一价: {ask_price:.4f} 数量: {ask_size:.2f}
- 买卖盘口比: {bid_ask_ratio:.2f}

【技术指标摘要】
- 布林带位置: {bb_position:.2f}（0~1）
- ATR: {atr:.4f}
- RSI14: {rsi:.2f}
- MACD Hist: {macd_hist:.4f}
- KDJ (K/D/J): {k:.2f}/{d:.2f}/{j:.2f}

请根据以上所有信息，给出具体的交易建议，包括方向、入场价、止损价、第一目标位、第二目标位、信号强度，并附带一句市场状态描述。以 JSON 格式输出，不要包含其他文字。

JSON 格式：
{{
  "direction": "long" 或 "short" 或 "neutral",
  "entry": 价格数值,
  "stop_loss": 价格数值,
  "take_profit1": 第一目标价,
  "take_profit2": 第二目标价,
  "strength": "strong" 或 "medium" 或 "weak",
  "market_state": "例如：多头主导、空头主导、震荡整理、变盘前夕等",
  "key_support": 关键支撑位价格数值（可以为 null）,
  "key_resistance": 关键压力位价格数值（可以为 null）
}}
如果在描述中提到“关键支撑位”或“关键压力位”，请务必在 key_support / key_resistance 字段中给出对应的大致价格数值。
如果无法给出明确建议，direction 设为 "neutral"，其他字段可为 null，market_state 描述当前状态。
""")

        # ----- 原有提示词模板（保持不变）-----
        self.PROMPT_TEMPLATE = data.get("PROMPT_TEMPLATE", """你是一位资深的数字货币分析师兼交易员。以下是 {symbol} 永续合约最近 {lookback} 根 {rule} K 线的数据（含常用技术指标）：

        {data_text}

        此外，当前市场状况补充信息如下：
        {market_depth}
        {open_interest}
        {funding_rate_info}

        请根据以上所有信息，给出具体的交易建议，包括方向、入场价、止损价、第一目标位、第二目标位以及信号强度。请严格以 JSON 格式输出，不要包含任何其他文字说明。JSON格式如下：
        {{
          "direction": "long" 或 "short" 或 "neutral",
          "entry": 价格数值,
          "stop_loss": 价格数值,
          "take_profit1": 第一目标价,
          "take_profit2": 第二目标价,
          "strength": "strong" 或 "medium" 或 "weak"
        }}
        如果无法给出明确建议，direction 设为 "neutral"，其他字段可为 null。
        """)