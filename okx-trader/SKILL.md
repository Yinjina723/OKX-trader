---
name: okx-trader
description: >
  OKX 永续合约智能交易助手。当用户提到 OKX、交易信号、庄家分析、插针检测、回测、
  网格策略、永续合约、API 交易、Web 面板、多周期共振、操盘检测、威科夫、DeepSeek AI、
  资金费率、OI 持仓流向时触发。支持信号生成、7维庄家检测、Web 仪表盘、回测等操作。
---

# OKX 交易助手 — CodeBuddy Skill

## 项目概述

你正在协助一个基于 Python 的 OKX 永续合约（SWAP）智能交易助手项目。

**项目位置**: `<你的项目目录>`
**入口文件**: `main.py` (命令行) / `web_app.py` (Web面板)
**配置文件**: `config.json`

## 核心能力

1. **5 阶段流水线**: 数据加载 → 辅助拉取 → 指标提取 → 并行分析 → 后处理
2. **7 维庄家博弈检测引擎**: 精英背离、OI流向、Taker聪明钱、资金费率、基差、散户拥挤度等
3. **AI 集成**: DeepSeek API 多模型共识分析
4. **自动交易**: 信号驱动下单 + 止盈止损 + 网格策略
5. **Web 面板**: Flask + ECharts 暗色主题仪表盘 (`http://localhost:5000`)
6. **回测系统**: Portfolio 模拟 + 夏普/回撤指标

## 知识库加载

处理任何与 OKX 交易助手相关的问题时，你必须先加载以下参考文档：

- `references/architecture.md` — 项目架构与数据流
- `references/pipeline_stages.md` — 5 阶段流水线详解
- `references/manipulation_engine.md` — 庄家博弈检测引擎
- `references/config_reference.md` — config.json 全部配置项
- `references/module_api.md` — 各模块核心函数签名
- `references/behavior_tags.md` — BehaviorTag 枚举与检测器
- `references/quickstart.md` — 快速开始与常见问题

## 快捷脚本（通过 exec 工具调用）

所有脚本位于 `scripts/` 目录，需在项目根目录运行：

| 命令 | 功能 |
|------|------|
| `python scripts/okx_signal.py <SYMBOL>` | 生成指定交易对信号 |
| `python scripts/okx_web.py [host] [port]` | 启动 Web 面板 |
| `python scripts/okx_backtest.py <SYMBOL>` | 运行回测 |
| `python scripts/okx_status.py [SYMBOL]` | 查询最新信号状态 |
| `python scripts/okx_install.py` | 环境检测+依赖安装 |

## 常用操作指南

### 生成信号
修改 `config.json` 中的 `SYMBOLS`，然后运行：
```bash
python scripts/okx_signal.py AI-USDT-SWAP
```

### 修改检测参数
- 插针阈值: `config.json` → `WICK_SHADOW_RATIO` (默认 3.0)
- 7维权重: `config.json` → `MANIPULATION_WEIGHTS`
- 分析开关: `MANIPULATION_ENABLED`, `TECHNICAL_BATCH_ENABLED` 等

### 添加新的 BehaviorTag
1. 在 `detectors.py` 的 `BehaviorTag` 枚举中添加新标签
2. 在对应检测器中写触发逻辑
3. 在 `manipulation/synthesis.py` 的 `determine_phase_and_direction()` 中处理新标签

### 新增分析维度
1. 在 `manipulation/` 下创建新维度模块
2. 在 `manipulation/engine.py` 的 `run_manipulation_analysis()` 中调用
3. 在 `synthesis.py` 的 `synthesize_direction()` 中加权合成

### 排查问题
- API 连接失败 → 检查 `config.json` 中的 `PROXY_URL`
- AI 分析报错 → 检查 `AI_API_KEY` 和 DeepSeek 余额
- 信号为空 → 检查 `SYMBOLS` 格式 (必须是 `*-SWAP`)

## 关键架构规则

1. **分层清晰**: 分析层（manipulation/）/策略层（technical_analysis.py, grid_manager.py）/数据层（okx_client.py）互不耦合
2. **配置驱动**: 所有参数在 `config.json` 中，通过 `Config` 类访问
3. **管道化**: 5个 stage 独立可测，修改某个 stage 不影响其他
4. **线程池复用**: data=8, analysis=4 的模块级线程池，不要在每个请求中重建
5. **信号写入路径**: `config.OUTPUT_DIR`（默认 `./output`）
