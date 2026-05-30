# OKX AI 永续合约交易助手

基于 Python 的 OKX 永续合约（SWAP）智能交易系统，集成 **AI 分析 + 7 维庄家博弈检测 + 多周期共振 + Web 仪表盘**。

## 核心能力

| 功能 | 说明 |
|------|------|
| **5 阶段信号流水线** | 数据加载 → 辅助拉取 → 指标提取 → 并行分析 → 后处理 |
| **7 维庄家检测引擎** | 精英背离、OI 流向、Taker 聪明钱、资金费率、基差、散户拥挤度、威科夫分析 |
| **AI 深度分析** | DeepSeek 多模型共识 + 节能模式 |
| **自动交易** | 信号驱动下单 + 止盈止损 + 加仓策略 |
| **Web 仪表盘** | Flask + ECharts 暗色主题面板，实时 K 线 + 庄家事件标记 |
| **回测系统** | Portfolio 模拟 + 夏普/回撤/胜率 |
| **网格策略** | 等差/等比网格，ATR 动态区间 |
| **AI IDE Skill 包** | 一键安装到 CodeBuddy / OpenClaw / Cursor / Windsurf / Claude Code |

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/你的用户名/okx-trader.git
cd okx-trader
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 API 密钥

```bash
# 复制配置模板
copy config.example.json config.json   # Windows
# cp config.example.json config.json   # macOS/Linux

# 编辑 config.json，填入你自己的密钥：
#   - "DEEPSEEK_API_KEY": DeepSeek API Key
#   - "OKX_API_KEY":     OKX API Key（需交易权限）
#   - "OKX_SECRET_KEY":  OKX Secret Key
#   - "OKX_PASSPHRASE":  OKX Passphrase
#   - "SYMBOL":          交易对，如 ["BTC-USDT-SWAP"]
```

### 4. 运行

```bash
# 方式 1: 命令行主程序（循环监控）
python main.py

# 方式 2: Web 面板（推荐新手）
python web_app.py
# 浏览器打开 http://localhost:5000

# 方式 3: 快捷脚本
python scripts/okx_signal.py BTC-USDT-SWAP   # 生成信号
python scripts/okx_status.py                 # 查询状态
python scripts/okx_backtest.py BTC-USDT-SWAP # 回测
```

## 安装 AI IDE Skill（可选）

如果你使用 AI IDE，可一键安装 skill 包：

```bash
bash okx-trader/install.sh codebuddy    # CodeBuddy
bash okx-trader/install.sh openclaw     # OpenClaw
bash okx-trader/install.sh cursor       # Cursor
bash okx-trader/install.sh windsurf     # Windsurf
bash okx-trader/install.sh claude       # Claude Code
```

## 项目结构

```
okx-trader/
├── main.py                    # 命令行入口（循环监控）
├── web_app.py                 # Web 面板入口
├── config.py                  # 配置类（从 config.json 读取）
├── config.example.json        # 配置模板（复制为 config.json）
├── okx_client.py              # OKX API 客户端
├── ai_analysis.py             # DeepSeek AI 分析
├── backtest.py                # 回测引擎
├── data_collector.py          # 数据采集
├── data_cache.py              # 数据缓存
├── data_utils.py              # 数据工具
├── detectors.py               # 行为检测器 + BehaviorTag 枚举
├── detector_config.py         # 检测器配置
├── grid_manager.py            # 网格交易管理
├── intent_engine.py           # 意图引擎
├── logger.py                  # 日志模块
├── monitor_app.py             # 监控应用
├── multi_tf_analysis.py       # 多周期分析
├── order_utils.py             # 订单工具
├── generate_signals_multi.py  # 多币种信号生成
├── manipulation/              # 庄家检测引擎（7 维）
│   ├── engine.py              # 引擎入口
│   ├── elite.py               # 精英背离检测
│   ├── oi_flow.py             # OI 资金流向
│   ├── taker.py               # Taker 聪明钱
│   ├── funding.py             # 资金费率分析
│   ├── basis.py               # 基差分析
│   ├── crowd.py               # 散户拥挤度
│   ├── wyckoff.py             # 威科夫分析
│   ├── synthesis.py           # 多维度合成
│   ├── predict.py             # 预测模块
│   └── kline_helpers.py       # K 线辅助
├── templates/                 # Flask HTML 模板
├── static/                    # 静态资源（CSS/JS）
├── requirements.txt           # Python 依赖
└── okx-trader/                # AI IDE Skill 包
    ├── SKILL.md               # Skill 入口
    ├── install.sh             # 安装脚本
    ├── references/            # 知识库文档
    ├── scripts/               # 快捷脚本
    ├── openclaw/              # OpenClaw 适配
    ├── cursor/                # Cursor 规则
    ├── windsurf/              # Windsurf 规则
    └── claude/                # Claude Code 规则
```

## 安全警告

> ⚠️ **永远不要将 `config.json` 提交到 Git！** 该文件已被 `.gitignore` 排除。
> 使用 `config.example.json` 作为模板，填写你自己的 API 密钥。

## 依赖

- Python 3.10+
- 主要依赖：Flask, pandas, numpy, matplotlib, openai, python-okx, loguru
- 完整列表见 `requirements.txt`

## 免责声明

本工具仅供学习和研究使用。数字货币交易存在高风险，请谨慎操作。作者不对任何交易损失负责。
