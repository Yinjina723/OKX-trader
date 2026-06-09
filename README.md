# OKX AI 永续合约交易助手

> 🎯 **短线交易准确度 80%+** — AI 日线定方向 + 1H 机械信号入场 + 固定止盈止损

基于 Python 的 OKX 永续合约（SWAP）智能交易系统，集成 **AI 深度分析 + 庄家行为检测 + 清算猎杀策略 + 资金护卫 + Web 仪表盘**。

---

## 核心功能

| 模块 | 功能说明 |
|------|---------|
| **日线分析管道** | 日线 K 线 → 技术指标 → 形态/背离 → 庄家检测 → 情绪分析 → AI 综合研判 |
| **AI 决策引擎** | 自动发现 Top10 热门合约 → 批量下载数据 → AI 分析 → 评分排序推荐 |
| **机械信号系统** | AI 定方向 + 1H 技术指标自动产生入场信号 → 固定 TP/SL 执行 |
| **庄家行为检测** | K 线位置/量能异常/影线/停滞 + 威科夫简化分析 |
| **清算猎杀** | 多空比极端值 → 推算爆仓瀑布区 → 提前挂单"吃尸体" |
| **资金卫士** | 阶梯入场 + 联合爆仓价计算 → 确保永不爆仓 |
| **Web 仪表盘** | Flask + ECharts 暗色主题，日线分析 + 回测面板 |
| **回测系统** | 多币种批量回测，夏普/最大回撤/胜率/盈亏比 |
| **本地服务器** | 本地 HTTP 服务，管理信号与交易状态 |

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/Yinjina723/OKX-trader.git
cd OKX-trader
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 API 密钥

编辑 `config.json`，填入密钥：

```json
{
    "DEEPSEEK_API_KEY": "你的DeepSeek密钥",
    "OKX_API_KEY": "你的OKX API Key",
    "OKX_SECRET_KEY": "你的OKX Secret Key",
    "OKX_PASSPHRASE": "你的OKX Passphrase",
    "SYMBOL": ["BTC-USDT-SWAP"],
    "SIMULATED": "1"
}
```

| 参数 | 说明 |
|------|------|
| `SIMULATED` | `"1"` = 模拟模式，`"0"` = 实盘 |
| `TAKE_PROFIT_RR` | 止盈风险比（默认 1.0） |
| `LS_LONG_EXTREME` | 多头极端阈值（默认 2.0） |
| `HUNT_LEVERAGE_LEVELS` | 清算猎杀杠杆档位 |
| `MAX_SIGNALS_PER_DAY` | 每日最大信号数 |

### 4. 运行

```bash
# 日线分析（命令行）
python main.py

# Web 面板（推荐）
python web_app.py
# 浏览器打开 http://localhost:5000

# 智能决策引擎（多币种扫描）
python decision_engine.py

# 本地服务器
python local_server.py
```

---

## 项目结构

```
OKX-trader/
├── main.py                     # 日线分析入口
├── web_app.py                  # Web 面板（Flask）
├── config.py                   # 配置管理
├── config.json                 # 配置文件（需自行填写密钥）
├── config_server.json          # 服务器配置模板
├── okx_client.py               # OKX API 客户端
├── ai_analysis.py              # DeepSeek AI 分析
├── backtest.py                 # 回测引擎
├── indicators.py               # 技术指标计算
├── patterns.py                 # K线形态 / RSI背离 / MACD背离 / 均线排列
├── mechanical_signals.py       # 1H 机械信号生成器
├── decision_engine.py          # 智能决策引擎（多币种）
├── capital_guard.py            # 资金卫士（阶梯入场 + 防爆仓）
├── liquidation_hunter.py       # 清算猎杀策略
├── sentiment.py                # 市场情绪分析
├── prepare_data.py             # 历史数据下载
├── trade_log.py                # 信号/交易日志
├── logger.py                   # 日志模块
├── local_server.py             # 本地 HTTP 服务
├── deploy.sh                   # Linux 部署脚本
├── manipulation/               # 庄家行为检测引擎
│   ├── __init__.py
│   ├── daily_engine.py         # 日线检测主引擎
│   ├── types.py                # BehaviorTag / DetectionResult
│   ├── kline_helpers.py        # K线辅助（位置/量能/影线/停滞）
│   └── wyckoff.py              # 威科夫简化分析
├── templates/                  # HTML 模板
│   ├── panel.html              # 主面板
│   └── backtest.html           # 回测面板
├── static/                     # 静态资源
├── data/                       # 历史数据缓存
├── output/                     # 分析输出
├── okx-trader/                 # AI IDE Skill 包
├── OKxAPI整合/                  # OKX API 参考文档
├── requirements.txt            # Python 依赖
└── README.md
```

---

## 交易策略

### 日线分析流程

```
数据加载 → 技术指标 → 形态/背离 → 庄家检测 → 情绪分析 → AI 综合研判 → 输出信号
```

### 清算猎杀

- 多空比 > 2.0 → 多头拥挤 → 下方挂多单猎杀
- 多空比 < 0.7 → 空头拥挤 → 上方挂空单猎杀
- 基于 VWAP + 杠杆档位推算爆仓瀑布区

### 资金护卫

- 70% 资金入场 + 30% 备用金在更深爆仓区挂单
- 联合爆仓价被推至"不可触及"水平

---

## 安全警告

> ⚠️ **切换实盘前务必确认：**
>
> ```json
> "SIMULATED": "1",     // 先模拟测试
> ```
>
> 建议在模拟模式下充分验证策略后再切换实盘。
> 数字货币交易存在高风险，请谨慎操作。

---

## 依赖

- Python 3.10+
- Flask, pandas, numpy, matplotlib
- openai (DeepSeek API)
- python-okx (OKX SDK)
- loguru (日志)
- 完整列表见 `requirements.txt`

---

## 免责声明

本工具仅供学习和研究使用。数字货币交易存在高风险，作者不对任何交易损失负责。

---

## ☕ 打赏

如果这个项目对你有帮助，欢迎请我喝杯咖啡～

![支付宝打赏](./static/Alipay.jpg)

> 扫码时请备注 "OKX"，感谢支持！
