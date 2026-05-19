/**
 * 多数据源同步相关API
 */
import { ApiClient } from './request'

// 数据源状态接口
export interface DataSourceStatus {
  name: string
  priority: number
  available: boolean
  description: string
  token_source?: 'database' | 'env'  // Token 来源（仅 Tushare）
}

// 同步状态接口
export interface SyncStatus {
  job: string
  status: 'idle' | 'running' | 'success' | 'success_with_errors' | 'failed' | 'never_run'
  started_at?: string
  finished_at?: string
  total: number
  inserted: number
  updated: number
  errors: number
  last_trade_date?: string
  data_sources_used: string[]
  source_stats?: Record<string, Record<string, number>>
  message?: string
}

// 同步请求参数
export interface SyncRequest {
  force?: boolean
  preferred_sources?: string[]
}

// API响应格式
export interface ApiResponse<T = any> {
  success: boolean
  message: string
  data: T
}

// 基础测试结果接口
export interface BaseTestResult {
  success: boolean
  message: string
  count?: number
  date?: string
}

// 测试结果接口（简化版）
export interface DataSourceTestResult {
  name: string
  priority: number
  available: boolean
  message: string
  token_source?: 'database' | 'env'  // Token 来源（仅 Tushare）
}

// 使用建议接口
export interface SyncRecommendations {
  primary_source?: {
    name: string
    priority: number
    reason: string
  }
  fallback_sources: Array<{
    name: string
    priority: number
  }>
  suggestions: string[]
  warnings: string[]
}

export interface AkshareDatabaseStatus {
  basic_info: {
    total_count: number
    extended_count: number
    coverage_rate: number
    latest_update?: string
  }
  market_quotes: {
    total_count: number
    latest_update?: string
  }
  historical_daily: {
    total_count: number
    symbol_count: number
    earliest_trade_date?: string
    latest_trade_date?: string
    latest_update?: string
  }
  historical_by_period: Array<{
    period?: string
    total_count: number
    symbol_count: number
    earliest_trade_date?: string
    latest_trade_date?: string
  }>
  data_quality: string
  check_time: string
}

export interface AkshareInitializationStatus {
  is_running: boolean
  current_task?: string
  start_time?: string
  progress?: {
    current_step?: string
    completed_steps?: number
    total_steps?: number
  }
  result?: any
  duration: number
}

export interface StockSyncCoveragePeriod {
  count: number
  earliest_trade_date?: string
  latest_trade_date?: string
  latest_update?: string
  sources: string[]
}

export interface StockSyncCoverageItem {
  code: string
  name?: string
  industry?: string
  market?: string
  basic: {
    exists: boolean
    source?: string
    latest_update?: string
  }
  historical: Record<string, StockSyncCoveragePeriod>
  financial: {
    count: number
    latest_report_period?: string
    latest_update?: string
    sources: string[]
  }
  quotes: {
    exists: boolean
    trade_date?: string
    latest_update?: string
    source?: string
  }
}

export interface StockSyncCoverageSummary {
  basic_stock_count: number
  historical_by_period: Array<{
    period?: string
    symbol_count: number
    record_count: number
    earliest_trade_date?: string
    latest_trade_date?: string
  }>
  financial_stock_count: number
  quote_stock_count: number
}

export interface StockSyncCoverageResponse {
  items: StockSyncCoverageItem[]
  total: number
  page: number
  page_size: number
  has_more: boolean
  summary: StockSyncCoverageSummary
}

/**
 * 获取数据源状态
 */
export const getDataSourcesStatus = (): Promise<ApiResponse<DataSourceStatus[]>> => {
  return ApiClient.get('/api/sync/multi-source/sources/status')
}

/**
 * 获取当前正在使用的数据源
 */
export const getCurrentDataSource = (): Promise<ApiResponse<{
  name: string
  priority: number
  description: string
  token_source?: 'database' | 'env'
  token_source_display?: string
}>> => {
  return ApiClient.get('/api/sync/multi-source/sources/current')
}

/**
 * 获取同步状态
 */
export const getSyncStatus = (): Promise<ApiResponse<SyncStatus>> => {
  return ApiClient.get('/api/sync/multi-source/status')
}

/**
 * 运行股票基础信息同步
 */
export const runStockBasicsSync = (params?: {
  force?: boolean
  preferred_sources?: string
}): Promise<ApiResponse<SyncStatus>> => {
  const queryParams = new URLSearchParams()
  if (params?.force) {
    queryParams.append('force', 'true')
  }
  if (params?.preferred_sources) {
    queryParams.append('preferred_sources', params.preferred_sources)
  }

  const url = `/api/sync/multi-source/stock_basics/run${queryParams.toString() ? '?' + queryParams.toString() : ''}`
  return ApiClient.post(url, undefined, {
    timeout: 600000 // 同步操作需要更长时间，设置为10分钟
  })
}

/**
 * 测试数据源连接
 * @param sourceName - 可选，指定要测试的数据源名称。如果不指定，则测试所有数据源
 */
export const testDataSources = (sourceName?: string): Promise<ApiResponse<{ test_results: DataSourceTestResult[] }>> => {
  const params = sourceName ? { source_name: sourceName } : {}
  return ApiClient.post('/api/sync/multi-source/test-sources', params, {
    timeout: 15000 // 单个数据源测试超时15秒，多个数据源最多30秒
  })
}

/**
 * 获取同步建议
 */
export const getSyncRecommendations = (): Promise<ApiResponse<SyncRecommendations>> => {
  return ApiClient.get('/api/sync/multi-source/recommendations')
}

/**
 * 获取 AKShare / 策略数据状态，包含日线条数与日期范围
 */
export const getAkshareDatabaseStatus = (): Promise<ApiResponse<AkshareDatabaseStatus>> => {
  return ApiClient.get('/api/akshare-init/status')
}

/**
 * 获取 AKShare 初始化/策略数据同步任务状态
 */
export const getAkshareInitializationStatus = (): Promise<ApiResponse<AkshareInitializationStatus>> => {
  return ApiClient.get('/api/akshare-init/initialization-status')
}

/**
 * 按指定周期同步策略工具所需数据：基础信息 + 日线
 */
export const startStrategyDataSync = (params: {
  historical_days: number
  force?: boolean
}): Promise<ApiResponse<any>> => {
  return ApiClient.post('/api/akshare-init/start-strategy-sync', params, {
    timeout: 60000
  })
}

/**
 * 获取同步历史记录
 */
export const getSyncHistory = (params?: {
  page?: number
  page_size?: number
  status?: string
}): Promise<ApiResponse<{
  records: SyncStatus[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}>> => {
  const queryParams = new URLSearchParams()
  if (params?.page) {
    queryParams.append('page', params.page.toString())
  }
  if (params?.page_size) {
    queryParams.append('page_size', params.page_size.toString())
  }
  if (params?.status) {
    queryParams.append('status', params.status)
  }

  const url = `/api/sync/multi-source/history${queryParams.toString() ? '?' + queryParams.toString() : ''}`
  return ApiClient.get(url)
}

/**
 * 获取股票同步覆盖明细
 */
export const getStockSyncCoverage = (params?: {
  page?: number
  page_size?: number
  keyword?: string
  source?: string
}): Promise<ApiResponse<StockSyncCoverageResponse>> => {
  const queryParams = new URLSearchParams()
  if (params?.page) queryParams.append('page', params.page.toString())
  if (params?.page_size) queryParams.append('page_size', params.page_size.toString())
  if (params?.keyword) queryParams.append('keyword', params.keyword)
  if (params?.source) queryParams.append('source', params.source)

  const url = `/api/sync/multi-source/stock-coverage${queryParams.toString() ? '?' + queryParams.toString() : ''}`
  return ApiClient.get(url)
}

/**
 * 清空同步缓存
 */
export const clearSyncCache = (): Promise<ApiResponse<{ cleared: boolean }>> => {
  return ApiClient.delete('/api/sync/multi-source/cache')
}

// 传统单一数据源同步API（保持兼容性）
export const runSingleSourceSync = (): Promise<ApiResponse<any>> => {
  return ApiClient.post('/api/sync/stock_basics/run')
}

export const getSingleSourceSyncStatus = (): Promise<ApiResponse<any>> => {
  return ApiClient.get('/api/sync/stock_basics/status')
}

// ── 统一同步 API ──────────────────────────────────────────

export interface SyncItem {
  key: string
  label: string
}

export interface UnifiedSyncRequest {
  sync_items: string[]
  data_sources: string[]
  mode: string
  start_date?: string
  end_date?: string
  symbol_scope: string
  symbols?: string[]
}

export interface UnifiedSyncResult {
  job_id: string
  total_steps: number
  completed_steps: number
  status: string
  results: { step: string; success: boolean; error?: string; result?: any }[]
  current_step?: string
  started_at?: string
  finished_at?: string
}

export const getSyncItems = (): Promise<ApiResponse<{ items: SyncItem[]; sources: { key: string; label: string }[] }>> => {
  return ApiClient.get('/api/sync/multi-source/unified/sync-items')
}

export const runUnifiedSync = (params: UnifiedSyncRequest): Promise<ApiResponse<{ job_id: string; config: any }>> => {
  return ApiClient.post('/api/sync/multi-source/unified/run', params, { timeout: 30000 })
}

export const getUnifiedSyncStatus = (jobId: string): Promise<ApiResponse<UnifiedSyncResult>> => {
  return ApiClient.get(`/api/sync/multi-source/unified/status/${jobId}`)
}

export const cancelUnifiedSync = (jobId: string): Promise<ApiResponse<any>> => {
  return ApiClient.post(`/api/sync/multi-source/unified/cancel/${jobId}`)
}

/** 获取当前运行中的同步任务（页面刷新后恢复进度） */
export const getRunningSyncJobs = (): Promise<ApiResponse<{ running_tasks: any[] }>> => {
  return ApiClient.get('/api/sync/multi-source/unified/running')
}

/** 停止 AKShare 初始化/策略同步任务 */
export const stopAkshareInit = (): Promise<ApiResponse<any>> => {
  return ApiClient.post('/api/akshare-init/stop')
}
