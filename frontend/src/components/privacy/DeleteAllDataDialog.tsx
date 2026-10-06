'use client'

import { useEffect, useState } from 'react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { PRIVACY_DELETE_CONFIRMATION_PHRASE } from '@/lib/api/privacy'
import { useDeleteAllData } from '@/lib/hooks/use-privacy'
import { useTranslation } from '@/lib/hooks/use-translation'

interface DeleteAllDataDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * Same bar as other destructive dialogs in the app (e.g.
 * NotebookDeleteDialog): names exactly what's affected, and - since this
 * is a full-instance wipe, not a scoped delete - additionally requires
 * typing the exact confirmation phrase the server also checks
 * (api/privacy_service.py REQUIRED_DELETE_CONFIRMATION), not just a click.
 */
export function DeleteAllDataDialog({ open, onOpenChange }: DeleteAllDataDialogProps) {
  const { t } = useTranslation()
  const [confirmText, setConfirmText] = useState('')
  const deleteAllData = useDeleteAllData()

  useEffect(() => {
    if (open) setConfirmText('')
  }, [open])

  const phraseMatches = confirmText === PRIVACY_DELETE_CONFIRMATION_PHRASE

  const handleConfirm = async () => {
    if (!phraseMatches) return
    await deleteAllData.mutateAsync(confirmText)
    onOpenChange(false)
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t('privacy.deleteDialogTitle')}</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2 text-sm">
              <p>{t('privacy.deleteDialogIncludes')}</p>
              <p>{t('privacy.deleteDialogExcludes')}</p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-2">
          <Label htmlFor="delete-confirm-phrase">
            {t('privacy.deleteConfirmLabel', { phrase: PRIVACY_DELETE_CONFIRMATION_PHRASE })}
          </Label>
          <Input
            id="delete-confirm-phrase"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder={t('privacy.deleteConfirmPlaceholder')}
            autoComplete="off"
          />
          {confirmText.length > 0 && !phraseMatches && (
            <p className="text-xs text-destructive">{t('privacy.deleteConfirmMismatch')}</p>
          )}
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleteAllData.isPending}>
            {t('common.cancel')}
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={handleConfirm}
            disabled={!phraseMatches || deleteAllData.isPending}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {deleteAllData.isPending ? (
              <>
                <LoadingSpinner size="sm" className="mr-2" />
                {t('privacy.deleteConfirmButton')}
              </>
            ) : (
              t('privacy.deleteConfirmButton')
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
