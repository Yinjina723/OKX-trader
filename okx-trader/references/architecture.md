# OKX 永续合约交易助手 — 项目架构

## 概述

基于 Python 的 OKX 永续合约（SWAP）智能交易助手，核心能力：
- **5 阶段流水线**：数据加载 → 辅助拉取 → 指标提取 → 并行分析 → 后处理
- **7 维庄家博弈检测引擎**：精英背离、OI 流向、Taker 聪明钱、资金费率、基差、散户拥挤度等
- **AI 集成**：DeepSeek API 多模型共识分析
- **自动交易**：信号驱动下单、止盈止损、资金费率核算
- **Web 面板**：Flask + ECharts 暗色主题实时仪表盘
- **回测系统**：Portfolio 模拟 + 夏普/回撤/收益率指标

## 目录结构

```
OKX/
├── main.py              # 主入口：5 阶段管道编排 + 主循环 + 自动交易
├── config.json          # 用户配置（API Key、交易对、周期、权重等）
├── config.py            # Config 类：加载和访问配置
├── okx_client.py        # OKX API 客户端（REST 全部接口）
├── trade_client.py      # 下单/撤单/持仓查询
├── data_utils.py        # K 线加载/合并/重采样/指标计算
├── data_cache.py        # TTL 缓存层（减少 API 调用）
├── data_collector.py    # WebSocket 实时数据采集
├── ai_analysis.py       # DeepSeek AI 分析模块
├── technical_analysis.py# 技术指标批处理
├── multi_tf_analysis.py # 多时间框架共振分析
├── grid_manager.py      # 网格策略生成器
├── backtest.py          # 回测引擎（Portfolio + 绩效指标）
├── order_utils.py       # 下单工具（合约张数换算）
├── detectors.py         # 7 大检测器（插针/量异常/夹板等）
├── detector_config.py   # 检测器参数配置
├── intent_engine.py     # 庄家意图推断
├── manipulation/        # 庄家博弈检测 V3 引擎
│   ├── engine.py        # 主引擎（run_manipulation_analysis）
│   ├── crowd.py         # 散户拥挤度
│   ├── elite.py         # 精英背离 + 多周期趋向
│   ├── oi_flow.py       # OI 持仓流向四象限
│   ├── taker.py         # Taker 买卖压力
│   ├── funding.py       # 资金费率极端值
│   ├── basis.py         # 多周期基差分析
│   ├── synthesis.py     # 7 维加权合成 + 阶段判定
│   ├── wyckoff.py       # 威科夫模式识别
│   ├── predict.py       # 下一步预测 + 综合点位
│   └── kline_helpers.py # K 线辅助（位置/量异常/插针/滞涨）
├── web_app.py           # Flask Web 面板
├── ai_analysis.py       # AI 信号生成
├── sentiment_analysis.py# 市场情绪分析（恐慌/贪婪指数）
├── monitor_app.py       # 监控应用
├── templates/           # HTML 模板
│   └── panel.html       # 暗色主题仪表盘
└── static/              # 静态资源
```

## 数据流

```
                 config.json
                      ↓
                 Config 对象
                      ↓
   ┌──────────────────────────────────────────┐
   │           main.py 管道编排                 │
   │                                          │
   │  Stage 1: _stage_load_data()              │
   │    ├── 内存缓存K线 (get_or_update_kline)   │
   │    ├── 历史数据加载 + 降级                 │
   │    └── 指标计算 (calculate_indicators)     │
   │                                          │
   │  Stage 2: _stage_fetch_aux()              │
   │    ├── 15+ 数据源并行拉取                  │
   │    ├── 模块级线程池复用 (8 workers)        │
   │    └── TTL 缓存降级 (fetch_or_cache)       │
   │                                          │
   │  Stage 3: _stage_extract_metrics()        │
   │    ├── 数值提取 (funding_rate, ls_ratio...)│
   │    ├── 情绪计算 (Fear Gauge)               │
   │    └── 多周期数据整理                      │
   │                                          │
   │  Stage 4: _stage_run_analysis()           │
   │    ├── 规则策略 (RULE_STRATEGY_ENABLED)     │
   │    ├── 技术分析 (TECHNICAL_BATCH_ENABLED)   │
   │    └── 庄家操盘 (MANIPULATION_ENABLED)      │
   │                                          │
   │  Stage 5: _stage_post_process()           │
   │    ├── AI DeepSeek 分析                   │
   │    ├── 多周期共振 (MTF_CONFLUENCE)         │
   │    ├── 操盘预测更新                        │
   │    ├── 信号标准化 + 冲突仲裁               │
   │    ├── 后处理过滤                          │
   │    └── 写入文件                            │
   └──────────────────────────────────────────┘
                      ↓
              signal dict + grid 建议
                      ↓
         save_signal_to_txt / save_signals_history
                      ↓
         try_auto_trade (可选：自动下单)
```

## 关键技术特性

| 特性 | 说明 |
|------|------|
| P1 线程池复用 | 模块级 ThreadPoolExecutor，data=8, analysis=4 |
| P1 内存缓存 | 长周期 K 线缓存，增量更新 |
| P3 管道化 | 5 阶段拆分，每阶段独立可测 |
| TTL 缓存 | 300s 默认 TTL，减少 API 调用 |
| 自适应阈值 | 波动率驱动方向判定阈值 |
| 多模型 AI | DeepSeek 多模型共识（eco 模式） |
