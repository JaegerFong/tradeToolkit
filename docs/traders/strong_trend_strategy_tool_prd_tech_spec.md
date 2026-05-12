# 强趋势策略工具需求与技术实现方案

## 1. 目标

建设「结构化策略 Markdown -> 后台任务 -> 股票池管理 -> 每日复盘/次日计划 -> 信号级回测」闭环。首版以强趋势股策略为模板，基于本地 A 股日线数据执行，不自动交易，不接券商。

## 2. 首版范围

- 策略 Markdown 必须包含固定章节：每日选股过滤、强趋势形态确认、走势气质量化、产业链轮动、买点、卖点、仓位、每日复盘。
- 规则引擎使用日线收盘数据实现初筛、趋势确认、气质评分、买点、卖点、止损和股票池状态更新。
- LLM 首版不参与入选/剔除裁判；系统使用确定性模板生成复盘与次日计划，后续可在同一服务中替换为证据约束的 LLM 文案。
- 板块、产业链、龙虎榜、绝对龙头识别为可选增强数据，缺失时记录 `missing_evidence`，不阻塞运行。
- 回测为信号级回测：统计买点信号的未来 N 日收益、胜率、平均收益和按信号类型拆分表现。

## 3. 后端接口

统一前缀：`/api/strategies`

- `POST /api/strategies`：创建策略，保存 Markdown、解析配置、版本与启停状态。
- `GET /api/strategies`：查询策略列表。
- `GET /api/strategies/{strategy_id}`：查询策略详情。
- `PUT /api/strategies/{strategy_id}`：更新策略，Markdown 变更时版本号加一。
- `POST /api/strategies/{strategy_id}/validate`：重新解析并返回校验结果。
- `POST /api/strategies/{strategy_id}/runs`：手动触发每日筛选/复盘。
- `GET /api/strategies/{strategy_id}/runs/{run_id}`：查询运行状态、复盘和次日计划。
- `GET /api/strategies/{strategy_id}/runs/{run_id}/events`：查询运行时间线。
- `GET /api/strategies/{strategy_id}/runs/{run_id}/results`：查询入选结果。
- `GET /api/strategies/{strategy_id}/pool`：查询策略独立股票池。
- `POST /api/strategies/{strategy_id}/backtests`：创建信号级回测。
- `GET /api/strategies/{strategy_id}/backtests/{backtest_id}`：查询回测状态和指标。

## 4. 数据模型

- `strategy_definitions`：策略定义、Markdown 原文、解析结果、版本、启停状态、调度配置。
- `strategy_runs`：单次运行状态、进度、统计、每日复盘和次日计划。
- `strategy_run_events`：运行过程事件。
- `strategy_run_results`：单次运行的股票结果、评分、信号、止损、失效条件和证据。
- `strategy_stock_pool`：策略维度股票池，记录状态、入池原因、跟踪天数、最新评分和证据。
- `strategy_backtests`：回测任务、参数、进度、指标。
- `strategy_backtest_results`：逐条历史信号及未来收益。

## 5. 执行逻辑

每日任务流程：

1. 加载启用策略与解析后的结构化配置。
2. 从 `stock_basic_info` 加载 A 股股票池。
3. 从 `stock_daily_quotes` 读取日线数据。
4. 执行初筛：5日涨幅、近5日涨停次数、收盘在 MA5 上、成交量大于10日均量、上市天数。
5. 执行趋势确认：连续3日收盘不破 MA5、MA5/MA10 贴合、远离 MA20、MA5 > MA10 > MA20。
6. 执行气质评分：断板不跌、反包、高位横盘、历史新高、强势横盘；板块强弱首版作为可选缺失证据。
7. 执行买点检测：首阳突破、首阴、均线回踩、第一次跌停日线近似。
8. 执行卖点检测：连续滞涨、假突破、跌破 MA10 等。
9. 计算综合评分并更新策略股票池。
10. 生成每日复盘和次日计划。

综合评分首版为：趋势 40%、气质 25%、买点 20%、可选增强 15%。缺少可选增强时按已知证据归一化，并在结果中显示缺失证据。

## 6. 调度

系统启动时注册 `strong_trend_strategy_daily_runs`，默认 `40 18 * * 1-5` 执行。任务会扫描所有 `enabled` 策略并为每个策略创建一次 scheduled run。若同策略已有 queued/running 任务，跳过新任务。

## 7. 回测

回测按交易日重放规则引擎，只使用当前回放日期及之前的数据产生信号。买点信号产生后，统计未来 `holding_days` 日收益；默认 3 日。首版输出：

- 总信号数
- 胜率：未来收益大于 2% 视为成功
- 平均收益
- 最大有利收益
- 最大不利收益
- 按买点类型统计 count、win_rate、avg_return

## 8. 验收标准

- 完整强趋势 Markdown 能解析为结构化配置，并提示分钟级/龙虎榜等降级项。
- 缺少必填章节时返回 `invalid` 和缺失章节列表。
- 规则引擎能对构造日线数据识别强趋势、气质信号和买点。
- API 能完成：创建策略、校验、运行、查询结果、查询股票池、发起回测。
- 每个结果都包含 `missing_evidence` 和“研究与教育用途”的计划/复盘口径，不构成投资建议。
