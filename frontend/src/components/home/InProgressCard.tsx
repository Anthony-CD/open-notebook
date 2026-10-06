'use client'

import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { JobStatusBadge } from '@/components/common/JobStatusBadge'
import { sourcesApi } from '@/lib/api/sources'
import { usePodcastEpisodes } from '@/lib/hooks/use-podcasts'
import { useTranslation } from '@/lib/hooks/use-translation'
import { isJobActive } from '@/lib/utils/job-status'

// A bounded recent window, not the whole library - "in progress" only
// cares about sources that were touched recently enough to still be
// processing; an old, long-finished source will never show up here.
const RECENT_SOURCES_SCAN_LIMIT = 50

/**
 * "In progress" home section - sources still processing and podcasts still
 * generating, reusing the job-status infra (JobStatusBadge /
 * lib/utils/job-status.ts) that's otherwise unused by any page yet.
 */
export function InProgressCard() {
  const { t } = useTranslation()
  const { data: sources, isLoading: sourcesLoading } = useQuery({
    queryKey: ['home', 'processing-sources'],
    queryFn: () =>
      sourcesApi.list({ limit: RECENT_SOURCES_SCAN_LIMIT, sort_by: 'updated', sort_order: 'desc' }),
    refetchInterval: 10_000,
  })
  const { statusGroups, isLoading: episodesLoading } = usePodcastEpisodes()

  const processingSources = (sources ?? []).filter((s) => isJobActive(s.status))
  const generatingEpisodes = statusGroups.running

  const isLoading = sourcesLoading || episodesLoading
  const isEmpty = !isLoading && processingSources.length === 0 && generatingEpisodes.length === 0

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t('home.inProgress')}</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {t('home.inProgress')}
          </div>
        ) : isEmpty ? (
          <p className="text-sm text-muted-foreground">{t('home.noInProgress')}</p>
        ) : (
          <ul className="space-y-2">
            {processingSources.map((source) => (
              <li key={`source-${source.id}`}>
                <Link
                  href={`/sources/${encodeURIComponent(source.id)}`}
                  className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 -mx-2 text-sm card-hover"
                >
                  <span className="truncate">
                    {t('home.sourceProcessing', {
                      title: source.title || t('sources.untitledSource'),
                    })}
                  </span>
                  <JobStatusBadge status={source.status} className="shrink-0" />
                </Link>
              </li>
            ))}
            {generatingEpisodes.map((episode) => (
              <li key={`episode-${episode.id}`}>
                <Link
                  href="/podcasts"
                  className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 -mx-2 text-sm card-hover"
                >
                  <span className="truncate">
                    {t('home.podcastGenerating', { name: episode.name })}
                  </span>
                  <JobStatusBadge status={episode.job_status} className="shrink-0" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
