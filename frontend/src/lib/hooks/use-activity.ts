import { useQuery } from '@tanstack/react-query'

import { activityApi, ActivityFilters } from '@/lib/api/activity'

export const ACTIVITY_QUERY_KEY = ['activity'] as const

/**
 * Paginated, filterable audit-log listing for the Privacy & Activity
 * settings page (GET /api/activity - see api/routers/activity.py /
 * api/audit_service.py). Not auto-refreshing: this is a historical log,
 * not a live job status.
 */
export function useActivity(filters: ActivityFilters) {
  return useQuery({
    queryKey: [...ACTIVITY_QUERY_KEY, filters],
    queryFn: () => activityApi.list(filters),
  })
}
