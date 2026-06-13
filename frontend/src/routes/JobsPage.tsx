import { Link } from 'react-router-dom'

import { useJobsQuery } from '../queries/jobQueries'
import type { JobStatus } from '../types'
import { joinClasses, formatDate } from '../utils'
import { StatusPill } from '../components/StatusPill'
import {
  bannerBaseClass,
  buttonRowClass,
  eyebrowClass,
  headingGroupClass,
  heroLeadClass,
  pageStackClass,
  panelAccentClass,
  panelSoftClass,
  spinnerClass,
} from '../styles/designTokens'

function statusTone(status: JobStatus | string): 'neutral' | 'active' | 'success' | 'warning' | 'error' {
  if (status === 'queued' || status === 'running') return 'active'
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'error'
  if (status === 'cancelled') return 'neutral'
  return 'neutral'
}

function statusLabel(status: JobStatus | string): string {
  const labels: Record<string, string> = {
    queued: 'Queued',
    running: 'Running',
    succeeded: 'Succeeded',
    failed: 'Failed',
    cancelled: 'Cancelled',
  }
  return labels[status] ?? String(status).replaceAll('_', ' ')
}

// ─── JobsPage ─────────────────────────────────────────────────────────────────

export function JobsPage() {
  const { data: jobs, isLoading, error } = useJobsQuery()
  const hasActiveJobs = jobs?.some((j) => j.status === 'queued' || j.status === 'running') ?? false

  return (
    <div className={pageStackClass}>
      <section className={panelAccentClass}>
        <div className={headingGroupClass}>
          <p className={eyebrowClass}>Jobs</p>
          <div className="flex flex-wrap items-center gap-3">
            <h1>Recent Jobs</h1>
            {hasActiveJobs ? (
              <span className="flex items-center gap-1.5 rounded-full border border-[rgba(22,93,143,0.2)] bg-[rgba(22,93,143,0.08)] px-2.5 py-0.5 text-[0.75rem] font-semibold text-[#0d4c74]">
                <span className={spinnerClass} aria-hidden="true"></span>
                Live
              </span>
            ) : null}
          </div>
          <p className={heroLeadClass}>
            Browse previously generated KiCad projects. Click a job to inspect its artifacts,
            warnings, and build diagnostics.
          </p>
        </div>
      </section>

      {isLoading ? (
        <div className={joinClasses(bannerBaseClass, 'border-[rgba(22,93,143,0.2)] bg-[rgba(22,93,143,0.1)] text-[#0d4c74] mt-2')}>
          <span className={spinnerClass} aria-hidden="true"></span>
          <strong>Loading jobs…</strong>
        </div>
      ) : null}

      {error ? (
        <div className={joinClasses(bannerBaseClass, 'border-[rgba(154,45,40,0.18)] bg-[rgba(154,45,40,0.09)] text-[var(--error)] mt-2')}>
          <strong>
            {error instanceof Error ? error.message : 'Failed to load jobs.'}
          </strong>
        </div>
      ) : null}

      {!isLoading && jobs && jobs.length === 0 ? (
        <section className={panelSoftClass}>
          <p className="text-[var(--muted)]">No jobs yet. Use the Wizard or Circuit IR JSON page to generate a project.</p>
        </section>
      ) : null}

      {jobs && jobs.length > 0 ? (
        <div className="grid gap-3">
          {jobs.map((job) => (
            <Link
              key={job.id}
              to={`/jobs/${job.id}`}
              className="grid gap-3 rounded-[22px] border border-[rgba(88,63,39,0.14)] bg-[linear-gradient(180deg,rgba(255,255,255,0.82),rgba(255,248,235,0.72))] p-4 shadow-[0_8px_24px_rgba(71,43,19,0.07)] no-underline transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:shadow-[0_14px_32px_rgba(71,43,19,0.12)] sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:p-5"
            >
              <div className="grid gap-1">
                <p className="text-[1rem] font-semibold text-[var(--text)]">{job.project_name}</p>
                <div className="flex flex-wrap items-center gap-3">
                  <StatusPill tone={statusTone(job.status)}>{statusLabel(job.status)}</StatusPill>
                  <span className="text-[0.82rem] text-[var(--muted)]">
                    Updated {formatDate(job.updated_at)}
                  </span>
                </div>
              </div>
              <span className="text-[0.78rem] font-mono text-[var(--muted)] sm:text-right">
                {job.id}
              </span>
            </Link>
          ))}
        </div>
      ) : null}

      <div className={buttonRowClass}>
        <Link
          to="/"
          className="rounded-full border border-[rgba(24,75,69,0.12)] bg-[rgba(255,255,255,0.72)] px-[1.2rem] py-[0.8rem] font-semibold no-underline text-[var(--accent)] transition-[transform,opacity,box-shadow,background-color] duration-150 hover:-translate-y-px hover:bg-[rgba(255,255,255,0.9)]"
        >
          ← Back to Home
        </Link>
      </div>
    </div>
  )
}
