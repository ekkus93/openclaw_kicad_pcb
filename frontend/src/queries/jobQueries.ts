import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { queryKeys } from '../queryKeys'
import type { JobStatus } from '../types'

const ACTIVE_STATUSES: JobStatus[] = ['queued', 'running']

export function useJobsQuery() {
  return useQuery({
    queryKey: queryKeys.jobs,
    queryFn: () => api.getJobs(),
  })
}

// Polls every 3 s while a job is queued or running, then stops.
// The current backend runs project generation synchronously, so polling
// typically fires once or twice before the job reaches a terminal state.
// A future async worker model would keep the job in "queued"/"running"
// longer, making this polling behaviour more visible.
export function useJobQuery(jobId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.job(jobId ?? ''),
    queryFn: () => api.getJob(jobId!),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status && ACTIVE_STATUSES.includes(status as JobStatus)) {
        return 3_000
      }
      return false
    },
  })
}
