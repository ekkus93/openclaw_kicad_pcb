import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { JobPage } from '../routes/JobPage'
import { makeJob } from './fixtures'

// stub navigator.clipboard before tests that render CopyButton
Object.defineProperty(navigator, 'clipboard', {
  value: { writeText: vi.fn().mockResolvedValue(undefined) },
  configurable: true,
})

vi.mock('../queries/jobQueries', () => ({
  useJobQuery: vi.fn(),
  useJobsQuery: vi.fn(() => ({ data: [], isLoading: false, error: null })),
}))

import { useJobQuery } from '../queries/jobQueries'

function renderJobPage(jobId: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/jobs/${jobId}`]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// ─── Navigation ───────────────────────────────────────────────────────────────

describe('JobPage — navigation links', () => {
  it('always shows ← All Jobs link', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: makeJob({ status: 'succeeded' }),
      isLoading: false,
      error: null,
      isFetching: false,
    } as ReturnType<typeof useJobQuery>)
    renderJobPage('test-job-456')
    expect(screen.getByRole('link', { name: '← All Jobs' })).toBeInTheDocument()
  })

  it('shows Try from Circuit IR JSON on failure without fromSessionId', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: makeJob({ status: 'failed', error: { message: 'oops' } }),
      isLoading: false,
      error: null,
      isFetching: false,
    } as ReturnType<typeof useJobQuery>)
    renderJobPage('test-job-456')
    expect(screen.getByRole('link', { name: 'Try from Circuit IR JSON' })).toBeInTheDocument()
  })

  it('does not show Try from Circuit IR JSON on success', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: makeJob({ status: 'succeeded' }),
      isLoading: false,
      error: null,
      isFetching: false,
    } as ReturnType<typeof useJobQuery>)
    renderJobPage('test-job-456')
    expect(screen.queryByRole('link', { name: 'Try from Circuit IR JSON' })).not.toBeInTheDocument()
  })

  it('shows Back to session when fromSessionId present', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: makeJob({ status: 'failed', error: { message: 'oops' } }),
      isLoading: false,
      error: null,
      isFetching: false,
    } as ReturnType<typeof useJobQuery>)
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/jobs/test-job-456?from=sess-abc']}>
          <Routes>
            <Route path="/jobs/:jobId" element={<JobPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(screen.getByRole('link', { name: '← Back to session' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Try from Circuit IR JSON' })).not.toBeInTheDocument()
  })
})

// ─── Error surfacing ──────────────────────────────────────────────────────────

describe('JobPage — error message surfacing', () => {
  it('shows error message in status card for failed jobs', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: makeJob({ status: 'failed', error: { message: 'Symbol library not found.' } }),
      isLoading: false,
      error: null,
      isFetching: false,
    } as ReturnType<typeof useJobQuery>)
    renderJobPage('test-job-456')
    expect(screen.getByText('Symbol library not found.')).toBeInTheDocument()
  })

  it('does not show error message when job succeeded', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: makeJob({ status: 'succeeded' }),
      isLoading: false,
      error: null,
      isFetching: false,
    } as ReturnType<typeof useJobQuery>)
    renderJobPage('test-job-456')
    expect(screen.queryByText('Symbol library not found.')).not.toBeInTheDocument()
  })

  it('does not crash when failed job has no message in error payload', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: makeJob({ status: 'failed', error: { code: 'ERR' } }),
      isLoading: false,
      error: null,
      isFetching: false,
    } as ReturnType<typeof useJobQuery>)
    renderJobPage('test-job-456')
    expect(screen.getByText('Generation failed. Review the error and artifacts below.')).toBeInTheDocument()
  })
})

// ─── Polling indicator ────────────────────────────────────────────────────────

describe('JobPage — polling indicator (task 4.6)', () => {
  it('shows Checking for updates when job is running and isFetching is true', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: makeJob({ status: 'running' }),
      isLoading: false,
      error: null,
      isFetching: true,
    } as ReturnType<typeof useJobQuery>)

    renderJobPage('test-job-456')
    expect(screen.getByText('Checking for updates…')).toBeInTheDocument()
  })

  it('shows Checking for updates when job is queued and isFetching is true', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: makeJob({ status: 'queued' }),
      isLoading: false,
      error: null,
      isFetching: true,
    } as ReturnType<typeof useJobQuery>)

    renderJobPage('test-job-456')
    expect(screen.getByText('Checking for updates…')).toBeInTheDocument()
  })

  it('does not show Checking for updates when job is running but not fetching', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: makeJob({ status: 'running' }),
      isLoading: false,
      error: null,
      isFetching: false,
    } as ReturnType<typeof useJobQuery>)

    renderJobPage('test-job-456')
    expect(screen.queryByText('Checking for updates…')).not.toBeInTheDocument()
  })

  it('does not show Checking for updates when job has succeeded', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: makeJob({ status: 'succeeded' }),
      isLoading: false,
      error: null,
      isFetching: true,
    } as ReturnType<typeof useJobQuery>)

    renderJobPage('test-job-456')
    expect(screen.queryByText('Checking for updates…')).not.toBeInTheDocument()
  })

  it('does not show Checking for updates when job has failed', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: makeJob({ status: 'failed', error: { message: 'Something went wrong' } }),
      isLoading: false,
      error: null,
      isFetching: true,
    } as ReturnType<typeof useJobQuery>)

    renderJobPage('test-job-456')
    expect(screen.queryByText('Checking for updates…')).not.toBeInTheDocument()
  })

  it('shows initial loading state when isLoading is true', () => {
    vi.mocked(useJobQuery).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
      isFetching: true,
    } as ReturnType<typeof useJobQuery>)

    renderJobPage('test-job-456')
    expect(screen.getByText('Loading job detail…')).toBeInTheDocument()
    expect(screen.queryByText('Checking for updates…')).not.toBeInTheDocument()
  })
})
