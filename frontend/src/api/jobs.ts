import type { JobDetail, JobSummary } from '../types'
import { jsonBody, requestJson } from './client'

export function getJobs(): Promise<JobSummary[]> {
  return requestJson('/api/jobs')
}

export function getJob(jobId: string): Promise<JobDetail> {
  return requestJson(`/api/jobs/${encodeURIComponent(jobId)}`)
}

export function createJobFromNetlist(payload: {
  project_name: string
  netlist_json: Record<string, unknown>
  symbols_dir?: string | null
  validation?: string
  auto_fix?: boolean
}): Promise<JobDetail> {
  return requestJson('/api/jobs/from-netlist', {
    method: 'POST',
    body: jsonBody(payload),
  })
}
