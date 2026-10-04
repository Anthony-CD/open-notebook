import apiClient from './client'
import { UsageFilters, UsageSummary } from '@/lib/types/usage'

export interface UsageSummaryParams {
  period?: string
  from?: string
  to?: string
  model?: string
  provider?: string
  task_type?: string
}

/** Translate UI filters into query params (`session` -> from = session start). */
export function buildUsageParams(
  filters: UsageFilters,
  sessionStart?: string | null,
): UsageSummaryParams {
  const params: UsageSummaryParams = {}
  if (filters.period === 'session') {
    if (sessionStart) params.from = sessionStart
    else params.period = 'all'
  } else {
    params.period = filters.period
  }
  if (filters.model) params.model = filters.model
  if (filters.provider) params.provider = filters.provider
  if (filters.taskType) params.task_type = filters.taskType
  return params
}

export const usageApi = {
  getSummary: async (params: UsageSummaryParams = { period: '30d' }) => {
    const response = await apiClient.get<UsageSummary>('/usage/summary', { params })
    return response.data
  },
}
