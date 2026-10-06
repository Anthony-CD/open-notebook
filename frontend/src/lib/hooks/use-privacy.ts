import { useMutation, useQueryClient } from '@tanstack/react-query'

import { privacyApi } from '@/lib/api/privacy'
import { ACTIVITY_QUERY_KEY } from '@/lib/hooks/use-activity'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getApiErrorKey } from '@/lib/utils/error-handler'

/** Triggers a real browser download of the export bundle - no server-side
 * file persisted, matches api/privacy_service.py's "export is a direct
 * response, not a stored artifact" shape. */
function downloadJson(data: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

export function useExportData() {
  const { toast } = useToast()
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => privacyApi.exportData(),
    onSuccess: (bundle) => {
      downloadJson(bundle, `open-notebook-export-${new Date().toISOString().slice(0, 10)}.json`)
      toast({ title: t('privacy.exportSuccessToast') })
      // The export itself is an audited action (ACTION_DATA_EXPORTED) - refresh
      // the activity list so it shows up without a manual reload.
      queryClient.invalidateQueries({ queryKey: ACTIVITY_QUERY_KEY })
    },
    onError: (error: unknown) => {
      toast({
        title: t(getApiErrorKey(error, t('privacy.exportFailedToast'))),
        variant: 'destructive',
      })
    },
  })
}

export function useDeleteAllData() {
  const { toast } = useToast()
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (confirm: string) => privacyApi.deleteAllData(confirm),
    onSuccess: () => {
      toast({ title: t('privacy.deleteSuccessToast') })
      queryClient.invalidateQueries({ queryKey: ACTIVITY_QUERY_KEY })
      // The wipe touches nearly every domain query in the app - broad
      // invalidation is deliberate here (frontend/AGENTS.md's documented
      // trade-off), not an oversight.
      queryClient.invalidateQueries()
    },
    onError: (error: unknown) => {
      toast({
        title: t(getApiErrorKey(error, t('privacy.deleteFailedToast'))),
        variant: 'destructive',
      })
    },
  })
}
