import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'

import { driveApi } from '@/lib/api/drive'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getApiErrorMessage } from '@/lib/utils/error-handler'
import { DriveImportRequest, DriveStatusResponse } from '@/lib/types/drive'

const DRIVE_SYNC_CHANNEL = 'open-notebook-drive'
const DRIVE_SYNC_STORAGE_KEY = 'open-notebook-drive-changed'

/**
 * Tell other open tabs the Drive connection changed (connect, disconnect,
 * reconnect needed) so their Settings card / import panel refetch status.
 * BroadcastChannel where available, a localStorage `storage` event otherwise.
 */
export function notifyDriveChanged() {
  if (typeof window === 'undefined') return
  try {
    if ('BroadcastChannel' in window) {
      const channel = new BroadcastChannel(DRIVE_SYNC_CHANNEL)
      channel.postMessage({ type: 'drive-changed', at: Date.now() })
      channel.close()
      return
    }
  } catch {
    // fall through to storage event
  }
  try {
    window.localStorage.setItem(DRIVE_SYNC_STORAGE_KEY, String(Date.now()))
  } catch {
    // storage unavailable (private mode) - focus refetch still covers it
  }
}

function invalidateDriveStatus(queryClient: QueryClient) {
  return queryClient.invalidateQueries({ queryKey: QUERY_KEYS.driveStatus })
}

/** A Drive call answered 422 = connection missing / needs reconnect (the
 * backend never uses 401 for Drive, which would log the user out). */
function isDriveReconnectError(error: unknown) {
  return isAxiosError(error) && error.response?.status === 422
}

/**
 * Connection status, read from the backend DB on every fetch. Refetches on
 * window focus and when another tab signals a change, so returning from the
 * Google consent screen or disconnecting elsewhere updates this tab too.
 */
export function useDriveStatus() {
  const queryClient = useQueryClient()

  useEffect(() => {
    if (typeof window === 'undefined') return
    const refresh = () => {
      invalidateDriveStatus(queryClient)
    }
    let channel: BroadcastChannel | null = null
    try {
      if ('BroadcastChannel' in window) {
        channel = new BroadcastChannel(DRIVE_SYNC_CHANNEL)
        channel.onmessage = refresh
      }
    } catch {
      channel = null
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key === DRIVE_SYNC_STORAGE_KEY) refresh()
    }
    window.addEventListener('storage', onStorage)
    return () => {
      channel?.close()
      window.removeEventListener('storage', onStorage)
    }
  }, [queryClient])

  return useQuery({
    queryKey: QUERY_KEYS.driveStatus,
    queryFn: () => driveApi.getStatus(),
    staleTime: 5 * 1000,
    refetchOnWindowFocus: 'always',
    retry: 1,
  })
}

/**
 * Fetches the Google OAuth consent URL, then does a full-page redirect - this
 * is a standard OAuth flow, not an API call the caller awaits a result from.
 * Also used for "Reconnect".
 */
export function useConnectDrive() {
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: () => driveApi.getAuthUrl(),
    onSuccess: (data) => {
      window.location.href = data.auth_url
    },
    onError: (error: unknown) => {
      toast({
        title: t('common.error'),
        description: getApiErrorMessage(error, (key) => t(key), t('drive.connectFailed')),
        variant: 'destructive',
      })
    },
  })
}

const DISCONNECTED_STATUS = (prev?: DriveStatusResponse): DriveStatusResponse => ({
  configured: prev?.configured ?? true,
  connected: false,
  account_email: null,
  granted_scopes: [],
  connected_at: null,
  last_sync_at: null,
  needs_reconnect: false,
  reconnect_reason: null,
})

export function useDisconnectDrive() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: () => driveApi.disconnect(),
    // Optimistic: the card flips to "not connected" immediately; rolled
    // back if the request fails, and reconciled with the DB on settle.
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: QUERY_KEYS.driveStatus })
      const previous = queryClient.getQueryData<DriveStatusResponse>(QUERY_KEYS.driveStatus)
      queryClient.setQueryData<DriveStatusResponse>(
        QUERY_KEYS.driveStatus,
        DISCONNECTED_STATUS(previous)
      )
      return { previous }
    },
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ['drive', 'files'] })
      notifyDriveChanged()
      toast({ title: t('drive.disconnected') })
    },
    onError: (error: unknown, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(QUERY_KEYS.driveStatus, context.previous)
      }
      toast({
        title: t('common.error'),
        description: getApiErrorMessage(error, (key) => t(key), t('drive.disconnectFailed')),
        variant: 'destructive',
      })
    },
    onSettled: () => {
      invalidateDriveStatus(queryClient)
    },
  })
}

/**
 * Single-page file list (metadata only). `pageToken` is undefined for the
 * first page; the caller (DriveImportPanel) manages accumulating pages when
 * the user asks for more, mirroring `next_page_token` from Drive's API.
 */
export function useDriveFiles(query: string, pageToken: string | undefined, enabled: boolean) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: QUERY_KEYS.driveFiles(query, pageToken),
    queryFn: async () => {
      try {
        return await driveApi.listFiles({ query: query || undefined, page_token: pageToken })
      } catch (error) {
        // Grant revoked / not connected: the backend just flagged the
        // record - pull the new status so the UI shows "reconnect".
        if (isDriveReconnectError(error)) {
          invalidateDriveStatus(queryClient)
          notifyDriveChanged()
        }
        throw error
      }
    },
    enabled,
    staleTime: 10 * 1000,
    retry: (failureCount, error) => !isDriveReconnectError(error) && failureCount < 2,
  })
}

export function useImportDriveFile() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (data: DriveImportRequest) => driveApi.importFile(data),
    onSuccess: (_, variables) => {
      // Same invalidation shape as useCreateSource() (use-sources.ts) so a
      // Drive import refreshes the sources list exactly like any other
      // add-source method.
      queryClient.invalidateQueries({
        queryKey: QUERY_KEYS.sources(variables.notebook_id),
        refetchType: 'active',
      })
      queryClient.invalidateQueries({
        queryKey: QUERY_KEYS.sourcesInfinite(variables.notebook_id),
        refetchType: 'active',
      })
      queryClient.invalidateQueries({
        queryKey: QUERY_KEYS.sources(),
        refetchType: 'active',
      })
      // last_sync_at changed
      invalidateDriveStatus(queryClient)
      toast({
        title: t('sources.sourceQueued'),
        description: t('sources.sourceQueuedDesc'),
      })
    },
    onError: (error: unknown) => {
      if (isDriveReconnectError(error)) {
        invalidateDriveStatus(queryClient)
        notifyDriveChanged()
      }
      toast({
        title: t('common.error'),
        description: getApiErrorMessage(error, (key) => t(key), t('drive.importFailed')),
        variant: 'destructive',
      })
    },
  })
}
