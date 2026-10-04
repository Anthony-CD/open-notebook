'use client'

import { AlertTriangle, Ban, CheckCircle, Clock, HelpCircle, Loader2 } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { useTranslation } from '@/lib/hooks/use-translation'
import { cn } from '@/lib/utils'
import { JobStatus, jobStatusLabelKey, normalizeJobStatus } from '@/lib/utils/job-status'

export const JOB_STATUS_STYLES: Record<JobStatus, { icon: LucideIcon; className: string }> = {
  queued: { icon: Clock, className: 'bg-teal-tint text-teal border-teal/30' },
  processing: { icon: Loader2, className: 'bg-teal-tint text-teal border-teal/30' },
  completed: { icon: CheckCircle, className: 'bg-fern-tint text-fern border-fern/30' },
  failed: {
    icon: AlertTriangle,
    className: 'bg-destructive-tint text-destructive border-destructive/30',
  },
  canceled: { icon: Ban, className: 'bg-muted text-muted-foreground border-transparent' },
  unknown: { icon: HelpCircle, className: 'bg-muted text-muted-foreground border-transparent' },
}

interface JobStatusBadgeProps {
  /** Raw or canonical status from the API - normalized internally. */
  status: unknown
  /** Hide the badge for completed jobs (most lists only flag unfinished work). */
  hideWhenCompleted?: boolean
  showIcon?: boolean
  className?: string
}

/**
 * Localized status badge for any background job (sources, podcasts, study
 * sets, embedding rebuilds...). Never renders the raw status string.
 * Processing uses an indeterminate spinner: the job queue reports no real
 * progress, so no percentage is invented.
 */
export function JobStatusBadge({
  status,
  hideWhenCompleted = false,
  showIcon = true,
  className,
}: JobStatusBadgeProps) {
  const { t } = useTranslation()
  const canonical = normalizeJobStatus(status)
  if (hideWhenCompleted && canonical === 'completed') return null

  const { icon: Icon, className: tone } = JOB_STATUS_STYLES[canonical]
  return (
    <Badge
      variant="outline"
      data-status={canonical}
      className={cn('gap-1 text-xs font-medium', tone, className)}
    >
      {showIcon && (
        <Icon
          aria-hidden
          className={cn('h-3 w-3', canonical === 'processing' && 'animate-spin')}
        />
      )}
      {t(jobStatusLabelKey(canonical))}
    </Badge>
  )
}
