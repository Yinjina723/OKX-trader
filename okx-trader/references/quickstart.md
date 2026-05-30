# OKX 交易助手 — 快速开始

## 环境要求

- Python 3.10+
- Windows / macOS / Linux
- OKX API Key（需开启交易权限）
- DeepSeek API Key（用于 AI 分析）

## 安装

```bash
# 1. 克隆或下载项目到本地
cd <你的项目目录>

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 API Key
# 编辑 config.json，填入:
#   API_KEY, SECRET_KEY, PASSPHRASE
#   AI_API_KEY (DeepSeek)
#   SYMBOLS (要监控的交易对)
```

## 使用方式

### 方式 1: 命令行主程序

```bash
# 生成信号并进入监控循环
python main.py

# 回测模式
# 在 config.json 中设置 BACKTEST_ENABLED = true
python main.py
```

### 方式 2: Web 面板

```bash
# 启动 Web 面板（默认 http://localhost:5000）
python web_app.py
```

打开浏览器访问 `http://localhost:5000`，可查看：
- 暗色主题仪表盘
- 实时 K 线图（含插针事件标记）
- 一键生成信号
- 在线回测
- 配置修改

### 方式 3: 快捷脚本（Skill 附带）

```bash
# 生成 AI-USDT-SWAP 的交易信号
python scripts/okx_signal.py AI-USDT-SWAP

# 查询最新信号状态
python scripts/okx_status.py

# 运行回测
python scripts/okx_backtest.py AI-USDT-SWAP
```

## 配置要点

1. **SYMBOLS**: 必须是 OKX SWAP 合约格式，如 `AI-USDT-SWAP`
2. **TARGET_TIMEFRAME**: 支持 `1m/3m/5m/15m/30m/1H/4H/6H/12H/1D`
3. **MANIPULATION_ENABLED**: 是否开启庄家博弈检测（推荐开启）
4. **AUTO_TRADE_ENABLED**: 是否自动下单（风险自负）
5. **AI_ECO_MODE**: AI 精简模式（减少 token 消耗，推荐开启）

## 常见问题

**Q: 连接 OKX API 失败？**
A: 检查代理设置 `PROXY_URL`，国内需配置代理

**Q: 信号为空？**
A: 检查 `SYMBOLS` 格式是否正确（必须是 `-SWAP` 结尾），确认 API Key 有读取权限

**Q: AI 分析报错？**
A: 确认 `AI_API_KEY` 正确，检查 DeepSeek 余额

**Q: 如何添加新的分析维度？**
A: 参考 `references/manipulation_engine.md` 和 `references/pipeline_stages.md`
