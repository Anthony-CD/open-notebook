'use client'

import { useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, PauseCircle, PlayCircle, RotateCcw } from 'lucide-react'

import { FlashcardItem, SrsRating } from '@/lib/types/study'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useReviewFlashcard, useSuspendFlashcard } from '@/lib/hooks/use-study'
import { buildQueue, isDue, todayIso } from '@/lib/utils/flashcard-queue'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

interface FlashcardViewerProps {
  items: FlashcardItem[]
  studySetId: string
  notebookId?: string
}

const RATING_STYLES: Record<SrsRating, string> = {
  again: 'border-destructive/40 text-destructive hover:bg-destructive-tint',
  hard: 'border-gold/40 text-gold hover:bg-gold-tint',
  good: 'border-fern/40 text-fern hover:bg-fern-tint',
  easy: 'border-teal/40 text-teal hover:bg-teal-tint',
}

// Written as literal keys (not a template string) so the i18n unused-key
// checker (src/lib/locales/index.test.ts, plain substring search) can find
// them.
const RATING_LABEL_KEY: Record<SrsRating, string> = {
  again: 'study.rating.again',
  hard: 'study.rating.hard',
  good: 'study.rating.good',
  easy: 'study.rating.easy',
}

export function FlashcardViewer({ items, studySetId, notebookId }: FlashcardViewerProps) {
  const { t } = useTranslation()
  const [position, setPosition] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [sourceFilter, setSourceFilter] = useState<string>('all')
  const reviewFlashcard = useReviewFlashcard(studySetId, notebookId)
  const suspendFlashcard = useSuspendFlashcard(studySetId, notebookId)

  // Distinct source_title citations across the set, for the "filter by
  // source" dropdown - hidden entirely when no card carries one (older
  // study sets generated before this field existed, or source content that
  // wasn't labeled into `## Source: <title>` sections).
  const sourceTitles = useMemo(() => {
    const set = new Set<string>()
    items.forEach((item) => {
      if (item.source_title) set.add(item.source_title)
    })
    return Array.from(set).sort()
  }, [items])

  const filteredItems = useMemo(() => {
    if (sourceFilter === 'all') return items
    return items.filter((item) => item.source_title === sourceFilter)
  }, [items, sourceFilter])

  // Built once per study set/filter (not on every `items` update, which
  // would reshuffle the deck under the user mid-session as reviews change
  // due dates - see useReviewFlashcard's optimistic cache patch).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const queue = useMemo(() => buildQueue(filteredItems), [studySetId, sourceFilter])

  const dueCount = useMemo(() => {
    const today = todayIso()
    return filteredItems.filter((item) => isDue(item, today)).length
  }, [filteredItems])

  if (items.length === 0) {
    return null
  }

  if (filteredItems.length === 0) {
    return (
      <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
        {t('study.sourceFilterEmpty')}
      </div>
    )
  }

  const clampedPosition = Math.min(position, queue.length - 1)
  const originalFilteredIndex = queue[clampedPosition]
  const card = filteredItems[originalFilteredIndex]
  // Index back into the full `items` array (not `filteredItems`) - the
  // review/suspend mutations address cards by their position in the study
  // set as stored, which the source filter never changes.
  const originalIndex = items.indexOf(card)
  const cardDue = isDue(card, todayIso())

  const goTo = (nextPosition: number) => {
    setPosition(Math.max(0, Math.min(queue.length - 1, nextPosition)))
    setFlipped(false)
  }

  const handleRate = (rating: SrsRating) => {
    reviewFlashcard.mutate({ itemIndex: originalIndex, rating })
    if (clampedPosition < queue.length - 1) {
      goTo(clampedPosition + 1)
    } else {
      setFlipped(false)
    }
  }

  const handleToggleSuspend = () => {
    suspendFlashcard.mutate({ itemIndex: originalIndex, suspended: !card.suspended })
  }

  return (
    <div className="flex w-full max-w-xl flex-col items-center gap-4">
      {sourceTitles.length > 0 ? (
        <Select
          value={sourceFilter}
          onValueChange={(value) => {
            setSourceFilter(value)
            setPosition(0)
            setFlipped(false)
          }}
        >
          <SelectTrigger className="w-full max-w-xs" aria-label={t('study.sourceFilterLabel')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('study.sourceFilterAll')}</SelectItem>
            {sourceTitles.map((title) => (
              <SelectItem key={title} value={title}>
                {title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : null}

      <div className="flex flex-wrap items-center justify-center gap-2">
        <p className="text-sm text-muted-foreground">
          {t('study.cardProgress', { current: clampedPosition + 1, total: queue.length })}
        </p>
        {card.suspended ? (
          <Badge variant="outline" className="border-muted-foreground/40 text-muted-foreground">
            {t('study.suspendedBadge')}
          </Badge>
        ) : dueCount > 0 ? (
          <Badge variant="outline" className="border-teal/40 text-teal">
            {t('study.dueCount', { count: dueCount })}
          </Badge>
        ) : (
          <Badge variant="outline" className="border-fern/40 text-fern">
            {t('study.allCaughtUp')}
          </Badge>
        )}
      </div>

      <div
        className="w-full max-w-xl [perspective:1200px]"
        role="button"
        tabIndex={0}
        aria-label={t('study.flipCard')}
        onClick={() => setFlipped((prev) => !prev)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            setFlipped((prev) => !prev)
          }
        }}
      >
        <div
          className="relative h-64 w-full cursor-pointer transition-transform duration-500 [transform-style:preserve-3d]"
          style={{ transform: flipped ? 'rotateY(180deg)' : 'rotateY(0deg)' }}
        >
          <div className="absolute inset-0 flex items-center justify-center rounded-xl border bg-card p-6 text-center shadow-sm [backface-visibility:hidden]">
            <p className="text-lg font-medium text-foreground">{card.front}</p>
          </div>
          <div
            className="absolute inset-0 flex items-center justify-center rounded-xl border bg-fern-tint p-6 text-center shadow-sm [backface-visibility:hidden]"
            style={{ transform: 'rotateY(180deg)' }}
          >
            <p className="text-lg text-foreground">{card.back}</p>
          </div>
        </div>
      </div>

      {card.source_title ? (
        <p className="text-xs text-muted-foreground">
          {t('study.sourceLabel')}: {card.source_title}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => goTo(clampedPosition - 1)}
          disabled={clampedPosition === 0}
        >
          <ChevronLeft className="h-4 w-4" />
          {t('study.previous')}
        </Button>
        <Button variant="outline" size="sm" onClick={() => setFlipped((prev) => !prev)}>
          <RotateCcw className="h-4 w-4" />
          {t('study.flipCard')}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => goTo(clampedPosition + 1)}
          disabled={clampedPosition === queue.length - 1}
        >
          {t('common.next')}
          <ChevronRight className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={handleToggleSuspend}
          disabled={suspendFlashcard.isPending}
        >
          {card.suspended ? (
            <>
              <PlayCircle className="h-4 w-4" />
              {t('study.resumeCard')}
            </>
          ) : (
            <>
              <PauseCircle className="h-4 w-4" />
              {t('study.suspendCard')}
            </>
          )}
        </Button>
      </div>
      {card.suspended ? (
        <p className="text-xs text-muted-foreground">{t('study.suspendedHint')}</p>
      ) : null}

      {flipped ? (
        <div className="flex flex-col items-center gap-2">
          <p className="text-xs text-muted-foreground">{t('study.reviewPrompt')}</p>
          <div className="flex flex-wrap items-center justify-center gap-2">
            {(['again', 'hard', 'good', 'easy'] as SrsRating[]).map((rating) => (
              <Button
                key={rating}
                variant="outline"
                size="sm"
                disabled={reviewFlashcard.isPending}
                onClick={() => handleRate(rating)}
                className={cn(RATING_STYLES[rating])}
              >
                {t(RATING_LABEL_KEY[rating])}
              </Button>
            ))}
          </div>
          {!cardDue ? (
            <p className="text-xs text-muted-foreground">{t('study.reviewAheadHint')}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
