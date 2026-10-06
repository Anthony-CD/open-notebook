import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'

import { DeleteAllDataDialog } from './DeleteAllDataDialog'
import { useDeleteAllData } from '@/lib/hooks/use-privacy'

// useTranslation is mocked globally in setup.ts (t returns the key string,
// so t('privacy.deleteConfirmLabel', {phrase}) renders as the raw key -
// the phrase itself still comes through via the real
// PRIVACY_DELETE_CONFIRMATION_PHRASE constant used by the component).

vi.mock('@/lib/hooks/use-privacy', () => ({
  useDeleteAllData: vi.fn(),
}))

const mockUseDeleteAllData = vi.mocked(useDeleteAllData)

function mockMutation(overrides: Partial<ReturnType<typeof useDeleteAllData>> = {}) {
  return {
    mutate: vi.fn(),
    mutateAsync: vi.fn().mockResolvedValue({ message: 'ok', deleted: {} }),
    isPending: false,
    ...overrides,
  } as unknown as ReturnType<typeof useDeleteAllData>
}

describe('DeleteAllDataDialog', () => {
  it('keeps the confirm button disabled until the exact phrase is typed', () => {
    mockUseDeleteAllData.mockReturnValue(mockMutation())

    render(<DeleteAllDataDialog open onOpenChange={vi.fn()} />)

    const confirmButton = screen.getByRole('button', { name: 'privacy.deleteConfirmButton' })
    expect(confirmButton).toBeDisabled()

    const input = screen.getByLabelText('privacy.deleteConfirmLabel', { exact: false })
    fireEvent.change(input, { target: { value: 'delete all my data' } })
    expect(confirmButton).toBeDisabled()
    expect(screen.getByText('privacy.deleteConfirmMismatch')).toBeInTheDocument()

    fireEvent.change(input, { target: { value: 'DELETE ALL MY DATA' } })
    expect(confirmButton).not.toBeDisabled()
  })

  it('calls the delete mutation with the typed phrase once confirmed', () => {
    const mutateAsync = vi.fn().mockResolvedValue({ message: 'ok', deleted: {} })
    mockUseDeleteAllData.mockReturnValue(mockMutation({ mutateAsync }))
    const onOpenChange = vi.fn()

    render(<DeleteAllDataDialog open onOpenChange={onOpenChange} />)

    const input = screen.getByLabelText('privacy.deleteConfirmLabel', { exact: false })
    fireEvent.change(input, { target: { value: 'DELETE ALL MY DATA' } })

    const confirmButton = screen.getByRole('button', { name: 'privacy.deleteConfirmButton' })
    fireEvent.click(confirmButton)

    expect(mutateAsync).toHaveBeenCalledWith('DELETE ALL MY DATA')
  })
})
