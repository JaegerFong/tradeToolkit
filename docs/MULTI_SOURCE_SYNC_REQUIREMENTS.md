# 多数据源同步模块 — 细化同步功能需求与技术实现文档

## 1. 现状分析

### 1.1 当前可同步的数据类型

| 数据类型 | 数据源 | MongoDB 集合 | 后端服务方法 |
|---------|--------|-------------|-------------|
| **基础信息** | Tushare / AKShare | `stock_basic_info` | `AKShareSyncService.sync_stock_basic_info` / `TushareSyncService.sync_stock_basic_info` |
| **实时行情** | Tushare / AKShare | `market_quotes` | `sync_realtime_quotes`（Tushare 对 ≤10 只股票自动切换 AKShare） |
| **历史 K 线** | Tushare / AKShare | `stock_daily_quotes` | `sync_historical_data`（支持 daily/weekly/monthly） |
| **财务数据** | Tushare / AKShare | `stock_financial_data` | `sync_financial_data`（支持 quarterly/annual） |
| **新闻数据** | Tushare / AKShare | `stock_news` | `sync_news_data`（支持自选股/全市场、按小时回溯） |
| **行情入库** | 轮转（Tushare/AKShare） | `market_quotes` | `QuotesIngestionService.run_once`（交易时段持续轮询） |

### 1.2 当前 API 端点能力

| 端点 | 方法 | 能力 | 缺失 |
|------|------|------|------|
| `/api/sync/multi-source/stock_basics/run` | POST | 仅基础信息全量同步 | 无日期、无符号筛选、无数据类型选择 |
| `/api/akshare-init/start-strategy-sync` | POST | 基础信息+日线，可选历史天数 | 仅 AKShare，无法选数据源、无法选周期 |
| `/api/akshare-init/start-full` | POST | 完整初始化（6步） | 无法指定数据类型子集 |
| `/api/financial-data/sync/start` | POST | 财务数据批量同步 | 独立的财务路由，不在统一同步页面 |
| `/api/financial-data/sync/single` | POST | 单只股票财务同步 | 同上 |

### 1.3 定时调度（APScheduler）

| 任务 | Cron | 数据源 | 参数 |
|------|------|--------|------|
| 基础信息同步 | 每日 06:30 | 多源 | `force=False` |
| 行情同步 | `*/30 9-15 * * 1-5` | AKShare | `force=False`，跳过非交易日 |
| 历史数据同步 | `0 17 * * 1-5` | Tushare/AKShare | `incremental=True`，跳过非交易日 |
| 财务数据同步 | `0 4 * * 0` | Tushare/AKShare | 无参数 |
| 新闻同步 | `0 */2 * * *` | AKShare | `favorites_only=True` |
| 状态检查 | 每小时 | Tushare/AKShare | 无参数 |

### 1.4 前端当前状态

多数据源同步页面（`/settings/sync`）目前混合了两个独立的同步子系统：

- **SyncControl** → 仅触发"股票基础信息同步"，参数只有 `force` 和 `preferred_sources`
- **StrategyDataSync** → 仅触发 AKShare 的"策略数据同步"（基础信息+日线），参数只有 `historical_days` 和 `force`

两个子系统互不知晓，完成时无联动刷新。

---

## 2. 需求目标

将多数据源同步页面改造为一个**统一的、细粒度的数据同步管理中心**，支持：

1. **按数据类型选择**同步目标
2. **按数据源选择**（Tushare / AKShare）
3. **按时间范围选择**（增量 / 全量 / 指定日期区间）
4. **按股票范围选择**（全市场 / 指定代码 / 仅自选股）
5. **统一的同步进度与历史**展示
6. **手动触发 + 定时调度**管理

---

## 3. 功能需求明细

### 3.1 统一同步触发面板

| 功能 | 说明 |
|------|------|
| **数据类型多选** | 复选框：基础信息 / 实时行情 / 历史日线 / 历史周线 / 历史月线 / 财务数据 / 新闻数据 |
| **数据源选择** | 下拉多选：Tushare / AKShare，默认两者都选，自动展示可用状态 |
| **同步模式** | 单选：增量同步（从上次中断处继续）/ 全量同步（全部重新拉取）/ 指定日期区间 |
| **日期区间选择器** | 当选择"指定日期区间"时出现，开始日期 + 结束日期，默认近 1 年 |
| **股票范围** | 全市场 / 指定代码（textarea 输入，逗号/换行分隔）/ 仅自选股 |
| **高级选项（折叠）** | 批次大小 / 请求间隔 / 重试次数 / 超时时间（有默认值，高级用户可调） |
| **执行按钮** | "开始同步"，运行时变为进度条 + 取消按钮 |

### 3.2 同步进度面板

| 功能 | 说明 |
|------|------|
| **总体进度条** | 百分比 + 当前步骤描述（如"步骤 2/5：同步历史日线 — 处理 300/5000 只股票"） |
| **分步进度** | 每类数据一个子进度条（基础信息 / 行情 / 日线 / 周线 / 月线 / 财务 / 新闻） |
| **实时统计** | 已处理 / 成功 / 失败 / 跳过 计数，实时更新 |
| **预计剩余时间** | 基于已完成批次的平均速度估算 |
| **取消按钮** | 向 `scheduler_executions` 设置 `cancel_requested=true` |

### 3.3 同步历史面板（增强）

| 功能 | 说明 |
|------|------|
| **筛选器** | 按数据类型 / 数据源 / 状态（成功/失败/运行中）/ 日期范围 |
| **列表增强** | 每条记录展示：触发时间、数据类型、数据源、股票数、成功/失败数、耗时、错误摘要 |
| **重试按钮** | 失败的任务可一键重试（使用相同参数） |
| **详情展开** | 展开查看每只失败股票的详细错误信息 |

### 3.4 数据覆盖面板（增强）

| 功能 | 说明 |
|------|------|
| **筛选增强** | 按交易所板块（主板/创业板/科创板）、按缺失数据类型（未同步日线/未同步财务等） |
| **批量操作** | 勾选多只股票 → "补同步选中股票"（弹出确认对话框，选择要补同步的数据类型） |
| **导出** | 导出当前筛选结果为 CSV/Excel |

### 3.5 定时调度管理

| 功能 | 说明 |
|------|------|
| **快速配置** | 为每种数据类型提供快速 cron 预设（每日/每周/交易时段） |
| **独立开关** | 每种数据类型 × 数据源的定时同步独立启用/禁用 |
| **下次执行时间** | 展示每个定时任务的下次触发时间 |

---

## 4. 技术实现方案

### 4.1 新增统一同步 API 端点

新增路由 `POST /api/sync/unified/run`：

```python
class UnifiedSyncRequest(BaseModel):
    # 数据类型
    sync_items: List[str] = Field(
        default=["basic_info", "historical"],
        description="basic_info, quotes, historical, weekly, monthly, financial, news"
    )
    # 数据源
    data_sources: List[str] = Field(
        default=["tushare", "akshare"]
    )
    # 同步模式
    mode: str = Field(default="incremental", description="incremental / full / date_range")
    start_date: Optional[str] = Field(default=None, description="YYYY-MM-DD, mode=date_range 时必填")
    end_date: Optional[str] = Field(default=None, description="YYYY-MM-DD，默认今天")
    # 股票范围
    symbol_scope: str = Field(default="all", description="all / custom / favorites")
    symbols: Optional[List[str]] = Field(default=None, description="symbol_scope=custom 时必填")
    # 高级选项
    batch_size: int = Field(default=100)
    rate_limit_delay: float = Field(default=0.5)
    max_retries: int = Field(default=3)
```

**响应（立即返回，后台执行）：**
```json
{
  "success": true,
  "job_id": "unified_sync_20260517_103000",
  "message": "同步任务已启动",
  "config": { ... }
}
```

### 4.2 统一同步编排器

新增 `app/services/unified_sync_service.py`，编排多数据类型×多数据源的同步流程：

```
UnifiedSyncService.run_sync(config)
  ├── 1. basic_info → TushareSyncService + AKShareSyncService
  ├── 2. quotes     → TushareSyncService + AKShareSyncService（交易时段检查）
  ├── 3. historical → TushareSyncService + AKShareSyncService（daily）
  ├── 4. weekly     → TushareSyncService + AKShareSyncService（weekly）
  ├── 5. monthly    → TushareSyncService + AKShareSyncService（monthly）
  ├── 6. financial  → TushareSyncService + AKShareSyncService
  └── 7. news       → AKShareSyncService（news）
```

每个步骤复用现有的 Worker 方法，新增对 `start_date`/`end_date`/`symbols` 参数的透传。

### 4.3 进度追踪增强

复用现有的 `scheduler_executions` 集合 + `update_job_progress()` 函数。新增：

- `total_steps`: 总共多少步骤（数据类型数 × 数据源数）
- `current_step`: 当前步骤索引
- `step_progress`: 每个步骤的进度字典 `{"basic_info": {"processed": 300, "total": 5000}}`

### 4.4 前端改造

**新增统一同步面板组件** `UnifiedSyncPanel.vue`：

```
┌─────────────────────────────────────────────────┐
│ 🎯 数据类型                                       │
│ ☑ 基础信息  ☑ 实时行情  ☑ 历史日线  ☐ 周线  ☐ 月线  │
│ ☐ 财务数据  ☐ 新闻数据                            │
├─────────────────────────────────────────────────┤
│ 📡 数据源:  [Tushare ✓] [AKShare ✓]              │
├─────────────────────────────────────────────────┤
│ ⏱ 同步模式:  ○ 增量  ● 全量  ○ 指定区间           │
│ 📅 开始日期: [2025-05-17]  结束日期: [2026-05-17] │
├─────────────────────────────────────────────────┤
│ 📊 股票范围:  ● 全市场  ○ 指定代码  ○ 仅自选股      │
├─────────────────────────────────────────────────┤
│                    [ 开始同步 ]                   │
└─────────────────────────────────────────────────┘
```

**改造 SyncControl.vue**：从仅支持 `stock_basics` 升级为支持全部数据类型。

**改造 MultiSourceSync.vue 页面布局**：

- 顶部：统一同步触发面板
- 左侧：数据源状态 + 同步进度 + 建议
- 右侧：同步历史 + 数据覆盖明细

### 4.5 关键文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `app/services/unified_sync_service.py` | **新增** | 统一同步编排器 |
| `app/routers/multi_source_sync.py` | 修改 | 新增 `POST /unified/run`、`GET /unified/status/{job_id}` |
| `app/worker/akshare_sync_service.py` | 修改 | `sync_historical_data` 增加 `job_id` 参数 |
| `app/worker/tushare_sync_service.py` | 无需改动 | 已支持所有需要的参数 |
| `frontend/src/components/Sync/UnifiedSyncPanel.vue` | **新增** | 统一同步触发面板 |
| `frontend/src/components/Sync/SyncControl.vue` | 修改 | 升级为支持全部数据类型 |
| `frontend/src/components/Sync/SyncHistory.vue` | 修改 | 增加筛选器和重试按钮 |
| `frontend/src/components/Sync/StockSyncCoverage.vue` | 修改 | 增加筛选器和批量操作 |
| `frontend/src/views/System/MultiSourceSync.vue` | 修改 | 调整页面布局 |
| `frontend/src/api/sync.ts` | 修改 | 新增 API 函数 |

---

## 5. 实现优先级

### P0（核心功能，第一期）

1. 新增 `UnifiedSyncService` 编排器
2. 新增 `POST /api/sync/unified/run` 端点
3. 新增 `UnifiedSyncPanel.vue` 前端面板（数据类型选择 + 数据源选择 + 同步模式 + 日期区间 + 股票范围）
4. 同步进度实时展示

### P1（增强功能，第二期）

5. 同步历史筛选器增强（按数据类型/数据源/状态过滤）
6. 失败任务重试
7. 数据覆盖面板批量补同步操作
8. 定时调度管理面板

### P2（优化功能，第三期）

9. 数据导出（CSV/Excel）
10. 预计剩余时间
11. 数据源优先级动态调整（UI 拖拽排序）
