import { useQuery } from '@tanstack/react-query'

import { buildUsageParams, usageApi } from '@/lib/api/usage'
import { UsageFilters } from '@/lib/types/usage'

const SESSION_START_KEY = 'open-notebook:usage-session-start'

/**
 * Start of the current browser session (stored in sessionStorage, so it
 * resets when the tab/browser session ends). "This session" usage = events
 * recorded since this instant - an honest, clearly-labelled approximation,
 * not a server-side session concept.
 */
export function getUsageSessionStart(): string | null {
  if (typeof window === 'undefined') return null
  try {
    let value = window.sessionStorage.getItem(SESSION_START_KEY)
    if (!value) {
      value = new Date().toISOString()
      window.sessionStorage.setItem(SESSION_START_KEY, value)
    }
    return value
  } catch {
    return null
  }
}

/**
 * Spend changes infrequently (it's aggregated from LLM calls, not a live
 * feed), so a short staleTime plus refetch-on-focus keeps it fresh enough
 * without polling.
 */
export function useUsageSummary(filters: UsageFilters = { period: '30d' }) {
  const sessionStart = filters.period === 'session' ? getUsageSessionStart() : null
  const params = buildUsageParams(filters, sessionStart)
  return useQuery({
    queryKey: ['usage', 'summary', params] as const,
    queryFn: () => usageApi.getSummary(params),
    staleTime: 60 * 1000,
    refetchOnWindowFocus: true,
  })
}
