'use client'

import Link from 'next/link'
import { useMemo } from 'react'
import { formatDistanceToNow } from 'date-fns'
import { Book } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useNotebooks } from '@/lib/hooks/use-notebooks'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getDateLocale } from '@/lib/utils/date-locale'

const RECENT_LIMIT = 5

/**
 * Notebook-only "recent" list for the dashboard home, sorted by `updated`.
 * Deliberately not RecentlyViewed (components/notebooks/RecentlyViewed.tsx)
 * reused verbatim - that component mixes notebooks and sources into one
 * list from the recently-viewed endpoint, while home.recentNotebooks /
 * home.noRecentNotebooks's copy is notebooks-only. Same data source
 * (useNotebooks) the notebooks list page already uses, same
 * formatDistanceToNow + date-locale pattern RecentlyViewed uses.
 */
export function RecentNotebooksCard() {
  const { t, language } = useTranslation()
  const { data: notebooks, isLoading } = useNotebooks(false)
  const locale = getDateLocale(language)

  const recent = useMemo(() => {
    if (!notebooks) return []
    return [...notebooks]
      .sort((a, b) => new Date(b.updated).getTime() - new Date(a.updated).getTime())
      .slice(0, RECENT_LIMIT)
  }, [notebooks])

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Book className="h-4 w-4 text-teal" />
          {t('home.recentNotebooks')}
        </CardTitle>
        {recent.length > 0 && (
          <Link href="/notebooks" className="text-xs text-muted-foreground hover:underline">
            {t('home.viewAll')}
          </Link>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="text-sm text-muted-foreground">{t('home.noRecentNotebooks')}</p>
        ) : recent.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('home.noRecentNotebooks')}</p>
        ) : (
          <ul className="space-y-2">
            {recent.map((notebook) => (
              <li key={notebook.id}>
                <Link
                  href={`/notebooks/${encodeURIComponent(notebook.id)}`}
                  className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 -mx-2 text-sm card-hover"
                >
                  <span className="truncate font-medium">{notebook.name}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatDistanceToNow(new Date(notebook.updated), { addSuffix: true, locale })}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
