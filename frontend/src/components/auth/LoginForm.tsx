'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/lib/hooks/use-auth'
import { useAuthStore } from '@/lib/stores/auth-store'
import { getConfig } from '@/lib/config'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { AlertCircle, ChevronDown, Languages } from 'lucide-react'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { useTranslation } from '@/lib/hooks/use-translation'
import { languages } from '@/lib/locales'
import { normalizeLanguage } from '@/lib/i18n'
import { cn } from '@/lib/utils'

/**
 * Compact language picker for the (unauthenticated) login screen. Uses a
 * native <select> so it works without the app shell and is fully
 * keyboard/screen-reader accessible. Changing it never navigates.
 */
export function LoginLanguageSwitcher({ className }: { className?: string }) {
  const { t, language, setLanguage } = useTranslation()
  const current = normalizeLanguage(language)

  return (
    <label className={cn('inline-flex items-center gap-1.5 text-xs text-muted-foreground', className)}>
      <Languages className="h-3.5 w-3.5" aria-hidden="true" />
      <span className="sr-only">{t('common.language')}</span>
      <select
        aria-label={t('common.language')}
        value={current}
        onChange={(e) => void setLanguage(e.target.value)}
        className="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {languages.map((lang) => (
          <option key={lang.code} value={lang.code} lang={lang.code}>
            {lang.label}
          </option>
        ))}
      </select>
    </label>
  )
}

/**
 * Honest password-recovery help. Open Notebook has a single shared password
 * (OPEN_NOTEBOOK_PASSWORD on the server); there is no email reset flow, so
 * this only explains who can recover/change it.
 */
export function ForgotPasswordHelp() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)

  return (
    <div className="text-sm">
      <button
        type="button"
        className="inline-flex items-center gap-1 text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
        aria-expanded={open}
        aria-controls="forgot-password-help"
        onClick={() => setOpen((v) => !v)}
      >
        {t('auth.forgotPassword')}
        <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')} aria-hidden="true" />
      </button>
      {open && (
        <div
          id="forgot-password-help"
          className="mt-2 space-y-2 rounded-md border bg-muted/40 p-3 text-xs text-muted-foreground text-left"
        >
          <p>{t('auth.forgotPasswordHelp1')}</p>
          <p>{t('auth.forgotPasswordHelp2')}</p>
        </div>
      )}
    </div>
  )
}

export function LoginForm() {
  const { t, language } = useTranslation()
  const [password, setPassword] = useState('')
  const { login, isLoading, error, errorParams } = useAuth()
  const { authRequired, checkAuthRequired, hasHydrated, isAuthenticated } = useAuthStore()
  const [isCheckingAuth, setIsCheckingAuth] = useState(true)
  const [configInfo, setConfigInfo] = useState<{ apiUrl: string; version: string; buildTime: string } | null>(null)
  const router = useRouter()

  // Load config info for debugging
  useEffect(() => {
    getConfig().then(cfg => {
      setConfigInfo({
        apiUrl: cfg.apiUrl,
        version: cfg.version,
        buildTime: cfg.buildTime,
      })
    }).catch(err => {
      console.error('Failed to load config:', err)
    })
  }, [])

  // Check if authentication is required on mount
  useEffect(() => {
    if (!hasHydrated) {
      return
    }

    const checkAuth = async () => {
      try {
        const required = await checkAuthRequired()

        // If auth is not required, redirect to notebooks
        if (!required) {
          router.push('/notebooks')
        }
      } catch (error) {
        console.error('Error checking auth requirement:', error)
        // On error, assume auth is required to be safe
      } finally {
        setIsCheckingAuth(false)
      }
    }

    // If we already know auth status, use it
    if (authRequired !== null) {
      if (!authRequired && isAuthenticated) {
        router.push('/notebooks')
      } else {
        setIsCheckingAuth(false)
      }
    } else {
      void checkAuth()
    }
  }, [hasHydrated, authRequired, checkAuthRequired, router, isAuthenticated])

  // Show loading while checking if auth is required
  if (!hasHydrated || isCheckingAuth) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <LoadingSpinner />
      </div>
    )
  }

  // If we still don't know if auth is required (connection error), show error
  if (authRequired === null) {
    return (
      <div className="relative min-h-screen flex items-center justify-center bg-background p-4">
        <LoginLanguageSwitcher className="absolute right-4 top-4" />
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <CardTitle>{t('common.connectionError')}</CardTitle>
            <CardDescription>
              {t('common.unableToConnect')}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="flex items-start gap-2 text-destructive text-sm">
                <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                <div className="flex-1">
                  {t(error || 'auth.connectErrorHint', errorParams ?? undefined)}
                </div>
              </div>

              {configInfo && (
                <div className="space-y-2 text-xs text-muted-foreground border-t pt-3">
                  <div className="font-medium">{t('common.diagnosticInfo')}:</div>
                  <div className="space-y-1 font-mono">
                    {configInfo.version && <div>{t('common.version')}: {configInfo.version}</div>}
                    <div>{t('common.built')}: {new Date(configInfo.buildTime).toLocaleString(normalizeLanguage(language))}</div>
                    <div className="break-all">{t('common.apiUrl')}: {configInfo.apiUrl}</div>
                    <div className="break-all">{t('common.frontendUrl')}: {typeof window !== 'undefined' ? window.location.href : 'N/A'}</div>
                  </div>
                  <div className="text-xs pt-2">
                    {t('common.checkConsoleLogs')}
                  </div>
                </div>
              )}

              <Button
                onClick={() => window.location.reload()}
                className="w-full"
              >
                {t('common.retryConnection')}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (password.trim()) {
      try {
        await login(password)
      } catch (error) {
        console.error('Unhandled error during login:', error)
        // The auth store should handle most errors, but this catches any unhandled ones
      }
    }
  }

  return (
    <div className="relative min-h-screen flex items-center justify-center bg-background p-4">
      <LoginLanguageSwitcher className="absolute right-4 top-4" />
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle>{t('auth.loginTitle')}</CardTitle>
          <CardDescription>
            {t('auth.loginDesc')}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <Input
                type="password"
                autoComplete="current-password"
                aria-label={t('auth.passwordPlaceholder')}
                placeholder={t('auth.passwordPlaceholder')}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={isLoading}
              />
            </div>

            {error && (
              <div className="flex items-center gap-2 text-destructive text-sm" role="alert">
                <AlertCircle className="h-4 w-4 flex-shrink-0" />
                {t(error, errorParams ?? undefined)}
              </div>
            )}

            <Button
              type="submit"
              className="w-full"
              disabled={isLoading || !password.trim()}
            >
              {isLoading ? t('auth.signingIn') : t('auth.signIn')}
            </Button>

            <div className="text-center">
              <ForgotPasswordHelp />
            </div>

            {configInfo && (
              <div className="text-xs text-center text-muted-foreground pt-2 border-t">
                {configInfo.version && <div>{t('common.version')} {configInfo.version}</div>}
                <div className="font-mono text-[10px]">{configInfo.apiUrl}</div>
              </div>
            )}
          </form>
        </CardContent>
      </Card>
    </div>
  )
}