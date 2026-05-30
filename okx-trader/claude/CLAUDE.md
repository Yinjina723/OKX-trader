# OKX 永续合约交易助手

你正在协助一个 OKX SWAP 智能交易系统的开发。

## 架构

```
项目根目录 (D:\ALL\OKX)
├── main.py              # 主入口：5阶段流水线编排
├── config.json           # 全部配置（API Key, 交易对, 权重等）
├── okx_client.py         # OKX REST API 客户端
├── manipulation/         # 庄家博弈检测 V3 引擎
│   ├── engine.py         # 主引擎 (run_manipulation_analysis)
│   ├── synthesis.py      # 7维加权合成 + 阶段判定
│   ├── crowd.py          # 散户拥挤度 (反向指标)
│   ├── elite.py          # 精英背离 + 多周期趋向
│   ├── oi_flow.py        # OI持仓流向四象限
│   ├── taker.py          # Taker聪明钱方向
│   ├── funding.py        # 资金费率极端值
│   ├── basis.py          # 多周期基差分析
│   ├── wyckoff.py        # 威科夫模式识别
│   ├── predict.py        # 下一步预测 + 综合点位
│   └── kline_helpers.py  # K线辅助(位置/量异常/插针/滞涨)
├── detectors.py          # 7大检测器 + BehaviorTag 枚举
├── ai_analysis.py        # DeepSeek AI 分析
├── backtest.py           # 回测引擎
├── grid_manager.py       # 网格策略
├── web_app.py            # Flask Web 面板
└── templates/panel.html  # 暗色仪表盘
```

## 核心规则

1. **配置驱动**: 所有参数在 config.json → Config 类
2. **管道独立**: 5个stage互不影响
3. **线程池复用**: data=8, analysis=4 模块级
4. **分析层解耦**: manipulation/ 只吃 DataFrame/Dict
5. **TTL缓存**: 300s 减少 API 调用

## 快捷命令

```
python scripts/okx_signal.py <SYMBOL>    # 生成信号
python scripts/okx_web.py                # Web 面板
python scripts/okx_backtest.py <SYMBOL>  # 回测
python scripts/okx_status.py [SYMBOL]    # 查状态
python scripts/okx_install.py            # 环境检测
```
