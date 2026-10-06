'use client'

import { useEffect } from 'react'
import i18n from '@/lib/i18n'
import { enUS } from '@/lib/locales/en-US'
import './globals.css'

// global-error replaces the root layout, so none of the providers (and no
// useTranslation) are available here: translate via the i18n instance and
// fall back to raw en-US strings if i18n is what failed.
function tr(key: string, fallback: string): string {
  try {
    const value = i18n.t(key)
    return value && value !== key ? value : fallback
  } catch {
    return fallback
  }
}

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error('Global application error:', error)
  }, [error])

  const lang = (typeof document !== 'undefined' && i18n.language) || 'en-US'

  return (
    <html lang={lang}>
      <body className="font-sans bg-background text-foreground">
        <div className="flex min-h-screen items-center justify-center p-4" role="alert">
          <div className="w-full max-w-md space-y-4 rounded-lg border bg-card p-6 text-center shadow-sm">
            <h1 className="text-lg font-semibold">{tr('common.appErrorTitle', enUS.common.appErrorTitle)}</h1>
            <p className="text-sm text-muted-foreground">{tr('common.pageErrorDesc', enUS.common.pageErrorDesc)}</p>
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => reset()}
                className="h-9 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
              >
                {tr('common.retry', enUS.common.retry)}
              </button>
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="h-9 rounded-md border px-4 text-sm font-medium hover:bg-accent"
              >
                {tr('common.refresh', enUS.common.refresh)}
              </button>
            </div>
          </div>
        </div>
      </body>
    </html>
  )
}
