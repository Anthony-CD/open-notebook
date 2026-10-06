'use client'

import Link from 'next/link'
import { GraduationCap } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useStudyMetrics } from '@/lib/hooks/use-study'
import { useTranslation } from '@/lib/hooks/use-translation'

/**
 * Dashboard-home version of the study nudge - cross-notebook (no
 * `notebookId`), same honest numbers as StudyMetricsSummary (see
 * api/study_service.py::StudyService.get_metrics), just condensed to the
 * one line a home card has room for.
 */
export function ContinueStudyingCard() {
  const { t } = useTranslation()
  const { data: metrics, isLoading, isError } = useStudyMetrics()

  const hasStudySets = !isLoading && !isError && !!metrics && metrics.total_cards > 0

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <GraduationCap className="h-4 w-4 text-gold" />
          {t('home.continueStudying')}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading || isError || !metrics ? (
          <p className="text-sm text-muted-foreground">{t('home.noStudySets')}</p>
        ) : !hasStudySets ? (
          <p className="text-sm text-muted-foreground">{t('home.noStudySets')}</p>
        ) : (
          <>
            <p className="text-sm text-foreground">
              {metrics.due_today > 0
                ? t('home.dueToday', { count: metrics.due_today })
                : t('home.allCaughtUp')}
            </p>
            {metrics.accuracy_pct !== null && (
              <p className="text-xs text-muted-foreground">
                {t('home.recallAccuracy', { pct: metrics.accuracy_pct })}
              </p>
            )}
          </>
        )}
        <Button asChild size="sm" variant="outline">
          <Link href="/study">{t('home.goToStudy')}</Link>
        </Button>
      </CardContent>
    </Card>
  )
}
