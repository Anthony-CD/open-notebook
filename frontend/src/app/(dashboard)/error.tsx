'use client'

import { useEffect } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useTranslation } from '@/lib/hooks/use-translation'

/**
 * Route-level error boundary for every page under (dashboard). Rendered
 * inside the dashboard layout, so a crash in one page (e.g. a settings card)
 * shows a translated, recoverable message instead of a blank screen.
 */
export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  const { t } = useTranslation()

  useEffect(() => {
    console.error('Dashboard route error:', error)
  }, [error])

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4" role="alert">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-destructive-tint">
            <AlertTriangle className="h-6 w-6 text-destructive" aria-hidden="true" />
          </div>
          <CardTitle>{t('common.pageErrorTitle')}</CardTitle>
          <CardDescription>{t('common.pageErrorDesc')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {process.env.NODE_ENV === 'development' && (
            <details className="rounded border bg-muted p-3 text-xs">
              <summary className="cursor-pointer font-medium">{t('common.errorDetails')}</summary>
              <pre className="mt-2 whitespace-pre-wrap break-all">{error.message}</pre>
            </details>
          )}
          <Button onClick={() => reset()} className="w-full">
            <RefreshCw className="mr-2 h-4 w-4" aria-hidden="true" />
            {t('common.retry')}
          </Button>
          <Button onClick={() => window.location.reload()} variant="outline" className="w-full">
            {t('common.refresh')}
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
