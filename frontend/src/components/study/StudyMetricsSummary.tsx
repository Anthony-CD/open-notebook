'use client'

import { AlertTriangle } from 'lucide-react'

import { useStudyMetrics } from '@/lib/hooks/use-study'
import { useTranslation } from '@/lib/hooks/use-translation'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'

interface StudyMetricsSummaryProps {
  /** Scopes the metrics to one notebook; omit for the cross-notebook view. */
  notebookId?: string
}

/**
 * Lightweight, honest study metrics summary - P1 Section 4 item 5
 * ("pending today, accuracy rate, weak topics, progress... without
 * misleading gamification"). Every number here is a direct count or plain
 * percentage from recorded review outcomes (see
 * api/study_service.py::StudyService.get_metrics) - no streaks, XP, or
 * invented score.
 *
 * Deliberately standalone (not wired into app/(dashboard)/page.tsx, which
 * is owned by other in-flight work) - this card is placed on the study
 * list page, and is a plain component other work can drop onto a
 * dashboard-home later if it wants to.
 */
export function StudyMetricsSummary({ notebookId }: StudyMetricsSummaryProps) {
  const { t } = useTranslation()
  const { data: metrics, isLoading, isError } = useStudyMetrics(notebookId)

  if (isLoading || isError || !metrics) {
    return null
  }

  const hasReviews = metrics.reviewed_cards > 0

  return (
    <Card>
      <CardContent className="grid gap-4 p-4 sm:grid-cols-3">
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {t('study.metricsDueToday')}
          </p>
          <p className="text-2xl font-bold text-foreground">{metrics.due_today}</p>
        </div>

        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {t('study.metricsAccuracy')}
          </p>
          {metrics.accuracy_pct !== null ? (
            <>
              <p className="text-2xl font-bold text-foreground">{metrics.accuracy_pct}%</p>
              <p className="text-xs text-muted-foreground">
                {t('study.metricsReviewed', {
                  reviewed: metrics.reviewed_cards,
                  total: metrics.total_cards,
                })}
              </p>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">{t('study.metricsAccuracyNone')}</p>
          )}
        </div>

        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {t('study.metricsWeakSources')}
          </p>
          {!hasReviews || metrics.weak_sources.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('study.metricsWeakSourcesEmpty')}</p>
          ) : (
            <ul className="space-y-1">
              {metrics.weak_sources.map((source) => (
                <li key={source.source_title} className="flex items-center gap-1.5 text-sm">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-gold" />
                  <span className="truncate text-foreground">{source.source_title}</span>
                  <Badge variant="outline" className="shrink-0 border-gold/40 text-gold">
                    {t('study.metricsWeakSourceStat', {
                      accuracy: source.accuracy_pct,
                      count: source.reviewed_count,
                    })}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
