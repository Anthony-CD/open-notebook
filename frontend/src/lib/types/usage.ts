/**
 * Usage & cost meter types. Mirrors api/models.py UsageSummaryResponse
 * (GET /api/usage/summary). Single source of truth: usage_event rows.
 */

/** Range presets. `session` is frontend-only: it sends `from` = browser session start. */
export type UsagePeriod = 'session' | '7d' | '30d' | '365d' | 'all'

export interface UsageFilters {
  period: UsagePeriod
  model?: string
  provider?: string
  taskType?: string
}

export interface UsageBreakdownItem {
  key: string
  provider?: string | null
  event_count: number
  failed_count: number
  input_tokens: number
  output_tokens: number
  cached_input_tokens: number
  cost_known_usd: number
  known_cost_events: number
  free_events: number
  unknown_cost_events: number
}

export interface UsageSummary {
  event_count: number
  failed_count: number
  period: { preset: string; from: string | null; to: string | null }
  generated_at: string
  tokens: {
    input: number
    output: number
    cached_input: number
    cached_input_reported_events: number
    /** null when embeddings are not tracked at all */
    embedding: number | null
    embeddings_tracked: boolean
  }
  cost: {
    /** Sum of known costs (paid known prices; free models add 0) */
    known_usd: number
    known_cost_events: number
    free_events: number
    /** Events whose price is unknown - NOT included in known_usd */
    unknown_cost_events: number
    unknown_cost_input_tokens: number
    unknown_cost_output_tokens: number
    is_complete: boolean
  }
  /** Configured budget in USD (STUDY_BUDGET_USD, default 10.0) */
  budget_usd: number
  by_provider: UsageBreakdownItem[]
  by_model: UsageBreakdownItem[]
  by_task_type: UsageBreakdownItem[]
  filter_options: { models: string[]; providers: string[]; task_types: string[] }
  applied_filters: Record<string, string | null>
  pricing_table_version: string
}
