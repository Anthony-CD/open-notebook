import apiClient from './client'

export interface ActivityItem {
  id: string
  action: string
  detail?: string | null
  created: string
}

export interface ActivityListResponse {
  items: ActivityItem[]
  total: number
  limit: number
  offset: number
}

export interface ActivityFilters {
  action?: string
  limit?: number
  offset?: number
}

export const activityApi = {
  list: async (filters: ActivityFilters = {}): Promise<ActivityListResponse> => {
    const response = await apiClient.get<ActivityListResponse>('/activity', {
      params: {
        action: filters.action || undefined,
        limit: filters.limit ?? 20,
        offset: filters.offset ?? 0,
      },
    })
    return response.data
  },
}
