import { ApiClient } from './request'

export interface StrategySchedule {
  enabled: boolean
  cron: string
}

export interface StrategySummary {
  strategy_id: string
  name: string
  version: number
  status: 'draft' | 'enabled' | 'disabled'
  schedule: StrategySchedule
  validation_status: 'valid' | 'invalid'
  errors: string[]
  warnings: string[]
  created_at: string
  updated_at: string
}

export interface StrategyDetail extends StrategySummary {
  markdown: string
  config: Record<string, any>
}

export interface StrategyRun {
  run_id: string
  strategy_id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'data_incomplete'
  run_type: string
  as_of_date?: string | null
  progress: { percent: number; step: string; message: string }
  stats: Record<string, number>
  summary?: string | null
  daily_review?: string | null
  next_day_plan?: string | null
  error?: string | null
}

export interface StrategyRunResult {
  code: string
  name: string
  signal_date: string
  status: string
  total_score: number
  close: number
  entry_reason: string
  review: string
  next_day_plan: string
  stop_loss?: number | null
  missing_evidence: string[]
}

export interface StrategyBacktest {
  backtest_id: string
  strategy_id: string
  status: string
  progress: { percent: number; step: string; message: string }
  metrics: Record<string, any>
  summary?: string | null
  error?: string | null
}

const unwrap = <T>(resp: any): T => (resp && 'data' in resp ? resp.data : resp) as T

export const strategiesApi = {
  list: async () => unwrap<StrategySummary[]>(await ApiClient.get('/api/strategies')),
  get: async (strategyId: string) => unwrap<StrategyDetail>(await ApiClient.get(`/api/strategies/${strategyId}`)),
  create: async (payload: { name: string; markdown: string; enabled?: boolean; schedule?: StrategySchedule }) =>
    unwrap<StrategyDetail>(await ApiClient.post('/api/strategies', payload, { timeout: 120000 })),
  update: async (strategyId: string, payload: Partial<{ name: string; markdown: string; enabled: boolean; schedule: StrategySchedule }>) =>
    unwrap<StrategyDetail>(await ApiClient.put(`/api/strategies/${strategyId}`, payload, { timeout: 120000 })),
  validate: async (strategyId: string) => unwrap<any>(await ApiClient.post(`/api/strategies/${strategyId}/validate`, {})),
  createRun: async (strategyId: string) => unwrap<StrategyRun>(await ApiClient.post(`/api/strategies/${strategyId}/runs`, {}, { timeout: 120000 })),
  getRun: async (strategyId: string, runId: string) => unwrap<StrategyRun>(await ApiClient.get(`/api/strategies/${strategyId}/runs/${runId}`)),
  listResults: async (strategyId: string, runId: string, limit = 50, offset = 0) =>
    unwrap<{ total: number; items: StrategyRunResult[] }>(
      await ApiClient.get(`/api/strategies/${strategyId}/runs/${runId}/results?limit=${limit}&offset=${offset}`)
    ),
  listPool: async (strategyId: string) => unwrap<any[]>(await ApiClient.get(`/api/strategies/${strategyId}/pool`)),
  createBacktest: async (strategyId: string, payload: { start_date: string; end_date: string; holding_days?: number; max_symbols?: number }) =>
    unwrap<StrategyBacktest>(await ApiClient.post(`/api/strategies/${strategyId}/backtests`, payload, { timeout: 120000 })),
  getBacktest: async (strategyId: string, backtestId: string) =>
    unwrap<StrategyBacktest>(await ApiClient.get(`/api/strategies/${strategyId}/backtests/${backtestId}`))
}
