'use client'

import React, { useEffect, useState } from 'react'
import i18n, { syncDocumentLanguage } from '@/lib/i18n'
import { LanguageLoadingOverlay } from '@/components/common/LanguageLoadingOverlay'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    syncDocumentLanguage(i18n.language)
    setMounted(true)
  }, [])

  // Before mount the detected language isn't applied yet (server HTML is
  // rendered without it), so the real tree stays hidden to avoid a flash of
  // the wrong language. Instead of a blank page we show a text-free spinner.
  // The wrapper element is the same before and after mount (only its style
  // changes) so children are NOT remounted when we reveal them.
  return (
    <>
      {!mounted && (
        <div
          className="min-h-screen flex items-center justify-center bg-background"
          aria-busy="true"
          data-testid="i18n-boot"
        >
          <LoadingSpinner className="text-muted-foreground" />
        </div>
      )}
      {mounted && <LanguageLoadingOverlay />}
      <div style={{ display: mounted ? 'contents' : 'none' }}>{children}</div>
    </>
  )
}
