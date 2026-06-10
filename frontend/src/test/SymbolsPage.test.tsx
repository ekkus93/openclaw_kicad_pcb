import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { screen, act, fireEvent } from '@testing-library/react'

import { SymbolsPage } from '../routes/SymbolsPage'
import { renderWithProviders } from './renderWithProviders'

vi.mock('../queries/symbolQueries', () => ({
  useSymbolSearchQuery: vi.fn(() => ({ data: undefined, isLoading: false, error: null })),
}))

import { useSymbolSearchQuery } from '../queries/symbolQueries'

describe('SymbolsPage — accessibility roles (task 2)', () => {
  it('exposes searching state via role="status"', () => {
    vi.mocked(useSymbolSearchQuery).mockReturnValueOnce({ data: undefined, isLoading: true, error: null } as ReturnType<typeof useSymbolSearchQuery>)
    renderWithProviders(<SymbolsPage />)
    const status = screen.getByRole('status')
    expect(status.textContent).toMatch(/searching/i)
  })

  it('exposes error state via role="alert"', () => {
    vi.mocked(useSymbolSearchQuery).mockReturnValueOnce({ data: undefined, isLoading: false, error: new Error('Library unavailable') } as ReturnType<typeof useSymbolSearchQuery>)
    renderWithProviders(<SymbolsPage />)
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('Library unavailable')
  })
})

describe('SymbolsPage — search debounce (task 4.7)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(useSymbolSearchQuery).mockClear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not pass the typed value to the query before debounce fires', () => {
    renderWithProviders(<SymbolsPage />)
    const input = screen.getByRole('searchbox')

    // Simulate typing — each change clears the previous timer and sets a new one
    fireEvent.change(input, { target: { value: 'a' } })
    fireEvent.change(input, { target: { value: 'ab' } })
    fireEvent.change(input, { target: { value: 'abc' } })

    // Before debounce fires, debouncedQuery is still '' so query hook is called with ''
    const argsBeforeTimer = vi.mocked(useSymbolSearchQuery).mock.calls.map(([q]) => q)
    expect(argsBeforeTimer).not.toContain('a')
    expect(argsBeforeTimer).not.toContain('ab')
    expect(argsBeforeTimer).not.toContain('abc')
  })

  it('passes the final query string after the debounce interval', () => {
    renderWithProviders(<SymbolsPage />)
    const input = screen.getByRole('searchbox')

    fireEvent.change(input, { target: { value: 'abc' } })
    vi.mocked(useSymbolSearchQuery).mockClear()

    act(() => { vi.advanceTimersByTime(300) })

    const argsAfterTimer = vi.mocked(useSymbolSearchQuery).mock.calls.map(([q]) => q)
    expect(argsAfterTimer).toContain('abc')
    expect(argsAfterTimer).not.toContain('a')
    expect(argsAfterTimer).not.toContain('ab')
  })

  it('only passes the last value when multiple changes arrive before the interval', () => {
    renderWithProviders(<SymbolsPage />)
    const input = screen.getByRole('searchbox')

    // Each change resets the timer — only 'xyz' should fire
    fireEvent.change(input, { target: { value: 'x' } })
    fireEvent.change(input, { target: { value: 'xy' } })
    fireEvent.change(input, { target: { value: 'xyz' } })

    vi.mocked(useSymbolSearchQuery).mockClear()
    act(() => { vi.advanceTimersByTime(300) })

    const args = vi.mocked(useSymbolSearchQuery).mock.calls.map(([q]) => q)
    expect(args).toContain('xyz')
    expect(args).not.toContain('x')
    expect(args).not.toContain('xy')
  })

  it('resets debouncedQuery immediately when input is cleared', () => {
    renderWithProviders(<SymbolsPage />)
    const input = screen.getByRole('searchbox')

    fireEvent.change(input, { target: { value: 'abc' } })
    act(() => { vi.advanceTimersByTime(300) })

    vi.mocked(useSymbolSearchQuery).mockClear()
    fireEvent.change(input, { target: { value: '' } })

    // Clearing resets debouncedQuery to '' immediately (no timer)
    const args = vi.mocked(useSymbolSearchQuery).mock.calls.map(([q]) => q)
    expect(args).toContain('')
  })

  it('does not fire a pending timer after unmount', () => {
    const { unmount } = renderWithProviders(<SymbolsPage />)
    const input = screen.getByRole('searchbox')

    fireEvent.change(input, { target: { value: 'ab' } })

    // Unmount before the 300 ms debounce fires — useEffect cleanup clears the timer
    unmount()

    // Advancing timers after unmount should not trigger state updates or warnings
    act(() => { vi.advanceTimersByTime(400) })
  })
})
