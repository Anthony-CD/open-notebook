'use client'

import { useEffect, useMemo, useState } from 'react'
import { Loader2, AlertCircle } from 'lucide-react'

import { getUsageSessionStart, useUsageSummary } from '@/lib/hooks/use-usage'
import { useTranslation } from '@/lib/hooks/use-translation'
import {
  UsageBreakdownItem,
  UsageFilters,
  UsagePeriod,
  UsageSummary,
} from '@/lib/types/usage'
import { cn } from '@/lib/utils'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

type ThresholdLevel = 'ok' | 'warn' | 'over'

function levelFor(percent: number): ThresholdLevel {
  if (percent >= 90) return 'over'
  if (percent >= 60) return 'warn'
  return 'ok'
}

const LEVEL_INDICATOR_CLASS: Record<ThresholdLevel, string> = {
  ok: 'bg-fern',
  warn: 'bg-warn',
  over: 'bg-destructive',
}

const LEVEL_TEXT_CLASS: Record<ThresholdLevel, string> = {
  ok: 'text-fern',
  warn: 'text-warn',
  over: 'text-destructive',
}

const PERIOD_OPTIONS: Array<{ value: UsagePeriod; key: string }> = [
  { value: 'session', key: 'usage.periodSession' },
  { value: '7d', key: 'usage.period7d' },
  { value: '30d', key: 'usage.period30d' },
  { value: '365d', key: 'usage.period365d' },
  { value: 'all', key: 'usage.periodAll' },
]

const ALL_VALUE = '__all__'

function formatUsd(value: number, locale: string) {
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value)
  } catch {
    return `$${value.toFixed(2)}`
  }
}

function formatDateTime(iso: string, locale: string) {
  try {
    return new Intl.DateTimeFormat(locale, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

function formatNumber(value: number, locale: string) {
  try {
    return new Intl.NumberFormat(locale).format(value)
  } catch {
    return String(value)
  }
}

/**
 * The subset of UsageCostTotals / UsageBreakdownItem needed to classify a
 * cost. The two source types use different field names for the dollar
 * amount (`known_usd` vs `cost_known_usd`) - callers normalize to this shape.
 */
interface CostFields {
  amountUsd: number
  knownCostEvents: number
  freeEvents: number
  unknownCostEvents: number
}

function costFieldsFromSummary(cost: UsageSummary['cost']): CostFields {
  return {
    amountUsd: cost.known_usd,
    knownCostEvents: cost.known_cost_events,
    freeEvents: cost.free_events,
    unknownCostEvents: cost.unknown_cost_events,
  }
}

function costFieldsFromBreakdownItem(item: UsageBreakdownItem): CostFields {
  return {
    amountUsd: item.cost_known_usd,
    knownCostEvents: item.known_cost_events,
    freeEvents: item.free_events,
    unknownCostEvents: item.unknown_cost_events,
  }
}

type CostKind = 'unavailable' | 'free' | 'partial' | 'known'

/**
 * Classifies a cost total for display. Mirrors the known/free/unknown
 * semantics in open_notebook/ai/pricing.py: known_cost_events + free_events +
 * unknown_cost_events always equals the event count, so these four buckets
 * are exhaustive and mutually exclusive.
 */
function classifyCost(fields: CostFields, eventCount: number): CostKind {
  if (eventCount <= 0) return 'known'
  if (fields.unknownCostEvents === eventCount) return 'unavailable'
  if (fields.knownCostEvents === 0 && fields.freeEvents === eventCount) return 'free'
  if (fields.unknownCostEvents > 0) return 'partial'
  return 'known'
}

type TFunction = (key: string, opts?: Record<string, unknown>) => string

interface CostValueProps {
  fields: CostFields
  eventCount: number
  locale: string
  t: TFunction
  size?: 'sm' | 'lg'
  showHint?: boolean
  levelClassName?: string
}

/** Renders a cost total, never implying more certainty than the data supports. */
function CostValue({
  fields,
  eventCount,
  locale,
  t,
  size = 'sm',
  showHint = true,
  levelClassName,
}: CostValueProps) {
  const kind = classifyCost(fields, eventCount)
  const amountText = formatUsd(fields.amountUsd, locale)
  const sizeClass = size === 'lg' ? 'text-lg font-semibold' : 'text-sm font-medium'

  if (kind === 'unavailable') {
    return (
      <div className="flex flex-col items-end gap-0.5">
        <span className={cn(sizeClass, 'font-mono text-muted-foreground')}>
          {t('usage.costUnavailable')}
        </span>
        {showHint && (
          <span className="text-xs text-muted-foreground">{t('usage.costUnavailableHint')}</span>
        )}
      </div>
    )
  }

  if (kind === 'free') {
    return (
      <div className="flex flex-col items-end gap-0.5">
        <Badge variant="secondary">{t('usage.freeLabel')}</Badge>
        {showHint && (
          <span className="text-xs text-muted-foreground">{t('usage.allFreeHint')}</span>
        )}
      </div>
    )
  }

  if (kind === 'partial') {
    return (
      <div className="flex flex-col items-end gap-0.5">
        <span className={cn(sizeClass, 'font-mono', levelClassName)}>
          {t('usage.costAtLeast', { amount: amountText })}
        </span>
        {showHint && (
          <span className="text-xs text-muted-foreground">
            {t('usage.unknownCalls', { count: fields.unknownCostEvents })} ·{' '}
            {t('usage.costPartialHint')}
          </span>
        )}
      </div>
    )
  }

  const isLessThanCent = fields.amountUsd > 0 && fields.amountUsd < 0.01
  const display = isLessThanCent
    ? t('usage.lessThan', { amount: formatUsd(0.01, locale) })
    : amountText

  return (
    <span
      className={cn(sizeClass, 'font-mono', levelClassName)}
      title={t('usage.exactCost', { amount: amountText })}
    >
      {display}
    </span>
  )
}

interface FilterSelectProps {
  value: string | undefined
  onChange: (value: string | undefined) => void
  options: string[]
  allLabel: string
  ariaLabel: string
}

function FilterSelect({ value, onChange, options, allLabel, ariaLabel }: FilterSelectProps) {
  if (options.length === 0) return null
  return (
    <Select
      value={value ?? ALL_VALUE}
      onValueChange={(next) => onChange(next === ALL_VALUE ? undefined : next)}
    >
      <SelectTrigger size="sm" aria-label={ariaLabel} className="h-8 max-w-[160px]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL_VALUE}>{allLabel}</SelectItem>
        {options.map((option) => (
          <SelectItem key={option} value={option}>
            {option}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

interface BreakdownListProps {
  title: string
  items: UsageBreakdownItem[]
  locale: string
  t: TFunction
}

function BreakdownList({ title, items, locale, t }: BreakdownListProps) {
  if (items.length === 0) return null
  const sorted = [...items].sort((a, b) => b.cost_known_usd - a.cost_known_usd)

  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      <div className="space-y-2">
        {sorted.map((item) => (
          <div key={item.key} className="flex items-center justify-between gap-3 text-sm">
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-foreground">{item.key}</span>
              <span className="text-xs text-muted-foreground">
                {formatNumber(item.event_count, locale)} {t('usage.calls')}
                {item.failed_count > 0 &&
                  ` · ${t('usage.failedCalls', { count: item.failed_count })}`}
              </span>
            </div>
            <CostValue
              fields={costFieldsFromBreakdownItem(item)}
              eventCount={item.event_count}
              locale={locale}
              t={t}
              showHint={false}
            />
          </div>
        ))}
      </div>
    </div>
  )
}

interface CostMeterWidgetProps {
  /** Renders a slim, single-line version with no breakdown (for a persistent header/sidebar spot). */
  compact?: boolean
  className?: string
}

export function CostMeterWidget({ compact = false, className }: CostMeterWidgetProps) {
  const { t, language } = useTranslation()
  const [filters, setFilters] = useState<UsageFilters>({ period: '30d' })
  const { data, isLoading, isError, refetch } = useUsageSummary(filters)

  // Keep the last known filter options visible while a new period/filter
  // selection is refetching, so the dropdowns don't flicker empty.
  const [lastFilterOptions, setLastFilterOptions] = useState<UsageSummary['filter_options'] | null>(
    null,
  )
  useEffect(() => {
    if (data) setLastFilterOptions(data.filter_options)
  }, [data])
  const filterOptions = data?.filter_options ?? lastFilterOptions

  // The budget bar is necessarily measured against known cost only
  // (cost.known_usd): calls with an unknown price can't be counted toward
  // it, so real spend may be higher than this bar shows when
  // cost.unknown_cost_events > 0.
  const percent = useMemo(() => {
    if (!data || data.budget_usd <= 0) return 0
    return Math.min(100, (data.cost.known_usd / data.budget_usd) * 100)
  }, [data])

  const level = levelFor(percent)

  const sessionStart = useMemo(
    () => (filters.period === 'session' ? getUsageSessionStart() : null),
    [filters.period],
  )

  const setPeriod = (period: UsagePeriod) => setFilters((prev) => ({ ...prev, period }))
  const setModel = (model: string | undefined) => setFilters((prev) => ({ ...prev, model }))
  const setProvider = (provider: string | undefined) =>
    setFilters((prev) => ({ ...prev, provider }))
  const setTaskType = (taskType: string | undefined) =>
    setFilters((prev) => ({ ...prev, taskType }))

  if (compact) {
    return (
      <div className={cn('space-y-1.5', className)}>
        <div className="flex items-center justify-between text-[11px] text-sidebar-foreground/60">
          <span>{t('usage.costMeter')}</span>
          {isLoading && !data ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-label={t('common.loading')} />
          ) : isError && !data ? (
            <AlertCircle className="h-3 w-3 text-destructive" aria-label={t('usage.loadError')} />
          ) : data && data.event_count === 0 ? (
            <span className="text-sidebar-foreground/60">{t('usage.noUsageYet')}</span>
          ) : data ? (
            <CostValue
              fields={costFieldsFromSummary(data.cost)}
              eventCount={data.event_count}
              locale={language}
              t={t}
              showHint={false}
              levelClassName={LEVEL_TEXT_CLASS[level]}
            />
          ) : null}
        </div>
        {data && data.event_count > 0 ? (
          <Progress
            value={percent}
            indicatorClassName={LEVEL_INDICATOR_CLASS[level]}
            className="h-1.5"
            aria-label={t('usage.budgetLabel')}
          />
        ) : null}
      </div>
    )
  }

  return (
    <Card className={className}>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <CardTitle>{t('usage.costMeter')}</CardTitle>
            <CardDescription>{t('usage.costMeterDesc')}</CardDescription>
          </div>
          <Select value={filters.period} onValueChange={(value) => setPeriod(value as UsagePeriod)}>
            <SelectTrigger size="sm" aria-label={t('usage.periodLabel')} className="h-8 w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PERIOD_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {t(option.key)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {filters.period === 'session' && sessionStart && (
          <p className="text-xs text-muted-foreground">
            {t('usage.sessionHint', { time: formatDateTime(sessionStart, language) })}
          </p>
        )}
        {filterOptions && (
          <div className="flex flex-wrap gap-2 pt-2">
            <FilterSelect
              value={filters.model}
              onChange={setModel}
              options={filterOptions.models}
              allLabel={t('usage.allModels')}
              ariaLabel={t('usage.modelLabel')}
            />
            <FilterSelect
              value={filters.provider}
              onChange={setProvider}
              options={filterOptions.providers}
              allLabel={t('usage.allProviders')}
              ariaLabel={t('usage.providerLabel')}
            />
            <FilterSelect
              value={filters.taskType}
              onChange={setTaskType}
              options={filterOptions.task_types}
              allLabel={t('usage.allFunctions')}
              ariaLabel={t('usage.functionLabel')}
            />
          </div>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {isError ? (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <span>{t('usage.loadError')}</span>
              <Button type="button" variant="outline" size="sm" onClick={() => refetch()}>
                {t('common.retry')}
              </Button>
            </AlertDescription>
          </Alert>
        ) : isLoading && !data ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t('common.loading')}
          </div>
        ) : data && data.event_count === 0 ? (
          <p className="text-sm text-muted-foreground">{t('usage.noUsageYet')}</p>
        ) : data ? (
          <>
            <div className="space-y-2">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <CostValue
                  fields={costFieldsFromSummary(data.cost)}
                  eventCount={data.event_count}
                  locale={language}
                  t={t}
                  size="lg"
                  levelClassName={LEVEL_TEXT_CLASS[level]}
                />
                <span className="text-sm text-muted-foreground">
                  {t('usage.usedOfBudget', { budget: formatUsd(data.budget_usd, language) })}
                </span>
              </div>
              <Progress
                value={percent}
                indicatorClassName={LEVEL_INDICATOR_CLASS[level]}
                aria-label={t('usage.budgetLabel')}
              />
              <p className="text-xs text-muted-foreground">
                {t('usage.percentUsed', { percent: percent.toFixed(1) })}
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
              <div>
                <p className="text-muted-foreground">{t('usage.inputTokens')}</p>
                <p className="font-mono text-sm text-foreground">
                  {formatNumber(data.tokens.input, language)}
                </p>
              </div>
              <div>
                <p className="text-muted-foreground">{t('usage.outputTokens')}</p>
                <p className="font-mono text-sm text-foreground">
                  {formatNumber(data.tokens.output, language)}
                </p>
              </div>
              <div>
                <p className="text-muted-foreground">{t('usage.cachedTokens')}</p>
                <p className="font-mono text-sm text-foreground">
                  {data.tokens.cached_input_reported_events > 0
                    ? formatNumber(data.tokens.cached_input, language)
                    : t('usage.notReported')}
                </p>
              </div>
              <div>
                <p className="text-muted-foreground">{t('usage.embeddingTokens')}</p>
                <p className="font-mono text-sm text-foreground">
                  {data.tokens.embedding === null
                    ? t('usage.notTracked')
                    : formatNumber(data.tokens.embedding, language)}
                </p>
              </div>
            </div>

            <BreakdownList
              title={t('usage.byModel')}
              items={data.by_model}
              locale={language}
              t={t}
            />
            <BreakdownList
              title={t('usage.byFunction')}
              items={data.by_task_type}
              locale={language}
              t={t}
            />
            <BreakdownList
              title={t('usage.providerLabel')}
              items={data.by_provider}
              locale={language}
              t={t}
            />

            <p className="text-xs text-muted-foreground">
              {t('usage.updatedAt', { time: formatDateTime(data.generated_at, language) })}
            </p>
          </>
        ) : null}
      </CardContent>
    </Card>
  )
}
