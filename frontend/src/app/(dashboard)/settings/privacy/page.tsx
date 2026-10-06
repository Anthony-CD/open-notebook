'use client'

import { useState } from 'react'
import { Download, ShieldAlert, Trash2 } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { ActivityList } from '@/components/privacy/ActivityList'
import { DeleteAllDataDialog } from '@/components/privacy/DeleteAllDataDialog'
import { useExportData } from '@/lib/hooks/use-privacy'
import { useTranslation } from '@/lib/hooks/use-translation'

export default function PrivacyPage() {
  const { t } = useTranslation()
  const exportData = useExportData()
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="p-6">
          <div className="max-w-4xl space-y-6">
            <div>
              <h1 className="font-display text-2xl font-bold tracking-tight">
                {t('privacy.title')}
              </h1>
              <p className="mt-2 text-muted-foreground">{t('privacy.description')}</p>
            </div>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <Download className="h-4 w-4 text-teal" />
                  {t('privacy.exportTitle')}
                </CardTitle>
                <CardDescription>{t('privacy.exportDesc')}</CardDescription>
              </CardHeader>
              <CardContent>
                <Button
                  variant="outline"
                  onClick={() => exportData.mutate()}
                  disabled={exportData.isPending}
                >
                  {exportData.isPending ? (
                    <LoadingSpinner size="sm" className="mr-2" />
                  ) : (
                    <Download className="mr-2 h-4 w-4" />
                  )}
                  {t('privacy.exportButton')}
                </Button>
              </CardContent>
            </Card>

            <Card className="border-destructive/30">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base text-destructive">
                  <ShieldAlert className="h-4 w-4" />
                  {t('privacy.deleteTitle')}
                </CardTitle>
                <CardDescription>{t('privacy.deleteDesc')}</CardDescription>
              </CardHeader>
              <CardContent>
                <Button variant="destructive" onClick={() => setDeleteDialogOpen(true)}>
                  <Trash2 className="mr-2 h-4 w-4" />
                  {t('privacy.deleteButton')}
                </Button>
              </CardContent>
            </Card>

            <ActivityList />
          </div>
        </div>
      </div>

      <DeleteAllDataDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen} />
    </AppShell>
  )
}
