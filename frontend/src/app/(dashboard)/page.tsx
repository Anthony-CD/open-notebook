'use client'

import { useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { EmptyState } from '@/components/common/EmptyState'
import { Button } from '@/components/ui/button'
import { ContinueStudyingCard } from '@/components/home/ContinueStudyingCard'
import { RecentNotebooksCard } from '@/components/home/RecentNotebooksCard'
import { InProgressCard } from '@/components/home/InProgressCard'
import { QuickActionsCard } from '@/components/home/QuickActionsCard'
import { OnboardingFlow } from '@/components/onboarding/OnboardingFlow'
import { useNotebooks } from '@/lib/hooks/use-notebooks'
import { useOnboardingStore } from '@/lib/stores/onboarding-store'
import { useTranslation } from '@/lib/hooks/use-translation'

/**
 * Dashboard home - replaces the old `redirect('/notebooks')` placeholder.
 * Zero-notebook instances get a gentle nudge toward onboarding instead of
 * empty due-today/recent-notebooks cards (there's nothing honest to show
 * there yet); everyone else gets the real cross-notebook study/recent/
 * in-progress summary plus quick actions.
 */
export default function DashboardHomePage() {
  const { t } = useTranslation()
  const { data: notebooks, isLoading: notebooksLoading } = useNotebooks(false)
  const completed = useOnboardingStore((state) => state.completed)
  const dismissed = useOnboardingStore((state) => state.dismissed)

  // Avoids an SSR/client hydration mismatch on the persisted onboarding
  // flags (onboarding-store has no `hasHydrated` flag of its own to check,
  // unlike preferences-store - see frontend/AGENTS.md's zustand gotcha).
  const [mounted, setMounted] = useState(false)
  const [onboardingOpen, setOnboardingOpen] = useState(false)

  useEffect(() => setMounted(true), [])
  useEffect(() => {
    if (mounted && !completed && !dismissed) {
      setOnboardingOpen(true)
    }
  }, [mounted, completed, dismissed])

  const hasNotebooks = !notebooksLoading && !!notebooks && notebooks.length > 0
  const showEmptyNudge = !notebooksLoading && !hasNotebooks

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl space-y-6 p-4 sm:p-6">
          <header className="space-y-1">
            <h1 className="font-display text-2xl font-bold tracking-tight">{t('home.title')}</h1>
            <p className="text-lg font-medium text-foreground">{t('home.welcomeBack')}</p>
            <p className="text-muted-foreground">{t('home.subtitle')}</p>
          </header>

          {showEmptyNudge ? (
            <EmptyState
              icon={Sparkles}
              title={t('onboarding.welcomeTitle')}
              description={t('onboarding.welcomeDesc')}
              action={
                <Button onClick={() => setOnboardingOpen(true)}>{t('onboarding.start')}</Button>
              }
            />
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <ContinueStudyingCard />
                <RecentNotebooksCard />
              </div>
              <InProgressCard />
            </>
          )}

          <QuickActionsCard />
        </div>
      </div>

      <OnboardingFlow open={onboardingOpen} onOpenChange={setOnboardingOpen} />
    </AppShell>
  )
}
