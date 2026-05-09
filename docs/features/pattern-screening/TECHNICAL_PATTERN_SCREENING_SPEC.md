# 技术形态选股（老鸭头 / N 字形态）技术实现规格说明

版本：v0.1（首期）

## 1. 设计原则

- **数据来源**：只使用本地 MongoDB（`stock_daily_quotes`、`market_quotes`、`stock_basic_info` 等）
- **异步执行**：选股任务为后台任务，可取消、可查询进度、可查看过程事件
- **可解释**：过程事件 + 关键依据摘要；禁止直接输出隐藏推理链
- **结构化输出**：LLM 输出必须可解析为 JSON；解析失败要降级为规则引擎结果

## 2. 数据依赖

### 2.1 必需集合

- `stock_basic_info`：股票基础信息（代码、名称、市值等）
- `market_quotes`：最新行情快照（最新价、涨跌额/幅、成交量等）
- `stock_daily_quotes`：历史日线 OHLCV（用于形态扫描）

### 2.2 输出集合（新增）

- `pattern_screening_tasks`
- `pattern_screening_results`

可选：

- `pattern_screening_events`（如不想把过程事件塞进任务文档，可独立存储）

## 3. API 契约

统一前缀：`/api/pattern-screening`

### 3.1 创建任务

`POST /api/pattern-screening/tasks`

Request（示例）：

```json
{
  "pattern_types": ["laoyatou", "n_shape"],
  "market": "CN",
  "universe": {
    "board": ["MAIN", "STAR", "CHINEXT"],
    "min_market_cap": 5000000000,
    "industries": []
  },
  "window": {
    "end_date": "auto",
    "lookback_days": 90
  },
  "rules": {
    "min_up_pct": 0.15,
    "max_drawdown": 0.5,
    "consolidation_volume_ratio": 0.75,
    "breakout_volume_ratio": 1.3
  },
  "llm": {
    "enabled": true,
    "max_reviews": 50
  }
}
```

Response：

```json
{
  "task_id": "ps_20260509_xxx",
  "status": "queued"
}
```

### 3.2 查询任务

`GET /api/pattern-screening/tasks/{task_id}`

Response（核心字段）：

```json
{
  "task_id": "string",
  "status": "queued|running|completed|failed|cancelled",
  "created_at": "datetime",
  "started_at": "datetime|null",
  "completed_at": "datetime|null",
  "progress": {
    "percent": 0,
    "step": "init|load_universe|load_kline|calc_indicators|detect_patterns|llm_review|finalize",
    "message": "string"
  },
  "stats": {
    "total_scanned": 0,
    "candidate_count": 0,
    "selected_count": 0
  },
  "summary": "string|null",
  "error": "string|null"
}
```

### 3.3 结果列表

`GET /api/pattern-screening/tasks/{task_id}/results?limit=50&offset=0`

Response：

```json
{
  "total": 18,
  "items": [
    {
      "code": "000001.SZ",
      "name": "平安银行",
      "price": 10.25,
      "change_amount": 0.22,
      "pct_chg": 2.19,
      "market_cap": 198000000000,
      "pattern_type": "laoyatou",
      "pattern_name": "老鸭头",
      "pattern_score": 82,
      "recommendation_score": 76,
      "signal_date": "2026-05-09",
      "brief_reason": "缩量回踩后放量突破平台，MA5/MA10 重新上穿 MA20"
    }
  ]
}
```

### 3.4 结果详情

`GET /api/pattern-screening/tasks/{task_id}/results/{code}`

Response：

```json
{
  "code": "000001.SZ",
  "name": "平安银行",
  "pattern_type": "laoyatou",
  "pattern_score": 82,
  "recommendation_score": 76,
  "pattern_breakdown": {
    "neck": "2026-03-01 至 2026-03-18，上升段涨幅 24%",
    "head": "2026-03-19 至 2026-04-10，缩量整理",
    "nose": "2026-04-11 至 2026-04-20，均线重新拐头",
    "breakout": "2026-04-21 放量突破平台"
  },
  "analysis": "入选分析摘要",
  "trend_expectation": "后续走势预期",
  "buy_price_range": [10.1, 10.45],
  "position_suggestion": "试探仓 20%，突破确认后最高不超过 40%",
  "stop_loss": 9.72,
  "risk_points": ["突破量能不足", "跌破 MA20 形态失效"],
  "invalid_conditions": ["收盘跌破整理平台下沿", "连续缩量无法站稳突破位"],
  "evidence": {
    "window_end_date": "2026-05-09",
    "lookback_days": 90,
    "key_levels": {
      "platform_high": 10.3,
      "platform_low": 9.8
    }
  }
}
```

### 3.5 过程事件（推荐）

`GET /api/pattern-screening/tasks/{task_id}/events?limit=200`

返回按时间排序的事件（前端以时间线渲染）。事件结构：

```json
{
  "timestamp": "datetime",
  "step": "string",
  "title": "string",
  "message": "string",
  "progress": 72,
  "data": {
    "symbol": "000001.SZ",
    "pattern_type": "laoyatou"
  }
}
```

### 3.6 取消任务

`POST /api/pattern-screening/tasks/{task_id}/cancel`

## 4. 任务执行与进度

首期统一采用 **RedisProgressTracker**（写 `progress:{task_id}`）作为进度源；后端同时可在 `pattern_screening_tasks` 中保存最后一次进度快照用于容错。

任务步骤：

1. `init`：创建 task 文档、记录参数
2. `load_universe`：加载股票池（从 `stock_basic_info` 拉取并按条件过滤）
3. `load_kline`：批量读取 `stock_daily_quotes`（按 symbol + 日期范围）
4. `calc_indicators`：计算必要指标（MA、回撤、区间高低点、成交量均值等）
5. `detect_patterns`：规则引擎输出候选与评分
6. `llm_review`：可选，对候选做 LLM 复核并生成结构化详情
7. `finalize`：写入 `pattern_screening_results`，写任务 summary，发送通知

## 5. LLM 智能体

### 5.1 角色与约束

- 只能使用输入数据，不得编造行情
- 输出必须符合 JSON Schema
- 禁止输出隐藏推理链；只输出“依据摘要”“风险点”“失效条件”等

### 5.2 输出 JSON Schema（首期）

```json
{
  "type": "object",
  "required": [
    "code",
    "name",
    "pattern_type",
    "pattern_score",
    "recommendation_score",
    "analysis",
    "trend_expectation",
    "buy_price_range",
    "position_suggestion",
    "stop_loss",
    "risk_points",
    "invalid_conditions"
  ],
  "properties": {
    "code": { "type": "string" },
    "name": { "type": "string" },
    "pattern_type": { "type": "string", "enum": ["laoyatou", "n_shape"] },
    "pattern_score": { "type": "integer", "minimum": 0, "maximum": 100 },
    "recommendation_score": { "type": "integer", "minimum": 0, "maximum": 100 },
    "pattern_breakdown": { "type": "object" },
    "analysis": { "type": "string" },
    "trend_expectation": { "type": "string" },
    "buy_price_range": {
      "type": "array",
      "items": { "type": "number" },
      "minItems": 2,
      "maxItems": 2
    },
    "position_suggestion": { "type": "string" },
    "stop_loss": { "type": "number" },
    "risk_points": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1
    },
    "invalid_conditions": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 1
    },
    "brief_reason": { "type": "string" }
  }
}
```

## 6. 前端对接建议

- 新增 API 封装：`frontend/src/api/patternScreening.ts`
- 页面：
  - `frontend/src/views/PatternScreening/index.vue`：创建任务 + 最近任务
  - `frontend/src/views/PatternScreening/TaskDetail.vue`：过程时间线 + 结果表格 + 详情抽屉
- 结果列表点击股票：\n  - 先跳转现有股票详情 `/stocks/:code`\n  - 或在抽屉中展示“入选详情”并提供“打开股票详情”按钮

