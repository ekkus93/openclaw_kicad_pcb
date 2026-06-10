import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { JobPage } from '../routes/JobPage'
import { makeJob } from './fixtures'

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
