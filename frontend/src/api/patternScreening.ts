import { ApiClient } from './request'
import type { ApiResponse } from './request'

const unwrap = async <T>(promise: Promise<ApiResponse<T> | T>): Promise<T> => {
  const response = await promise
  if (response && typeof response === 'object' && 'success' in response && 'data' in response) {
    return (response as ApiResponse<T>).data
  }
  return response as T
}

export type PatternType = 'laoyatou' | 'n_shape'

export interface PatternScreeningCreateReq {
  pattern_types: PatternType[]
  market?: 'CN'
  universe?: {
    board?: string[]
    min_market_cap?: number | null
    industries?: string[]
  }
  window?: {
    end_date?: string
    lookback_days?: number
  }
  rules?: {
    min_up_pct?: number
    max_drawdown?: number
    consolidation_volume_ratio?: number
    breakout_volume_ratio?: number
  }
  llm?: {
    enabled?: boolean
    max_reviews?: number
  }
}

export interface PatternScreeningCreateResp {
  task_id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
}

export interface PatternTaskProgress {
  percent: number
  step: string
  message: string
}

export interface PatternTaskStats {
  total_scanned: number
  candidate_count: number
  selected_count: number
}

export interface PatternTaskResp {
  task_id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  created_at: string
  started_at?: string | null
  completed_at?: string | null
  progress: PatternTaskProgress
  stats: PatternTaskStats
  summary?: string | null
  error?: string | null
}

export interface PatternEvent {
  task_id: string
  timestamp: string
  step: string
  title: string
  message: string
  progress: number
  data: Record<string, any>
}

export interface PatternResultListItem {
  code: string
  name: string
  price: number
  change_amount: number
  pct_chg: number
  market_cap: number
  pattern_type: PatternType
  pattern_name: string
  pattern_score: number
  recommendation_score: number
  signal_date: string
  brief_reason: string
}

export interface PatternResultListResp {
  total: number
  items: PatternResultListItem[]
}

export interface PatternResultDetail {
  code: string
  name: string
  pattern_type: PatternType
  pattern_score: number
  recommendation_score: number
  pattern_breakdown: Record<string, string>
  analysis: string
  trend_expectation: string
  buy_price_range: [number, number]
  position_suggestion: string
  stop_loss: number
  risk_points: string[]
  invalid_conditions: string[]
  evidence: Record<string, any>
}

export const patternScreeningApi = {
  createTask: (payload: PatternScreeningCreateReq) =>
    unwrap(ApiClient.post<PatternScreeningCreateResp>('/api/pattern-screening/tasks', payload, { timeout: 120000 })),
  getTask: (taskId: string) => unwrap(ApiClient.get<PatternTaskResp>(`/api/pattern-screening/tasks/${taskId}`)),
  cancelTask: (taskId: string) => unwrap(ApiClient.post<{ success: boolean }>(`/api/pattern-screening/tasks/${taskId}/cancel`, {})),
  listEvents: (taskId: string, limit = 200) =>
    unwrap(ApiClient.get<PatternEvent[]>(`/api/pattern-screening/tasks/${taskId}/events?limit=${limit}`)),
  listResults: (taskId: string, limit = 50, offset = 0) =>
    unwrap(ApiClient.get<PatternResultListResp>(`/api/pattern-screening/tasks/${taskId}/results?limit=${limit}&offset=${offset}`)),
  getResultDetail: (taskId: string, code: string) =>
    unwrap(ApiClient.get<PatternResultDetail>(`/api/pattern-screening/tasks/${taskId}/results/${encodeURIComponent(code)}`))
}
