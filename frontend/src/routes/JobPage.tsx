import { useEffect } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { useJobQuery } from '../queries/jobQueries'
import { joinClasses, formatDate, getErrorMessage, statusBannerToneClass } from '../utils'
import type { StatusTone } from '../utils'
import { StatusPill } from '../components/StatusPill'
import { WarningsPanel } from '../components/WarningCard'
import { DisclosurePanel, BuildSummaryPanel } from '../components/DisclosurePanel'
import type { DiagnosticsPayload } from '../components/DisclosurePanel'
import {
  bannerBaseClass,
  buttonPrimaryClass,
  buttonRowClass,
  buttonSecondaryClass,
  dashboardGridClass,
  detailListGridClass,
  emptyCopyClass,
  eyebrowClass,
  headingGroupClass,
  heroCardClass,
  heroLeadClass,
  heroPanelClass,
  heroStatCardClass,
  heroStatGridClass,
  jsonBlockClass,
  mutedCopyClass,
  pageStackClass,
  panelSoftClass,
  spinnerClass,
  stackColumnClass,
} from '../styles/designTokens'

// ─── Utility functions ────────────────────────────────────────────────────────

function formatJson(payload: unknown): string {
  return JSON.stringify(payload, null, 2)
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return {}
}

// ─── Status helpers ───────────────────────────────────────────────────────────

function statusTone(status: string): StatusTone {
  if (status === 'queued' || status === 'running') return 'active'
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'error'
  if (status === 'cancelled') return 'neutral'
  return 'neutral'
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: 'Queued',
    running: 'Running',
    succeeded: 'Succeeded',
    failed: 'Failed',
    cancelled: 'Cancelled',
  }
  return labels[status] ?? String(status).replaceAll('_', ' ')
}

function MetricCard({ label, value, tone = 'warm' }: { label: string; value: string; tone?: 'warm' | 'cool' | 'neutral' }) {
  const toneClass = {
    warm: 'bg-[linear-gradient(180deg,rgba(255,255,255,0.72),rgba(255,242,224,0.64))]',
    cool: 'bg-[linear-gradient(180deg,rgba(244,255,253,0.82),rgba(222,244,240,0.68))]',
    neutral: 'bg-[linear-gradient(180deg,rgba(255,255,255,0.68),rgba(244,239,230,0.7))]',
  }[tone]
  return (
    <div className={joinClasses(heroStatCardClass, toneClass)}>
      <div className="text-[0.73rem] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">{label}</div>
      <div className="mt-1 text-lg font-semibold text-[var(--text)]">{value}</div>
    </div>
  )
}

// ─── JsonPanel ────────────────────────────────────────────────────────────────

function JsonPanel({ title, payload }: { title: string; payload: unknown }) {
  return (
    <section className={panelSoftClass}>
      <div className={headingGroupClass}>
        <h2>{title}</h2>
      </div>
      <pre className={jsonBlockClass}>{formatJson(payload)}</pre>
    </section>
  )
}

// ─── NotFoundScreen ───────────────────────────────────────────────────────────

function NotFoundScreen({ heading, message }: { heading: string; message?: string }) {
  return (
    <div className={pageStackClass}>
      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h1>{heading}</h1>
          {message ? <p className={mutedCopyClass}>{message}</p> : null}
        </div>
        <div className={buttonRowClass}>
          <Link className={buttonPrimaryClass} to="/jobs">
            ← Back to Jobs
          </Link>
        </div>
      </section>
    </div>
  )
}

// ─── JobPage ──────────────────────────────────────────────────────────────────

export function JobPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const [searchParams] = useSearchParams()
  const fromSessionId = searchParams.get('from')

  const { data: job, isLoading, error, isFetching } = useJobQuery(jobId)
  const isInProgress = job?.status === 'queued' || job?.status === 'running'

  useEffect(() => {
    const base = 'KiCad PCB Web App'
    if (job) {
      document.title = `${job.project_name} — Job — ${base}`
    } else {
      document.title = `Job — ${base}`
    }
  }, [job])

  if (!jobId) {
    return <NotFoundScreen heading="Job not found" message="No job ID was provided." />
  }

  if (isLoading) {
    return (
      <div role="status" aria-live="polite" className={joinClasses(bannerBaseClass, statusBannerToneClass('active'), 'mt-4')}>
        <span className={spinnerClass} aria-hidden="true"></span>
        <strong>Loading job detail…</strong>
      </div>
    )
  }

  if (error || !job) {
    return (
      <NotFoundScreen
        heading="Job not found"
        message={getErrorMessage(error) !== 'Unexpected error.' ? getErrorMessage(error) : 'This job does not exist or could not be loaded.'}
      />
    )
  }

  const result = asRecord(job.result)
  const warnings = Array.isArray(result.warnings) ? result.warnings : []
  const diagnostics = result.generated_schematic_diagnostics ?? null
  const isFailed = job.status === 'failed'
  const hasMetrics = !isFailed && (result.component_count != null || result.net_count != null)

  return (
    <div className={pageStackClass}>
      {fromSessionId ? (
        <div className="flex items-center gap-3">
          <Link className={buttonSecondaryClass} to={`/wizard/${fromSessionId}/generate`}>
            ← Back to session
          </Link>
        </div>
      ) : null}

      <section className={heroPanelClass}>
        <div>
          <p className={eyebrowClass}>Job {job.id}</p>
          <h1>{job.project_name}</h1>
          <p className={heroLeadClass}>
            {isFailed
              ? 'Generation failed. Review the error and artifacts below.'
              : 'Inspect the generation result, artifacts, and warnings.'}
          </p>
          {hasMetrics ? (
            <div className={heroStatGridClass}>
              <MetricCard label="Artifacts" tone="warm" value={String(job.artifacts.length)} />
              <MetricCard label="Components" tone="cool" value={String(result.component_count ?? '—')} />
              <MetricCard label="Nets" tone="neutral" value={String(result.net_count ?? '—')} />
            </div>
          ) : (
            <div className={heroStatGridClass}>
              <MetricCard label="Artifacts" tone="warm" value={String(job.artifacts.length)} />
            </div>
          )}
        </div>
        <div className={heroCardClass}>
          <p className={eyebrowClass}>Status</p>
          <StatusPill tone={statusTone(job.status)}>{statusLabel(job.status)}</StatusPill>
          <span className={mutedCopyClass}>{formatDate(job.updated_at)}</span>
          {isInProgress && isFetching ? (
            <span role="status" aria-live="polite" className="flex items-center gap-1.5 text-[0.78rem] text-[var(--muted)]">
              <span className={spinnerClass} aria-hidden="true"></span>
              Checking for updates…
            </span>
          ) : null}
          <p className="text-sm leading-6 text-[var(--muted)]">
            {isFailed
              ? 'Review the error payload below to understand what went wrong.'
              : 'Review the artifact set first, then check warnings for any generation issues.'}
          </p>
        </div>
      </section>

      {job.artifacts.includes('schematic_preview.png') ? (
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Schematic Preview</h2>
            <p className={mutedCopyClass}>
              Generated from{' '}
              <code className="rounded bg-[rgba(88,63,39,0.08)] px-1 py-0.5 text-[0.85rem]">
                OpenClaw_Managed.kicad_sch
              </code>
            </p>
          </div>
          <img
            src={`/api/jobs/${job.id}/artifacts/schematic_preview.png`}
            alt="Generated schematic preview"
            className="w-full rounded-[18px] border border-[rgba(88,63,39,0.1)]"
            style={{ background: '#fff' }}
          />
        </section>
      ) : null}

      <div className={dashboardGridClass}>
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Artifacts</h2>
          </div>
          {job.artifacts.length ? (
            <div className={buttonRowClass}>
              {job.artifacts
                .filter((a) => a !== 'schematic_preview.png' && !a.endsWith('.svg'))
                .map((artifact) => (
                  <a key={artifact} className={buttonSecondaryClass} href={`/api/jobs/${job.id}/artifacts/${artifact}`}>
                    {artifact}
                  </a>
                ))}
            </div>
          ) : (
            <p className={emptyCopyClass}>No artifacts — generation did not complete.</p>
          )}
        </section>
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Summary</h2>
          </div>
          <dl className={detailListGridClass}>
            <dt className={mutedCopyClass}>Created</dt>
            <dd>{formatDate(job.created_at)}</dd>
            <dt className={mutedCopyClass}>Updated</dt>
            <dd>{formatDate(job.updated_at)}</dd>
            {!isFailed ? (
              <>
                <dt className={mutedCopyClass}>Components</dt>
                <dd>{String(result.component_count ?? '—')}</dd>
                <dt className={mutedCopyClass}>Nets</dt>
                <dd>{String(result.net_count ?? '—')}</dd>
              </>
            ) : null}
          </dl>
        </section>
      </div>

      {warnings.length > 0 ? <WarningsPanel warnings={warnings} /> : null}

      {job.error ? <JsonPanel title="Error" payload={job.error} /> : null}

      {diagnostics ? <BuildSummaryPanel diagnostics={diagnostics as DiagnosticsPayload} /> : null}

      <DisclosurePanel title="Developer Details">
        <div className={stackColumnClass}>
          <div>
            <h3 className="mb-3">Request Payload</h3>
            <pre className={jsonBlockClass}>{formatJson(job.request)}</pre>
          </div>
          {job.result ? (
            <div>
              <h3 className="mb-3">Result Payload</h3>
              <pre className={jsonBlockClass}>{formatJson(job.result)}</pre>
            </div>
          ) : null}
        </div>
      </DisclosurePanel>
    </div>
  )
}
