import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { ApiError, api } from '../api'
import type { JobDetail } from '../types'
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

function joinClasses(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}

function formatDate(isoString: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    }).format(new Date(isoString))
  } catch {
    return isoString
  }
}

function formatJson(payload: unknown): string {
  return JSON.stringify(payload, null, 2)
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return {}
}

function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) {
    return error.message
  }
  return 'Unexpected error.'
}

// ─── Status helpers ───────────────────────────────────────────────────────────

type StatusTone = 'neutral' | 'active' | 'success' | 'warning' | 'error'

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

function statusBannerToneClass(tone: StatusTone): string {
  const tones: Record<StatusTone, string> = {
    active: 'border-[rgba(22,93,143,0.2)] bg-[rgba(22,93,143,0.1)] text-[#0d4c74]',
    success: 'border-[rgba(35,102,79,0.2)] bg-[rgba(35,102,79,0.1)] text-[var(--success)]',
    warning: 'border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.11)] text-[var(--warning)]',
    error: 'border-[rgba(154,45,40,0.18)] bg-[rgba(154,45,40,0.09)] text-[var(--error)]',
    neutral: 'border-[rgba(117,99,80,0.16)] bg-[rgba(117,99,80,0.1)] text-[var(--muted)]',
  }
  return tones[tone]
}

function StatusPill({ tone, children }: { tone: StatusTone; children: string }) {
  const tones: Record<StatusTone, string> = {
    active: 'bg-[rgba(22,93,143,0.1)] text-[#0d4c74]',
    success: 'bg-[rgba(35,102,79,0.1)] text-[var(--success)]',
    warning: 'bg-[rgba(155,106,18,0.11)] text-[var(--warning)]',
    error: 'bg-[rgba(154,45,40,0.09)] text-[var(--error)]',
    neutral: 'bg-[rgba(117,99,80,0.1)] text-[var(--muted)]',
  }
  return (
    <span
      className={joinClasses(
        'inline-flex items-center rounded-full px-[0.7rem] py-[0.35rem] text-[0.83rem] font-bold',
        tones[tone],
      )}
    >
      {children}
    </span>
  )
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

// ─── Warning components ───────────────────────────────────────────────────────

function WarningCard({ warning }: { warning: Record<string, unknown> }) {
  const code = typeof warning.code === 'string' ? warning.code : null
  const message = typeof warning.message === 'string' ? warning.message : null
  const severity = typeof warning.severity === 'string' ? warning.severity : 'warning'
  const family = typeof warning.family === 'string' ? warning.family : null
  const details = warning.details && typeof warning.details === 'object' && !Array.isArray(warning.details)
    ? (warning.details as Record<string, unknown>)
    : null

  const severityColors = {
    error: 'border-[rgba(154,45,40,0.2)] bg-[rgba(154,45,40,0.05)]',
    warning: 'border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.05)]',
    info: 'border-[rgba(22,93,143,0.15)] bg-[rgba(22,93,143,0.05)]',
  }
  const badgeColors = {
    error: 'bg-[rgba(154,45,40,0.1)] text-[var(--error)]',
    warning: 'bg-[rgba(155,106,18,0.12)] text-[var(--warning)]',
    info: 'bg-[rgba(22,93,143,0.1)] text-[#0d4c74]',
  }
  const border = severityColors[severity as keyof typeof severityColors] ?? severityColors.warning
  const badge = badgeColors[severity as keyof typeof badgeColors] ?? badgeColors.warning

  const detailEntries = details
    ? Object.entries(details).filter(([k]) => k !== 'hint' || !message?.includes('hint'))
    : []

  return (
    <div className={joinClasses('rounded-[18px] border p-4', border)}>
      <div className="flex flex-wrap items-start gap-2">
        {severity !== 'warning' ? (
          <span className={joinClasses('rounded-full px-2 py-0.5 text-[0.72rem] font-bold uppercase tracking-[0.1em]', badge)}>
            {severity}
          </span>
        ) : null}
        {family ? (
          <span className="text-[0.75rem] font-semibold uppercase tracking-[0.1em] text-[var(--muted)]">
            {family.replaceAll('_', ' ')}
          </span>
        ) : null}
        {code ? (
          <code className="ml-auto rounded bg-[rgba(88,63,39,0.08)] px-1.5 py-0.5 text-[0.75rem] text-[var(--muted)]">
            {code}
          </code>
        ) : null}
      </div>
      {message ? (
        <p className="mt-2 text-[0.95rem] leading-6 text-[var(--text)]">{message}</p>
      ) : null}
      {detailEntries.length > 0 ? (
        <dl className="mt-3 grid gap-1 text-[0.82rem]">
          {detailEntries.map(([k, v]) => (
            <div key={k} className="flex flex-wrap gap-x-2">
              <dt className="font-semibold text-[var(--muted)]">{k.replaceAll('_', ' ')}:</dt>
              <dd className="text-[var(--text)]">
                {Array.isArray(v)
                  ? v.join(', ')
                  : typeof v === 'object' && v !== null
                    ? JSON.stringify(v)
                    : String(v)}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  )
}

function WarningsPanel({ warnings }: { warnings: unknown[] }) {
  if (!warnings.length) {
    return (
      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h2>Warnings</h2>
        </div>
        <p className={emptyCopyClass}>No warnings.</p>
      </section>
    )
  }
  return (
    <section className={panelSoftClass}>
      <div className={headingGroupClass}>
        <h2>Warnings</h2>
        <p className={mutedCopyClass}>{warnings.length} warning{warnings.length === 1 ? '' : 's'}</p>
      </div>
      <div className="grid gap-3">
        {warnings.map((w, i) => (
          <WarningCard
            key={i}
            warning={typeof w === 'object' && w !== null && !Array.isArray(w)
              ? (w as Record<string, unknown>)
              : { message: String(w) }}
          />
        ))}
      </div>
    </section>
  )
}

// ─── DisclosurePanel ──────────────────────────────────────────────────────────

function DisclosurePanel({
  title,
  defaultOpen = false,
  children,
}: {
  title: string
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className={panelSoftClass}>
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <h2>{title}</h2>
        <span
          className={joinClasses(
            'flex-shrink-0 text-[1.1rem] leading-none text-[var(--muted)] transition-transform duration-150',
            open ? 'rotate-180' : '',
          )}
          aria-hidden="true"
        >
          ▾
        </span>
      </button>
      {open ? <div className="mt-4">{children}</div> : null}
    </section>
  )
}

// ─── BuildSummaryPanel ────────────────────────────────────────────────────────

type DiagnosticsPayload = {
  symbol_count?: number
  wire_count?: number
  label_count?: number
  missing_bindings?: string[]
  unexpected_bindings?: string[]
  duplicate_bindings?: string[]
  hard_failures?: Array<{ code?: string; message?: string; details?: unknown }>
  [key: string]: unknown
}

function BuildSummaryPanel({ diagnostics }: { diagnostics: DiagnosticsPayload }) {
  const symbols = diagnostics.symbol_count ?? 0
  const wires = diagnostics.wire_count ?? 0
  const labels = diagnostics.label_count ?? 0
  const missingBindings = diagnostics.missing_bindings ?? []
  const unexpectedBindings = diagnostics.unexpected_bindings ?? []
  const duplicateBindings = diagnostics.duplicate_bindings ?? []
  const hardFailures = diagnostics.hard_failures ?? []
  const hasIssues =
    missingBindings.length > 0 ||
    unexpectedBindings.length > 0 ||
    duplicateBindings.length > 0 ||
    hardFailures.length > 0

  return (
    <DisclosurePanel title="Build Summary">
      <div className="grid gap-4">
        <ul className="m-0 grid list-disc gap-2 pl-5 text-[0.95rem] leading-6 text-[var(--text)]">
          <li><strong>{symbols}</strong> {symbols === 1 ? 'component' : 'components'} placed in the schematic</li>
          <li><strong>{wires}</strong> wire {wires === 1 ? 'connection' : 'connections'} routed between pins</li>
          {labels > 0 ? (
            <li><strong>{labels}</strong> net {labels === 1 ? 'label' : 'labels'} added to identify signal connections</li>
          ) : null}
        </ul>
        {hasIssues ? (
          <div className="grid gap-3">
            {hardFailures.map((f, i) => (
              <div key={i} className="rounded-[14px] border border-[rgba(154,45,40,0.2)] bg-[rgba(154,45,40,0.05)] p-3">
                <p className="text-[0.82rem] font-bold text-[var(--error)]">Generation error</p>
                {f.message ? <p className="mt-1 text-[0.92rem] leading-6 text-[var(--text)]">{f.message}</p> : null}
                {f.code ? <p className="mt-1 text-[0.78rem] text-[var(--muted)]">Code: {f.code}</p> : null}
              </div>
            ))}
            {missingBindings.length > 0 ? (
              <div className="rounded-[14px] border border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.05)] p-3">
                <p className="text-[0.82rem] font-bold text-[var(--warning)]">Missing net connections</p>
                <p className="mt-1 text-[0.88rem] leading-6 text-[var(--muted)]">
                  These nets were declared in the IR but have no corresponding wire or label:{' '}
                  <span className="text-[var(--text)]">{missingBindings.join(', ')}</span>
                </p>
              </div>
            ) : null}
            {unexpectedBindings.length > 0 ? (
              <div className="rounded-[14px] border border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.05)] p-3">
                <p className="text-[0.82rem] font-bold text-[var(--warning)]">Unexpected net connections</p>
                <p className="mt-1 text-[0.88rem] leading-6 text-[var(--muted)]">
                  These connections appear in the schematic but were not in the IR:{' '}
                  <span className="text-[var(--text)]">{unexpectedBindings.join(', ')}</span>
                </p>
              </div>
            ) : null}
            {duplicateBindings.length > 0 ? (
              <div className="rounded-[14px] border border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.05)] p-3">
                <p className="text-[0.82rem] font-bold text-[var(--warning)]">Duplicate net connections</p>
                <p className="mt-1 text-[0.88rem] leading-6 text-[var(--muted)]">
                  These nets appear more than once:{' '}
                  <span className="text-[var(--text)]">{duplicateBindings.join(', ')}</span>
                </p>
              </div>
            ) : null}
          </div>
        ) : (
          <p className="text-[0.88rem] text-[var(--success)]">
            All net connections verified — no issues detected.
          </p>
        )}
      </div>
    </DisclosurePanel>
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
  const [job, setJob] = useState<JobDetail | null>(null)
  const [loadedJobId, setLoadedJobId] = useState<string | null>(null)
  const [failedJobId, setFailedJobId] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const effectiveJobId = jobId ?? ''
  const loading = Boolean(effectiveJobId) && loadedJobId !== effectiveJobId && failedJobId !== effectiveJobId

  useEffect(() => {
    if (!effectiveJobId) {
      return
    }
    let cancelled = false
    void api
      .getJob(effectiveJobId)
      .then((response) => {
        if (!cancelled) {
          setJob(response)
          setLoadedJobId(effectiveJobId)
          setFailedJobId(null)
          setErrorMessage(null)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setFailedJobId(effectiveJobId)
          setErrorMessage(getErrorMessage(error))
        }
      })
    return () => {
      cancelled = true
    }
  }, [effectiveJobId])

  useEffect(() => {
    const base = 'KiCad PCB Web App'
    if (job) {
      document.title = `${job.project_name} — Job — ${base}`
    } else {
      document.title = `Job — ${base}`
    }
  }, [job])

  if (!effectiveJobId) {
    return <NotFoundScreen heading="Job not found" message="No job ID was provided." />
  }

  if (loading) {
    return (
      <div className={joinClasses(bannerBaseClass, statusBannerToneClass('active'), 'mt-4')}>
        <span className={spinnerClass} aria-hidden="true"></span>
        <strong>Loading job detail…</strong>
      </div>
    )
  }

  if (!job) {
    return (
      <NotFoundScreen
        heading="Job not found"
        message={errorMessage ?? 'This job does not exist or could not be loaded.'}
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

      <WarningsPanel warnings={warnings} />

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
