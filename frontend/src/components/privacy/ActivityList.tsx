'use client'

import { useState } from 'react'
import { formatDistanceToNow } from 'date-fns'
import { ChevronLeft, ChevronRight } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useActivity } from '@/lib/hooks/use-activity'
import { useTranslation } from '@/lib/hooks/use-translation'
import { auditActionLabelKey, KNOWN_AUDIT_ACTIONS } from '@/lib/utils/audit-action'
import { getDateLocale } from '@/lib/utils/date-locale'

const PAGE_SIZE = 20
const ALL_ACTIONS = '__all__'

/**
 * Paginated, filterable audit-log view (GET /api/activity - see
 * api/routers/activity.py). Every action code is shown through
 * auditActionLabelKey(), never the raw backend code - mirrors
 * JobStatusBadge's normalize-then-translate pattern.
 */
export function ActivityList() {
  const { t, language } = useTranslation()
  const locale = getDateLocale(language)
  const [action, setAction] = useState<string | undefined>(undefined)
  const [offset, setOffset] = useState(0)

  const { data, isLoading, isError } = useActivity({ action, limit: PAGE_SIZE, offset })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + PAGE_SIZE, total)

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle className="text-base">{t('activity.title')}</CardTitle>
        <Select
          value={action ?? ALL_ACTIONS}
          onValueChange={(value) => {
            setAction(value === ALL_ACTIONS ? undefined : value)
            setOffset(0)
          }}
        >
          <SelectTrigger className="w-full sm:w-56" aria-label={t('activity.filterLabel')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_ACTIONS}>{t('activity.filterAll')}</SelectItem>
            {KNOWN_AUDIT_ACTIONS.map((code) => (
              <SelectItem key={code} value={code}>
                {t(auditActionLabelKey(code))}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </CardHeader>
      <CardContent>
        <p className="mb-3 text-sm text-muted-foreground">{t('activity.description')}</p>

        {isLoading ? (
          <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
        ) : isError ? (
          <p className="text-sm text-destructive">{t('activity.loadFailed')}</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('activity.empty')}</p>
        ) : (
          <ul className="divide-y">
            {items.map((item) => (
              <li key={item.id} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <Badge variant="outline" className="mb-1">
                    {t(auditActionLabelKey(item.action))}
                  </Badge>
                  {item.detail && (
                    <p className="truncate text-sm text-muted-foreground">{item.detail}</p>
                  )}
                </div>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {item.created
                    ? formatDistanceToNow(new Date(item.created), { addSuffix: true, locale })
                    : null}
                </span>
              </li>
            ))}
          </ul>
        )}

        {total > PAGE_SIZE && (
          <div className="mt-4 flex items-center justify-between">
            <span className="text-xs text-muted-foreground">
              {t('activity.pageInfo', { from, to, total })}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                <ChevronLeft className="mr-1 h-3.5 w-3.5" />
                {t('activity.previousPage')}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                {t('activity.nextPage')}
                <ChevronRight className="ml-1 h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
