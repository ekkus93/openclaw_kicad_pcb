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

export function useJobQuery(jobId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.job(jobId ?? ''),
    queryFn: () => api.getJob(jobId!),
    enabled: Boolean(jobId),
    // Poll only while the job is in an active/running state
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status && ACTIVE_STATUSES.includes(status as JobStatus)) {
        return 3_000
      }
      return false
    },
  })
}
