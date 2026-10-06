import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'

import { ContinueStudyingCard } from './ContinueStudyingCard'
import { useStudyMetrics } from '@/lib/hooks/use-study'

vi.mock('@/lib/hooks/use-study', () => ({
  useStudyMetrics: vi.fn(),
}))

const mockUseStudyMetrics = vi.mocked(useStudyMetrics)

type MetricsResult = ReturnType<typeof useStudyMetrics>

function asResult(value: Partial<MetricsResult>) {
  return value as MetricsResult
}

describe('ContinueStudyingCard', () => {
  it('shows the honest "no study sets" message when nothing has ever been generated', () => {
    mockUseStudyMetrics.mockReturnValue(
      asResult({
        data: { due_today: 0, accuracy_pct: null, reviewed_cards: 0, total_cards: 0, weak_sources: [] },
        isLoading: false,
        isError: false,
      })
    )

    render(<ContinueStudyingCard />)

    expect(screen.getByText('home.noStudySets')).toBeInTheDocument()
  })

  it('shows the due-today count when cards are due', () => {
    mockUseStudyMetrics.mockReturnValue(
      asResult({
        data: { due_today: 3, accuracy_pct: 80, reviewed_cards: 10, total_cards: 12, weak_sources: [] },
        isLoading: false,
        isError: false,
      })
    )

    render(<ContinueStudyingCard />)

    expect(screen.getByText('home.dueToday')).toBeInTheDocument()
    expect(screen.getByText('home.recallAccuracy')).toBeInTheDocument()
  })

  it('shows "all caught up" when there are study sets but nothing due', () => {
    mockUseStudyMetrics.mockReturnValue(
      asResult({
        data: { due_today: 0, accuracy_pct: 100, reviewed_cards: 5, total_cards: 5, weak_sources: [] },
        isLoading: false,
        isError: false,
      })
    )

    render(<ContinueStudyingCard />)

    expect(screen.getByText('home.allCaughtUp')).toBeInTheDocument()
  })
})
