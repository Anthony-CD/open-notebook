import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import { CostMeterWidget } from './CostMeterWidget'
import { useUsageSummary } from '@/lib/hooks/use-usage'
import type { UsageSummary } from '@/lib/types/usage'

// useTranslation is mocked globally in setup.ts (t returns the key string,
// language is 'en-US').

vi.mock('@/lib/hooks/use-usage', () => ({
  useUsageSummary: vi.fn(),
  getUsageSessionStart: vi.fn(() => '2026-01-01T00:00:00.000Z'),
}))

const mockUseUsageSummary = vi.mocked(useUsageSummary)

type UseUsageSummaryResult = ReturnType<typeof useUsageSummary>

function asResult(value: Partial<UseUsageSummaryResult>): UseUsageSummaryResult {
  return value as UseUsageSummaryResult
}

function makeSummary(overrides: Partial<UsageSummary> = {}): UsageSummary {
  return {
    event_count: 10,
    failed_count: 0,
    period: { preset: '30d', from: null, to: null },
    generated_at: '2026-01-02T12:00:00.000Z',
    tokens: {
      input: 1000,
      output: 500,
      cached_input: 0,
      cached_input_reported_events: 0,
      embedding: null,
      embeddings_tracked: false,
    },
    cost: {
      known_usd: 3.4567,
      known_cost_events: 10,
      free_events: 0,
      unknown_cost_events: 0,
      unknown_cost_input_tokens: 0,
      unknown_cost_output_tokens: 0,
      is_complete: true,
    },
    budget_usd: 10,
    by_provider: [],
    by_model: [],
    by_task_type: [],
    filter_options: { models: [], providers: [], task_types: [] },
    applied_filters: {},
    pricing_table_version: 'v1',
    ...overrides,
  }
}

describe('CostMeterWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows a loading state while the summary is fetching', () => {
    mockUseUsageSummary.mockReturnValue(
      asResult({ data: undefined, isLoading: true, isError: false, refetch: vi.fn() }),
    )
    render(<CostMeterWidget />)
    expect(screen.getByText('common.loading')).toBeInTheDocument()
  })

  it('shows an error state with a working retry', () => {
    const refetch = vi.fn()
    mockUseUsageSummary.mockReturnValue(
      asResult({ data: undefined, isLoading: false, isError: true, refetch }),
    )
    render(<CostMeterWidget />)
    expect(screen.getByText('usage.loadError')).toBeInTheDocument()
    fireEvent.click(screen.getByText('common.retry'))
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('shows the true empty state when there is no usage yet', () => {
    mockUseUsageSummary.mockReturnValue(
      asResult({
        data: makeSummary({ event_count: 0 }),
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    )
    render(<CostMeterWidget />)
    expect(screen.getByText('usage.noUsageYet')).toBeInTheDocument()
  })

  it('renders a known, complete cost as a formatted dollar amount', () => {
    mockUseUsageSummary.mockReturnValue(
      asResult({ data: makeSummary(), isLoading: false, isError: false, refetch: vi.fn() }),
    )
    render(<CostMeterWidget />)
    expect(screen.getByText('$3.46')).toBeInTheDocument()
    // Never shown when the cost is known and complete.
    expect(screen.queryByText('usage.costUnavailable')).not.toBeInTheDocument()
    expect(screen.queryByText('usage.freeLabel')).not.toBeInTheDocument()
  })

  it('shows "< $0.01" instead of a misleading $0.00 for a tiny known cost', () => {
    mockUseUsageSummary.mockReturnValue(
      asResult({
        data: makeSummary({ cost: { ...makeSummary().cost, known_usd: 0.002 } }),
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    )
    render(<CostMeterWidget />)
    expect(screen.getByText('usage.lessThan')).toBeInTheDocument()
  })

  it('labels an all-free period distinctly from a real zero cost', () => {
    mockUseUsageSummary.mockReturnValue(
      asResult({
        data: makeSummary({
          cost: {
            known_usd: 0,
            known_cost_events: 0,
            free_events: 10,
            unknown_cost_events: 0,
            unknown_cost_input_tokens: 0,
            unknown_cost_output_tokens: 0,
            is_complete: true,
          },
        }),
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    )
    render(<CostMeterWidget />)
    expect(screen.getByText('usage.freeLabel')).toBeInTheDocument()
    expect(screen.getByText('usage.allFreeHint')).toBeInTheDocument()
  })

  it('never claims completeness when some calls have an unknown price', () => {
    mockUseUsageSummary.mockReturnValue(
      asResult({
        data: makeSummary({
          cost: {
            known_usd: 1.2,
            known_cost_events: 7,
            free_events: 0,
            unknown_cost_events: 3,
            unknown_cost_input_tokens: 100,
            unknown_cost_output_tokens: 50,
            is_complete: false,
          },
        }),
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    )
    render(<CostMeterWidget />)
    expect(screen.getByText('usage.costAtLeast')).toBeInTheDocument()
    expect(screen.getByText(/usage\.unknownCalls/)).toBeInTheDocument()
    expect(screen.getByText(/usage\.costPartialHint/)).toBeInTheDocument()
  })

  it('shows cost-unavailable (not $0.00) when every call has an unknown price', () => {
    mockUseUsageSummary.mockReturnValue(
      asResult({
        data: makeSummary({
          cost: {
            known_usd: 0,
            known_cost_events: 0,
            free_events: 0,
            unknown_cost_events: 10,
            unknown_cost_input_tokens: 500,
            unknown_cost_output_tokens: 200,
            is_complete: false,
          },
        }),
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    )
    render(<CostMeterWidget />)
    expect(screen.getByText('usage.costUnavailable')).toBeInTheDocument()
    expect(screen.getByText('usage.costUnavailableHint')).toBeInTheDocument()
  })

  it('falls back to notReported/notTracked for cache and embedding tokens', () => {
    mockUseUsageSummary.mockReturnValue(
      asResult({ data: makeSummary(), isLoading: false, isError: false, refetch: vi.fn() }),
    )
    render(<CostMeterWidget />)
    expect(screen.getByText('usage.notReported')).toBeInTheDocument()
    expect(screen.getByText('usage.notTracked')).toBeInTheDocument()
  })

  it('reports cached and embedding token counts once providers report them', () => {
    mockUseUsageSummary.mockReturnValue(
      asResult({
        data: makeSummary({
          tokens: {
            input: 1000,
            output: 500,
            cached_input: 250,
            cached_input_reported_events: 4,
            embedding: 0,
            embeddings_tracked: true,
          },
        }),
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    )
    render(<CostMeterWidget />)
    expect(screen.getByText('250')).toBeInTheDocument()
    // Tracked but genuinely zero this period - distinct from "not tracked".
    expect(screen.queryByText('usage.notTracked')).not.toBeInTheDocument()
  })

  it('renders model/task breakdown rows sorted by known cost, descending', () => {
    mockUseUsageSummary.mockReturnValue(
      asResult({
        data: makeSummary({
          by_model: [
            {
              key: 'cheap-model',
              event_count: 5,
              failed_count: 0,
              input_tokens: 10,
              output_tokens: 10,
              cached_input_tokens: 0,
              cost_known_usd: 0.5,
              known_cost_events: 5,
              free_events: 0,
              unknown_cost_events: 0,
            },
            {
              key: 'expensive-model',
              event_count: 5,
              failed_count: 1,
              input_tokens: 10,
              output_tokens: 10,
              cached_input_tokens: 0,
              cost_known_usd: 2.9,
              known_cost_events: 5,
              free_events: 0,
              unknown_cost_events: 0,
            },
          ],
        }),
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    )
    render(<CostMeterWidget />)
    const rows = screen.getAllByText(/-model$/)
    expect(rows[0]).toHaveTextContent('expensive-model')
    expect(rows[1]).toHaveTextContent('cheap-model')
    expect(screen.getByText(/usage\.failedCalls/)).toBeInTheDocument()
  })

  it('only renders a filter dropdown when its options are non-empty', () => {
    mockUseUsageSummary.mockReturnValue(
      asResult({
        data: makeSummary({
          filter_options: { models: ['gpt-4o'], providers: [], task_types: [] },
        }),
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    )
    render(<CostMeterWidget />)
    // Model filter trigger present (aria-label from usage.modelLabel key).
    expect(screen.getByLabelText('usage.modelLabel')).toBeInTheDocument()
    // Provider/function filters absent - no options for them.
    expect(screen.queryByLabelText('usage.providerLabel')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('usage.functionLabel')).not.toBeInTheDocument()
  })

  it('renders a compact single-line variant', () => {
    mockUseUsageSummary.mockReturnValue(
      asResult({ data: makeSummary(), isLoading: false, isError: false, refetch: vi.fn() }),
    )
    render(<CostMeterWidget compact />)
    expect(screen.getByText('$3.46')).toBeInTheDocument()
    // Full-card-only sections are absent in compact mode.
    expect(screen.queryByText('usage.byModel')).not.toBeInTheDocument()
  })
})
