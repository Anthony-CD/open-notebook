import apiClient from './client'

export interface PrivacyExportBundle {
  generated_at: string
  note: string
  notebooks: Record<string, unknown>[]
  sources: Record<string, unknown>[]
  notes: Record<string, unknown>[]
  chat_sessions: Record<string, unknown>[]
  study_sets: Record<string, unknown>[]
  episodes: Record<string, unknown>[]
  credentials: Record<string, unknown>[]
  drive_connection: Record<string, unknown> | null
}

export interface PrivacyDeleteResponse {
  message: string
  deleted: Record<string, number>
}

/** The exact phrase the server requires in the `confirm` field - must match
 * api.models.PrivacyDeleteRequest / api.privacy_service.REQUIRED_DELETE_CONFIRMATION. */
export const PRIVACY_DELETE_CONFIRMATION_PHRASE = 'DELETE ALL MY DATA'

export const privacyApi = {
  exportData: async (): Promise<PrivacyExportBundle> => {
    const response = await apiClient.get<PrivacyExportBundle>('/privacy/export')
    return response.data
  },

  deleteAllData: async (confirm: string): Promise<PrivacyDeleteResponse> => {
    const response = await apiClient.delete<PrivacyDeleteResponse>('/privacy/data', {
      data: { confirm },
    })
    return response.data
  },
}
