import { startTransition, useEffect, useMemo, useState } from 'react'
import type { ChangeEvent, FormEvent, ReactNode } from 'react'
import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
} from 'react-router-dom'

import { ApiError, api } from './api'
import type {
  CircuitBlockSpec,
  CircuitPortSpec,
  CircuitRailSpec,
  JobDetail,
  JobSummary,
  UiBootstrapResponse,
  WizardGenerateProjectResponse,
  WizardSessionDetail,
  WizardStatus,
  WizardStep,
} from './types'

const WIZARD_STEP_ORDER: Record<WizardStep, number> = {
  describe: 0,
  spec: 1,
  ir: 2,
  generate: 3,
}

const WIZARD_STEP_META: Record<WizardStep, { label: string; summary: string }> = {
  describe: { label: 'Describe Circuit', summary: 'Start a session and refine the brief.' },
  spec: { label: 'Review Spec', summary: 'Approve or revise the drafted specification.' },
  ir: { label: 'Review Circuit IR', summary: 'Generate, validate, and inspect the IR payload.' },
  generate: { label: 'Generate Project', summary: 'Launch the deterministic KiCad generation path.' },
}

const pageStackClass = 'grid gap-5'
const stackColumnClass = 'grid gap-5'
const pageShellClass = 'mx-auto max-w-[1320px] px-6 py-6 lg:px-4'
const dashboardGridClass = 'grid gap-5 [grid-template-columns:minmax(0,1.5fr)_minmax(320px,0.9fr)] lg:grid-cols-1'
const heroPanelClass =
  "relative grid gap-6 overflow-hidden rounded-[28px] border border-[rgba(109,47,20,0.14)] bg-[linear-gradient(135deg,rgba(255,249,241,0.88),rgba(255,239,213,0.92)),radial-gradient(circle_at_top_right,rgba(24,75,69,0.2),transparent_36%)] p-[1.8rem] shadow-[0_24px_60px_rgba(71,43,19,0.12)] [grid-template-columns:minmax(0,1.4fr)_minmax(240px,0.7fr)] after:pointer-events-none after:absolute after:inset-[auto_-40px_-80px_auto] after:h-[240px] after:w-[240px] after:bg-[radial-gradient(circle,rgba(242,196,138,0.58),transparent_70%)] after:content-[''] lg:grid-cols-1"
const heroCardClass =
  'relative z-[1] grid gap-3 rounded-3xl border border-[var(--border)] bg-[var(--panel)] p-[1.35rem] shadow-[var(--shadow-soft)]'
const panelBaseClass =
  'rounded-3xl border border-[var(--border)] p-[1.35rem] shadow-[var(--shadow-soft)]'
const panelSoftClass = `${panelBaseClass} bg-[linear-gradient(180deg,rgba(255,252,247,0.95),rgba(250,244,233,0.94))]`
const panelAccentClass = `${panelBaseClass} bg-[linear-gradient(180deg,rgba(255,248,237,0.98),rgba(248,237,220,0.94))]`
const headingGroupClass = 'mb-4 grid gap-1.5'
const eyebrowClass = 'm-0 text-[0.83rem] font-bold uppercase tracking-[0.14em] text-[var(--brand)]'
const mutedCopyClass = 'text-[var(--muted)]'
const buttonRowClass = 'flex flex-wrap gap-3'
const inputGridTwoUpClass = 'grid gap-4 md:grid-cols-2'
const buttonBaseClass =
  'rounded-full border-0 px-[1.2rem] py-[0.8rem] font-semibold no-underline transition-[transform,opacity] duration-150 hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-[0.55] disabled:transform-none'
const buttonPrimaryClass =
  `${buttonBaseClass} bg-[linear-gradient(135deg,var(--brand)_0%,var(--brand-deep)_100%)] text-[#fff8f1] shadow-[0_14px_30px_rgba(109,47,20,0.18)]`
const buttonSecondaryClass = `${buttonBaseClass} bg-[rgba(24,75,69,0.09)] text-[var(--accent)]`
const bannerBaseClass = 'flex items-center gap-3 rounded-[18px] border px-[1.1rem] py-[0.9rem]'
const spinnerClass = 'h-4 w-4 animate-spin rounded-full border-2 border-[rgba(13,76,116,0.16)] border-t-current'
const jsonBlockClass =
  'overflow-auto rounded-[18px] bg-[rgba(40,31,23,0.95)] p-4 font-[var(--font-mono)] text-[0.85rem] leading-[1.55] whitespace-pre-wrap break-words text-[#f7ead6]'
const emptyCopyClass = 'text-[var(--muted)]'
const recordListClass = 'm-0 grid list-none gap-3 p-0'
const recordListItemClass = 'flex items-center justify-between gap-3 lg:flex-col lg:items-start'
const compactListItemClass = 'flex items-baseline justify-between gap-3 lg:flex-col lg:items-start'
const tagListClass = 'm-0 grid list-none gap-3 p-0 [grid-template-columns:repeat(auto-fit,minmax(160px,max-content))]'
const tagItemClass = 'rounded-full border border-[rgba(109,47,20,0.12)] bg-[rgba(255,236,208,0.82)] px-[0.8rem] py-[0.45rem]'
const detailListClass = 'm-0 grid list-none gap-x-3 gap-y-2 p-0 [grid-template-columns:max-content_minmax(0,1fr)]'
const detailListGridClass = `${detailListClass} md:[grid-template-columns:repeat(2,max-content_minmax(0,1fr))]`
const blockGridClass = 'grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(220px,1fr))]'
const blockCardClass = 'grid gap-2 rounded-[18px] border border-[rgba(88,63,39,0.14)] bg-[rgba(255,255,255,0.6)] p-4'
const stepTrackerClass = 'm-0 grid list-none gap-4 p-0 md:grid-cols-4'
const transcriptListClass = 'grid gap-4'
const transcriptEntryClass = 'grid gap-1.5 rounded-[18px] border border-[rgba(88,63,39,0.14)] p-4'

function joinClasses(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
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

function statusBannerToneClass(tone: ReturnType<typeof statusTone>): string {
  const tones: Record<ReturnType<typeof statusTone>, string> = {
    active: 'border-[rgba(22,93,143,0.2)] bg-[rgba(22,93,143,0.1)] text-[#0d4c74]',
    success: 'border-[rgba(35,102,79,0.2)] bg-[rgba(35,102,79,0.1)] text-[var(--success)]',
    warning: 'border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.11)] text-[var(--warning)]',
    error: 'border-[rgba(154,45,40,0.18)] bg-[rgba(154,45,40,0.09)] text-[var(--error)]',
    neutral: 'border-[rgba(117,99,80,0.16)] bg-[rgba(117,99,80,0.1)] text-[var(--muted)]',
  }
  return tones[tone]
}

function statusPillToneClass(tone: ReturnType<typeof statusTone>): string {
  const tones: Record<ReturnType<typeof statusTone>, string> = {
    active: 'bg-[rgba(22,93,143,0.1)] text-[#0d4c74]',
    success: 'bg-[rgba(35,102,79,0.1)] text-[var(--success)]',
    warning: 'bg-[rgba(155,106,18,0.11)] text-[var(--warning)]',
    error: 'bg-[rgba(154,45,40,0.09)] text-[var(--error)]',
    neutral: 'bg-[rgba(117,99,80,0.1)] text-[var(--muted)]',
  }
  return tones[tone]
}

function canonicalWizardStep(session: WizardSessionDetail): WizardStep {
  if (session.status === 'spec_ready_for_review') {
    return 'spec'
  }
  if (
    session.status === 'spec_approved' ||
    session.status === 'drafting_ir' ||
    session.status === 'ir_needs_repair'
  ) {
    return 'ir'
  }
  if (
    session.status === 'ir_ready_for_generation' ||
    session.status === 'generation_started' ||
    session.status === 'completed'
  ) {
    return 'generate'
  }
  if (session.status === 'failed') {
    if (session.ir_json || session.ir_validation) {
      return 'generate'
    }
    if (session.spec) {
      return 'spec'
    }
  }
  return 'describe'
}

function wizardStepUnlocked(session: WizardSessionDetail, step: WizardStep): boolean {
  return WIZARD_STEP_ORDER[step] <= WIZARD_STEP_ORDER[canonicalWizardStep(session)]
}

function statusTone(status: WizardStatus | string): 'neutral' | 'active' | 'success' | 'warning' | 'error' {
  if (
    status === 'spec_ready_for_review' ||
    status === 'spec_approved' ||
    status === 'drafting_ir' ||
    status === 'generation_started'
  ) {
    return 'active'
  }
  if (status === 'completed' || status === 'ir_ready_for_generation' || status === 'succeeded') {
    return 'success'
  }
  if (status === 'awaiting_user_clarification' || status === 'ir_needs_repair' || status === 'running') {
    return 'warning'
  }
  if (status === 'failed' || status === 'cancelled') {
    return 'error'
  }
  return 'neutral'
}

function bannerForSession(session: WizardSessionDetail): string {
  if (session.status === 'awaiting_user_clarification') {
    return 'The wizard needs more detail before a reviewable spec can be approved.'
  }
  if (session.status === 'spec_ready_for_review') {
    return 'The spec draft is ready for review and explicit approval.'
  }
  if (session.status === 'spec_approved') {
    return 'The spec is approved. Circuit IR generation is now available.'
  }
  if (session.status === 'drafting_ir') {
    return 'The backend is generating Circuit IR from the approved specification.'
  }
  if (session.status === 'ir_needs_repair') {
    return 'The latest Circuit IR draft needs another repair pass before generation.'
  }
  if (session.status === 'ir_ready_for_generation') {
    return 'Validated Circuit IR is ready for deterministic project generation.'
  }
  if (session.status === 'generation_started') {
    return 'Project generation has started. Stay on this route for the result.'
  }
  if (session.status === 'completed') {
    return 'Project generation completed. Review the job result and artifacts below.'
  }
  if (session.status === 'failed') {
    return session.error?.message ?? 'The wizard hit an error.'
  }
  return 'Describe the circuit and keep refining the prompt until the spec is ready.'
}

function StatusPill({ tone, children }: { tone: ReturnType<typeof statusTone>; children: string }) {
  return (
    <span
      className={joinClasses(
        'inline-flex items-center rounded-full px-[0.7rem] py-[0.35rem] text-[0.83rem] font-bold capitalize',
        statusPillToneClass(tone),
      )}
    >
      {children}
    </span>
  )
}

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

function LabelList({ items }: { items: string[] }) {
  if (!items.length) {
    return <p className={emptyCopyClass}>None recorded.</p>
  }
  return (
    <ul className={tagListClass}>
      {items.map((item) => (
        <li key={item} className={tagItemClass}>
          {item}
        </li>
      ))}
    </ul>
  )
}

function PortList({ ports }: { ports: CircuitPortSpec[] }) {
  if (!ports.length) {
    return <p className={emptyCopyClass}>None recorded.</p>
  }
  return (
    <ul className={recordListClass}>
      {ports.map((port) => (
        <li key={`${port.name}-${port.description ?? ''}`} className={compactListItemClass}>
          <strong>{port.name}</strong>
          <span className={mutedCopyClass}>{port.description ?? port.signal_type ?? 'No detail'}</span>
        </li>
      ))}
    </ul>
  )
}

function RailList({ rails }: { rails: CircuitRailSpec[] }) {
  if (!rails.length) {
    return <p className={emptyCopyClass}>None recorded.</p>
  }
  return (
    <ul className={recordListClass}>
      {rails.map((rail) => (
        <li key={`${rail.name}-${rail.nominal_voltage ?? ''}`} className={compactListItemClass}>
          <strong>{rail.name}</strong>
          <span className={mutedCopyClass}>{rail.nominal_voltage ?? rail.description ?? 'No detail'}</span>
        </li>
      ))}
    </ul>
  )
}

function BlockList({ blocks }: { blocks: CircuitBlockSpec[] }) {
  if (!blocks.length) {
    return <p className={emptyCopyClass}>None recorded.</p>
  }
  return (
    <div className={blockGridClass}>
      {blocks.map((block) => (
        <article key={`${block.name}-${block.block_type}`} className={blockCardClass}>
          <strong>{block.name}</strong>
          <span className={mutedCopyClass}>{block.block_type.replaceAll('_', ' ')}</span>
          <p className={mutedCopyClass}>{block.summary}</p>
        </article>
      ))}
    </div>
  )
}

function Layout({
  bootstrap,
  children,
}: {
  bootstrap: UiBootstrapResponse | null
  children: ReactNode
}) {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-[var(--border)] bg-[rgba(245,239,226,0.82)] backdrop-blur-[18px]">
        <div className="mx-auto grid max-w-[1320px] grid-cols-[auto_1fr_auto] items-center gap-4 px-6 py-4 lg:px-4">
          <Link className="text-xl font-bold no-underline [font-family:var(--font-heading)] tracking-[0.01em]" to="/">
            KiCad PCB Web App
          </Link>
          <nav className="flex justify-center gap-3">
            <NavLink
              className={({ isActive }) =>
                joinClasses(
                  'rounded-full px-4 py-2 text-sm font-medium no-underline transition-all duration-150',
                  isActive
                    ? '-translate-y-px bg-[rgba(161,69,26,0.12)] text-[var(--brand-deep)]'
                    : 'text-[var(--muted)] hover:-translate-y-px hover:bg-[rgba(161,69,26,0.12)] hover:text-[var(--brand-deep)]',
                )
              }
              to="/"
              end
            >
              Overview
            </NavLink>
            <NavLink
              className={({ isActive }) =>
                joinClasses(
                  'rounded-full px-4 py-2 text-sm font-medium no-underline transition-all duration-150',
                  isActive
                    ? '-translate-y-px bg-[rgba(161,69,26,0.12)] text-[var(--brand-deep)]'
                    : 'text-[var(--muted)] hover:-translate-y-px hover:bg-[rgba(161,69,26,0.12)] hover:text-[var(--brand-deep)]',
                )
              }
              to="/wizard"
            >
              Wizard
            </NavLink>
          </nav>
          <div className="flex flex-col items-end gap-px text-right text-[0.86rem] text-[var(--muted)] lg:items-start lg:text-left">
            <span>Provider</span>
            <strong>{bootstrap?.llm_provider ?? 'loading'}</strong>
          </div>
        </div>
      </header>
      <main className={pageShellClass}>{children}</main>
    </div>
  )
}

function HomePage({ bootstrap }: { bootstrap: UiBootstrapResponse }) {
  const navigate = useNavigate()
  const [doctorText, setDoctorText] = useState('Loading backend health checks...')
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [jobsError, setJobsError] = useState<string | null>(null)
  const [projectName, setProjectName] = useState('WebDemo')
  const [symbolsDir, setSymbolsDir] = useState('')
  const [netlistText, setNetlistText] = useState(() => formatJson(bootstrap.example_netlist_json))
  const [resultText, setResultText] = useState('No requests submitted yet.')
  const [symbolQuery, setSymbolQuery] = useState('')
  const [symbolResults, setSymbolResults] = useState('No symbol search run yet.')
  const [busyLabel, setBusyLabel] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void Promise.all([api.getDoctor(), api.getJobs()])
      .then(([doctorResponse, jobsResponse]) => {
        if (cancelled) {
          return
        }
        setDoctorText(
          doctorResponse.checks
            .map((check) => `${check.name}: ${check.detail} (${check.ok ? 'ok' : 'missing'})`)
            .join('\n'),
        )
        setJobs(jobsResponse)
      })
      .catch((error) => {
        if (cancelled) {
          return
        }
        const message = getErrorMessage(error)
        setDoctorText(message)
        setJobsError(message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  function parseNetlist(): Record<string, unknown> {
    return JSON.parse(netlistText) as Record<string, unknown>
  }

  async function handleValidate(): Promise<void> {
    setBusyLabel('Validating Circuit IR...')
    try {
      const response = await api.validateNetlist({
        netlist_json: parseNetlist(),
        symbols_dir: symbolsDir || null,
      })
      setResultText(formatJson(response))
    } catch (error) {
      setResultText(formatJson({ error: getErrorMessage(error) }))
    } finally {
      setBusyLabel(null)
    }
  }

  async function handleGenerate(): Promise<void> {
    setBusyLabel('Generating project from Circuit IR...')
    try {
      const response = await api.createJobFromNetlist({
        project_name: projectName,
        netlist_json: parseNetlist(),
        symbols_dir: symbolsDir || null,
        validation: 'internal',
        auto_fix: true,
      })
      setResultText(formatJson(response))
      setJobs((current) => [response, ...current.filter((job) => job.id !== response.id)])
      startTransition(() => {
        navigate(`/jobs/${response.id}`)
      })
    } catch (error) {
      setResultText(formatJson({ error: getErrorMessage(error) }))
    } finally {
      setBusyLabel(null)
    }
  }

  async function handleSearchSymbols(): Promise<void> {
    if (!symbolQuery.trim()) {
      setSymbolResults('Enter a symbol query first.')
      return
    }
    setBusyLabel('Searching symbols...')
    try {
      const response = await api.searchSymbols(symbolQuery.trim())
      setSymbolResults(formatJson(response))
    } catch (error) {
      setSymbolResults(formatJson({ error: getErrorMessage(error) }))
    } finally {
      setBusyLabel(null)
    }
  }

  function handleLoadFile(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.target.files?.[0]
    if (!file) {
      return
    }
    void file.text().then((content) => {
      setNetlistText(content)
    })
  }

  return (
    <div className={pageStackClass}>
      <section className={heroPanelClass}>
        <div>
          <p className={eyebrowClass}>React + TypeScript frontend</p>
          <h1>One frontend now owns the whole web UI.</h1>
          <p className={mutedCopyClass}>
            The browser stays responsive while it talks to the FastAPI backend. Use the
            wizard for guided flows or work directly with Circuit IR and job artifacts here.
          </p>
        </div>
        <div className={heroCardClass}>
          <p className={eyebrowClass}>Wizard status</p>
          <strong>{bootstrap.llm_enabled ? 'Ready to start' : 'Disabled in config'}</strong>
          <span className={mutedCopyClass}>{bootstrap.llm_provider}</span>
          <button type="button" className={buttonPrimaryClass} onClick={() => navigate('/wizard')}>
            Open Wizard
          </button>
        </div>
      </section>

      {busyLabel ? (
        <div className={joinClasses(bannerBaseClass, statusBannerToneClass('active'))}>
          <span className={spinnerClass} aria-hidden="true"></span>
          <strong>{busyLabel}</strong>
        </div>
      ) : null}

      <div className={dashboardGridClass}>
        <section className={panelAccentClass}>
          <div className={headingGroupClass}>
            <h2>Direct Circuit IR</h2>
            <p className={mutedCopyClass}>Paste, validate, and generate without leaving the SPA.</p>
          </div>
          <div className={inputGridTwoUpClass}>
            <label>
              <span>Project Name</span>
              <input value={projectName} onChange={(event) => setProjectName(event.target.value)} />
            </label>
            <label>
              <span>Symbols Directory</span>
              <input
                value={symbolsDir}
                onChange={(event) => setSymbolsDir(event.target.value)}
                placeholder="/path/to/symbols"
              />
            </label>
          </div>
          <label>
            <span>Load JSON File</span>
            <input type="file" accept=".json,application/json" onChange={handleLoadFile} />
          </label>
          <label>
            <span>Circuit IR JSON</span>
            <textarea rows={18} value={netlistText} onChange={(event) => setNetlistText(event.target.value)} />
          </label>
          <div className={buttonRowClass}>
            <button type="button" className={buttonSecondaryClass} onClick={() => void handleValidate()}>
              Validate
            </button>
            <button type="button" className={buttonPrimaryClass} onClick={() => void handleGenerate()}>
              Generate
            </button>
          </div>
        </section>

        <div className={stackColumnClass}>
          <section className={panelSoftClass}>
            <div className={headingGroupClass}>
              <h2>Doctor</h2>
              <p className={mutedCopyClass}>Current backend health report.</p>
            </div>
            <pre className={joinClasses(jsonBlockClass, 'min-h-28')}>{doctorText}</pre>
          </section>
          <section className={panelSoftClass}>
            <div className={headingGroupClass}>
              <h2>Symbol Search</h2>
              <p className={mutedCopyClass}>Query the installed symbol libraries without leaving the app.</p>
            </div>
            <div className={buttonRowClass}>
              <input
                className="flex-1"
                value={symbolQuery}
                onChange={(event) => setSymbolQuery(event.target.value)}
                placeholder="resistor"
              />
              <button type="button" className={buttonSecondaryClass} onClick={() => void handleSearchSymbols()}>
                Search
              </button>
            </div>
            <pre className={joinClasses(jsonBlockClass, 'min-h-28')}>{symbolResults}</pre>
          </section>
        </div>
      </div>

      <div className={dashboardGridClass}>
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Validation / Generation Results</h2>
            <p className={mutedCopyClass}>Raw API payloads are still available when you need detail.</p>
          </div>
          <pre className={jsonBlockClass}>{resultText}</pre>
        </section>
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Recent Jobs</h2>
            <p className={mutedCopyClass}>Jump straight into job detail pages and artifact downloads.</p>
          </div>
          {jobsError ? <p className={emptyCopyClass}>{jobsError}</p> : null}
          {jobs.length ? (
            <ul className={recordListClass}>
              {jobs.map((job) => (
                <li key={job.id} className={recordListItemClass}>
                  <Link className="font-semibold text-[var(--brand-deep)] no-underline" to={`/jobs/${job.id}`}>
                    {job.project_name}
                  </Link>
                  <StatusPill tone={statusTone(job.status)}>{job.status}</StatusPill>
                  <span className={mutedCopyClass}>{job.updated_at}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={emptyCopyClass}>No jobs yet.</p>
          )}
        </section>
      </div>
    </div>
  )
}

function JobSummaryPanel({ job }: { job: JobDetail }) {
  const result = asRecord(job.result)
  const warnings = Array.isArray(result.warnings) ? result.warnings : []
  const diagnostics = result.generated_schematic_diagnostics ?? null
  return (
    <div className={stackColumnClass}>
      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h2>Latest Job</h2>
        </div>
        <dl className={detailListGridClass}>
          <dt className={mutedCopyClass}>Project</dt>
          <dd>{job.project_name}</dd>
          <dt className={mutedCopyClass}>Status</dt>
          <dd>
            <StatusPill tone={statusTone(job.status)}>{job.status}</StatusPill>
          </dd>
          <dt className={mutedCopyClass}>Updated</dt>
          <dd>{job.updated_at}</dd>
        </dl>
        <div className={buttonRowClass}>
          <Link className={buttonSecondaryClass} to={`/jobs/${job.id}`}>
            Open Job Detail
          </Link>
          {job.artifacts.map((artifact) => (
            <a key={artifact} className={buttonSecondaryClass} href={`/api/jobs/${job.id}/artifacts/${artifact}`}>
              {artifact}
            </a>
          ))}
        </div>
      </section>
      <JsonPanel title="Warnings" payload={warnings} />
      {diagnostics ? <JsonPanel title="Diagnostics" payload={diagnostics} /> : null}
    </div>
  )
}

function WizardPage({ bootstrap }: { bootstrap: UiBootstrapResponse }) {
  const navigate = useNavigate()
  const { sessionId, step } = useParams<{ sessionId?: string; step?: string }>()
  const routeStep = step as WizardStep | undefined
  const [session, setSession] = useState<WizardSessionDetail | null>(null)
  const [latestJob, setLatestJob] = useState<JobDetail | null>(null)
  const [loadedSessionId, setLoadedSessionId] = useState<string | null>(null)
  const [failedSessionId, setFailedSessionId] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [busyMessage, setBusyMessage] = useState<string | null>(null)
  const [projectName, setProjectName] = useState('')
  const [symbolsDir, setSymbolsDir] = useState('')
  const [message, setMessage] = useState('')
  const loading = Boolean(sessionId) && loadedSessionId !== sessionId && failedSessionId !== sessionId

  useEffect(() => {
    if (!sessionId) {
      return
    }

    let cancelled = false
    void api
      .getWizardSession(sessionId)
      .then((response) => {
        if (cancelled) {
          return
        }
        setSession(response)
        setLoadedSessionId(sessionId)
        setFailedSessionId(null)
        setErrorMessage(null)
        setProjectName(response.project_name ?? '')
        setSymbolsDir(response.symbols_dir ?? '')
      })
      .catch((error) => {
        if (!cancelled) {
          setFailedSessionId(sessionId)
          setErrorMessage(getErrorMessage(error))
        }
      })
    return () => {
      cancelled = true
    }
  }, [sessionId])

  useEffect(() => {
    if (!session?.latest_job_id) {
      return
    }
    let cancelled = false
    void api
      .getJob(session.latest_job_id)
      .then((job) => {
        if (!cancelled) {
          setLatestJob(job)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLatestJob(null)
        }
      })
    return () => {
      cancelled = true
    }
  }, [session?.latest_job_id])

  useEffect(() => {
    if (!session) {
      return
    }
    const canonical = canonicalWizardStep(session)
    const targetStep = routeStep && wizardStepUnlocked(session, routeStep) ? routeStep : canonical
    const targetPath = `/wizard/${session.id}/${targetStep}`
    if (`/wizard/${session.id}/${routeStep ?? ''}` !== targetPath) {
      startTransition(() => {
        navigate(targetPath, { replace: true })
      })
    }
  }, [navigate, routeStep, session])

  const currentStep: WizardStep = useMemo(() => {
    if (!session) {
      return 'describe'
    }
    if (routeStep && wizardStepUnlocked(session, routeStep)) {
      return routeStep
    }
    return canonicalWizardStep(session)
  }, [routeStep, session])

  async function handleCreateSession(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setBusyMessage(`Talking to ${bootstrap.llm_provider} to draft the first spec...`)
    setErrorMessage(null)
    try {
      const response = await api.createWizardSession({
        message,
        project_name: projectName || null,
        symbols_dir: symbolsDir || null,
      })
      setSession(response)
      setLatestJob(null)
      setMessage('')
      startTransition(() => {
        navigate(`/wizard/${response.id}/${canonicalWizardStep(response)}`)
      })
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setBusyMessage(null)
    }
  }

  async function handleSendMessage(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!session) {
      return
    }
    setBusyMessage(`Talking to ${bootstrap.llm_provider} to revise the spec draft...`)
    setErrorMessage(null)
    try {
      const response = await api.addWizardMessage(session.id, {
        message,
        project_name: projectName || null,
        symbols_dir: symbolsDir || null,
      })
      setSession(response)
      setLatestJob(null)
      setMessage('')
      startTransition(() => {
        navigate(`/wizard/${response.id}/${canonicalWizardStep(response)}`)
      })
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setBusyMessage(null)
    }
  }

  async function handleApproveSpec(): Promise<void> {
    if (!session) {
      return
    }
    setBusyMessage('Locking this spec checkpoint and moving to Circuit IR...')
    setErrorMessage(null)
    try {
      const response = await api.approveWizardSpec(session.id)
      setSession(response)
      setLatestJob(null)
      startTransition(() => {
        navigate(`/wizard/${response.id}/${canonicalWizardStep(response)}`)
      })
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setBusyMessage(null)
    }
  }

  async function handleGenerateIr(): Promise<void> {
    if (!session) {
      return
    }
    setBusyMessage(`Talking to ${bootstrap.llm_provider} and validating the resulting Circuit IR...`)
    setErrorMessage(null)
    try {
      const response = await api.generateWizardIr(session.id)
      setSession(response)
      if (!response.latest_job_id) {
        setLatestJob(null)
      }
      startTransition(() => {
        navigate(`/wizard/${response.id}/${canonicalWizardStep(response)}`)
      })
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setBusyMessage(null)
    }
  }

  async function handleGenerateProject(): Promise<void> {
    if (!session) {
      return
    }
    setBusyMessage('Generating the KiCad project from the validated Circuit IR...')
    setErrorMessage(null)
    try {
      const response: WizardGenerateProjectResponse = await api.generateWizardProject(session.id)
      setSession(response.session)
      setLatestJob(response.job)
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setBusyMessage(null)
    }
  }

  if (!sessionId) {
    return (
      <div className={pageStackClass}>
        <section className={heroPanelClass}>
          <div>
            <p className={eyebrowClass}>Guided circuit workflow</p>
            <h1>Wizard flow, now on React + TypeScript.</h1>
            <p className={mutedCopyClass}>
              The wizard runs through the same backend API, but the client now keeps the page alive
              while requests are in flight instead of blocking a full-page form submit.
            </p>
          </div>
          <div className={heroCardClass}>
            <p className={eyebrowClass}>Provider</p>
            <strong>{bootstrap.llm_provider}</strong>
            <span className={mutedCopyClass}>{bootstrap.llm_enabled ? 'Enabled' : 'Disabled in config'}</span>
          </div>
        </section>

        {errorMessage ? (
          <div className={joinClasses(bannerBaseClass, statusBannerToneClass('error'))}>
            <strong>{errorMessage}</strong>
          </div>
        ) : null}
        {busyMessage ? (
          <div className={joinClasses(bannerBaseClass, statusBannerToneClass('active'))}>
            <span className={spinnerClass} aria-hidden="true"></span>
            <strong>{busyMessage}</strong>
          </div>
        ) : null}

        <section className={panelAccentClass}>
          <div className={headingGroupClass}>
            <h2>Create a New Session</h2>
            <p className={mutedCopyClass}>
              Describe the goal, rails, inputs, outputs, and constraints. The client keeps control
              while the request runs.
            </p>
          </div>
          <form className="grid gap-5" onSubmit={(event) => void handleCreateSession(event)}>
            <div className={inputGridTwoUpClass}>
              <label>
                <span>Project Name</span>
                <input value={projectName} onChange={(event) => setProjectName(event.target.value)} />
              </label>
              <label>
                <span>Symbols Directory</span>
                <input value={symbolsDir} onChange={(event) => setSymbolsDir(event.target.value)} />
              </label>
            </div>
            <label>
              <span>Circuit Request</span>
              <textarea rows={10} required value={message} onChange={(event) => setMessage(event.target.value)} />
            </label>
            <div className={buttonRowClass}>
              <button
                type="submit"
                className={buttonPrimaryClass}
                disabled={!bootstrap.llm_enabled || !message.trim() || Boolean(busyMessage)}
              >
                Start Session
              </button>
            </div>
          </form>
        </section>
      </div>
    )
  }

  if (loading) {
    return (
      <div className={joinClasses(bannerBaseClass, statusBannerToneClass('active'), 'mt-4')}>
        <span className={spinnerClass} aria-hidden="true"></span>
        <strong>Loading wizard session...</strong>
      </div>
    )
  }

  if (!session) {
    return (
      <div className={joinClasses(bannerBaseClass, statusBannerToneClass('error'), 'mt-4')}>
        <strong>{errorMessage ?? 'Wizard session not found.'}</strong>
      </div>
    )
  }

  const canApproveSpec =
    Boolean(session.spec) &&
    !session.spec_approved &&
    !session.open_questions.length &&
    !session.unsupported_reasons.length
  const canGenerateIr = Boolean(session.spec) && session.spec_approved
  const canGenerateProject = Boolean(session.ir_validation?.valid)
  const visibleLatestJob = session.latest_job_id ? latestJob : null

  return (
    <div className={pageStackClass}>
      <section className={heroPanelClass}>
        <div>
          <p className={eyebrowClass}>Session {session.id}</p>
          <h1>{WIZARD_STEP_META[currentStep].label}</h1>
          <p className={mutedCopyClass}>{bannerForSession(session)}</p>
        </div>
        <div className={heroCardClass}>
          <p className={eyebrowClass}>Current status</p>
          <StatusPill tone={statusTone(session.status)}>{session.status.replaceAll('_', ' ')}</StatusPill>
          <span className={mutedCopyClass}>{session.llm_provider ?? bootstrap.llm_provider}</span>
        </div>
      </section>

      {busyMessage ? (
        <div className={joinClasses(bannerBaseClass, statusBannerToneClass('active'))}>
          <span className={spinnerClass} aria-hidden="true"></span>
          <strong>{busyMessage}</strong>
        </div>
      ) : null}
      {errorMessage ? (
        <div className={joinClasses(bannerBaseClass, statusBannerToneClass('error'))}>
          <strong>{errorMessage}</strong>
        </div>
      ) : null}
      {session.error?.message ? (
        <div className={joinClasses(bannerBaseClass, statusBannerToneClass('error'))}>
          <strong>{session.error.message}</strong>
        </div>
      ) : null}

      <section className={panelSoftClass}>
        <ol className={stepTrackerClass}>
          {(Object.keys(WIZARD_STEP_META) as WizardStep[]).map((wizardStep, index) => {
            const unlocked = wizardStepUnlocked(session, wizardStep)
            const active = currentStep === wizardStep
            return (
              <li
                key={wizardStep}
                className={joinClasses(
                  'grid gap-[0.55rem] rounded-[18px] border border-[rgba(88,63,39,0.14)] bg-[rgba(255,255,255,0.6)] p-[0.9rem] [grid-template-columns:2.2rem_minmax(0,1fr)]',
                  active && 'border-[rgba(161,69,26,0.3)]',
                  !active && unlocked && 'bg-[rgba(235,247,241,0.8)]',
                  !active && !unlocked && 'opacity-70',
                )}
              >
                <span className="inline-flex h-[2.2rem] w-[2.2rem] items-center justify-center rounded-full bg-[rgba(161,69,26,0.12)] font-bold">
                  {index + 1}
                </span>
                <div>
                  {unlocked ? (
                    <Link className="font-semibold text-[var(--brand-deep)] no-underline" to={`/wizard/${session.id}/${wizardStep}`}>
                      {WIZARD_STEP_META[wizardStep].label}
                    </Link>
                  ) : (
                    <strong>{WIZARD_STEP_META[wizardStep].label}</strong>
                  )}
                  <p className={mutedCopyClass}>{WIZARD_STEP_META[wizardStep].summary}</p>
                </div>
              </li>
            )
          })}
        </ol>
      </section>

      <div className={dashboardGridClass}>
        <aside className={joinClasses(panelSoftClass, 'top-[6.2rem] self-start lg:static lg:top-auto')}>
          <div className={headingGroupClass}>
            <h2>Session</h2>
          </div>
          <dl className={detailListClass}>
            <dt className={mutedCopyClass}>Project</dt>
            <dd>{session.project_name ?? session.spec?.project_name ?? 'Not set'}</dd>
            <dt className={mutedCopyClass}>Provider</dt>
            <dd>{session.llm_provider ?? bootstrap.llm_provider}</dd>
            <dt className={mutedCopyClass}>Updated</dt>
            <dd>{session.updated_at}</dd>
          </dl>
          <div className={buttonRowClass}>
            <Link className={buttonSecondaryClass} to="/wizard">
              New Session
            </Link>
            {session.latest_job_id ? (
              <Link className={buttonSecondaryClass} to={`/jobs/${session.latest_job_id}`}>
                Open Job
              </Link>
            ) : null}
          </div>
        </aside>

        <div className={stackColumnClass}>
          {currentStep === 'describe' ? (
            <>
              <section className={panelAccentClass}>
                <div className={headingGroupClass}>
                  <h2>Describe Circuit</h2>
                  <p className={mutedCopyClass}>Keep refining the prompt until the spec is ready for review.</p>
                </div>
                <form className="grid gap-5" onSubmit={(event) => void handleSendMessage(event)}>
                  <div className={inputGridTwoUpClass}>
                    <label>
                      <span>Project Name</span>
                      <input value={projectName} onChange={(event) => setProjectName(event.target.value)} />
                    </label>
                    <label>
                      <span>Symbols Directory</span>
                      <input value={symbolsDir} onChange={(event) => setSymbolsDir(event.target.value)} />
                    </label>
                  </div>
                  <label>
                    <span>Circuit Request</span>
                    <textarea rows={8} required value={message} onChange={(event) => setMessage(event.target.value)} />
                  </label>
                  <div className={buttonRowClass}>
                    <button
                      type="submit"
                      className={buttonPrimaryClass}
                      disabled={!bootstrap.llm_enabled || !message.trim() || Boolean(busyMessage)}
                    >
                      {session.messages.length > 1 ? 'Send Revision Note' : 'Start Wizard'}
                    </button>
                  </div>
                </form>
              </section>

              <section className={panelSoftClass}>
                <div className={headingGroupClass}>
                  <h2>Conversation</h2>
                </div>
                <div className={transcriptListClass}>
                  {session.messages.map((entry, index) => (
                    <article
                      key={`${entry.role}-${index}`}
                      className={joinClasses(
                        transcriptEntryClass,
                        entry.role === 'assistant'
                          ? 'bg-[rgba(255,245,228,0.8)]'
                          : 'bg-[rgba(236,245,243,0.78)]',
                      )}
                    >
                      <strong>{entry.role === 'user' ? 'You' : 'Wizard'}</strong>
                      <p className={mutedCopyClass}>{entry.content}</p>
                    </article>
                  ))}
                </div>
              </section>
            </>
          ) : null}

          {currentStep === 'spec' && session.spec ? (
            <>
              <section className={panelAccentClass}>
                <div className={headingGroupClass}>
                  <h2>Spec Review</h2>
                  <p className={mutedCopyClass}>Review the drafted circuit spec before allowing Circuit IR generation.</p>
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <div>
                    <h3>Purpose</h3>
                    <p>{session.spec.purpose}</p>
                  </div>
                  <div>
                    <h3>Inputs</h3>
                    <PortList ports={session.spec.inputs} />
                  </div>
                  <div>
                    <h3>Outputs</h3>
                    <PortList ports={session.spec.outputs} />
                  </div>
                  <div>
                    <h3>Supply Rails</h3>
                    <RailList rails={session.spec.supply_rails} />
                  </div>
                  <div>
                    <h3>Acceptance Criteria</h3>
                    <LabelList items={session.spec.acceptance_criteria} />
                  </div>
                  <div>
                    <h3>Constraints</h3>
                    <LabelList items={session.spec.constraints} />
                  </div>
                  <div>
                    <h3>Open Questions</h3>
                    <LabelList items={session.open_questions} />
                  </div>
                  <div>
                    <h3>Unsupported Reasons</h3>
                    <LabelList items={session.unsupported_reasons} />
                  </div>
                  <div className="md:col-span-2">
                    <h3>Blocks</h3>
                    <BlockList blocks={session.spec.blocks} />
                  </div>
                </div>
              </section>

              <section className={panelSoftClass}>
                <div className={headingGroupClass}>
                  <h2>Revise or Approve</h2>
                </div>
                <form className="grid gap-5" onSubmit={(event) => void handleSendMessage(event)}>
                  <label>
                    <span>Revision Note</span>
                    <textarea rows={6} value={message} onChange={(event) => setMessage(event.target.value)} />
                  </label>
                  <div className={buttonRowClass}>
                    <button
                      type="submit"
                      className={buttonSecondaryClass}
                      disabled={!message.trim() || Boolean(busyMessage)}
                    >
                      Send Revision Note
                    </button>
                    <button
                      type="button"
                      className={buttonPrimaryClass}
                      disabled={!canApproveSpec || Boolean(busyMessage)}
                      onClick={() => void handleApproveSpec()}
                    >
                      Approve Spec
                    </button>
                  </div>
                </form>
              </section>
            </>
          ) : null}

          {currentStep === 'ir' ? (
            <>
              <section className={panelAccentClass}>
                <div className={headingGroupClass}>
                  <h2>Circuit IR</h2>
                  <p className={mutedCopyClass}>
                    Generate the IR from the approved spec and inspect validation before creating a project.
                  </p>
                </div>
                {session.ir_validation ? (
                  <dl className={detailListGridClass}>
                    <dt className={mutedCopyClass}>Valid</dt>
                    <dd>{session.ir_validation.valid ? 'Yes' : 'No'}</dd>
                    <dt className={mutedCopyClass}>Auto-fixed</dt>
                    <dd>{session.ir_validation.auto_fixed ? 'Yes' : 'No'}</dd>
                    <dt className={mutedCopyClass}>Components</dt>
                    <dd>{session.ir_validation.component_count}</dd>
                    <dt className={mutedCopyClass}>Nets</dt>
                    <dd>{session.ir_validation.net_count}</dd>
                    <dt className={mutedCopyClass}>Symbols Dirs Used</dt>
                    <dd>{session.ir_validation.symbols_dirs_used.join(', ') || 'None'}</dd>
                  </dl>
                ) : (
                  <p className={emptyCopyClass}>No Circuit IR draft yet.</p>
                )}
                {session.ir_validation?.error_message ? (
                  <div className={joinClasses(bannerBaseClass, statusBannerToneClass('warning'))}>
                    <strong>{session.ir_validation.error_message}</strong>
                  </div>
                ) : null}
                <div className={buttonRowClass}>
                  <button
                    type="button"
                    className={buttonPrimaryClass}
                    disabled={!canGenerateIr || Boolean(busyMessage)}
                    onClick={() => void handleGenerateIr()}
                  >
                    {session.status === 'ir_needs_repair' ? 'Repair Circuit IR' : 'Generate Circuit IR'}
                  </button>
                </div>
              </section>
              {session.ir_validation?.warnings.length ? (
                <JsonPanel title="Validation Warnings" payload={session.ir_validation.warnings} />
              ) : null}
              {session.ir_json ? <JsonPanel title="Raw Circuit IR JSON" payload={session.ir_json} /> : null}
            </>
          ) : null}

          {currentStep === 'generate' ? (
            <>
              <section className={panelAccentClass}>
                <div className={headingGroupClass}>
                  <h2>Generate Project</h2>
                  <p className={mutedCopyClass}>
                    Use the validated Circuit IR as the handoff into the deterministic generation pipeline.
                  </p>
                </div>
                <div className={buttonRowClass}>
                  <button
                    type="button"
                    className={buttonPrimaryClass}
                    disabled={!canGenerateProject || Boolean(busyMessage)}
                    onClick={() => void handleGenerateProject()}
                  >
                    {visibleLatestJob ? 'Generate Again' : 'Generate Project'}
                  </button>
                </div>
              </section>
              {visibleLatestJob ? <JobSummaryPanel job={visibleLatestJob} /> : <p className={emptyCopyClass}>No generation job linked yet.</p>}
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function JobPage() {
  const { jobId } = useParams<{ jobId: string }>()
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

  if (!effectiveJobId) {
    return (
      <div className={joinClasses(bannerBaseClass, statusBannerToneClass('error'), 'mt-4')}>
        <strong>Job id is required.</strong>
      </div>
    )
  }

  if (loading) {
    return (
      <div className={joinClasses(bannerBaseClass, statusBannerToneClass('active'), 'mt-4')}>
        <span className={spinnerClass} aria-hidden="true"></span>
        <strong>Loading job detail...</strong>
      </div>
    )
  }

  if (!job) {
    return (
      <div className={joinClasses(bannerBaseClass, statusBannerToneClass('error'), 'mt-4')}>
        <strong>{errorMessage ?? 'Job not found.'}</strong>
      </div>
    )
  }

  const result = asRecord(job.result)
  const warnings = Array.isArray(result.warnings) ? result.warnings : []
  const diagnostics = result.generated_schematic_diagnostics ?? { message: 'No diagnostic summary recorded.' }

  return (
    <div className={pageStackClass}>
      <section className={heroPanelClass}>
        <div>
          <p className={eyebrowClass}>Job {job.id}</p>
          <h1>{job.project_name}</h1>
          <p className={mutedCopyClass}>Inspect the full generation result, artifacts, diagnostics, and raw payloads.</p>
        </div>
        <div className={heroCardClass}>
          <p className={eyebrowClass}>Status</p>
          <StatusPill tone={statusTone(job.status)}>{job.status}</StatusPill>
          <span className={mutedCopyClass}>{job.updated_at}</span>
        </div>
      </section>

      <div className={dashboardGridClass}>
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Artifacts</h2>
          </div>
          {job.artifacts.length ? (
            <div className={buttonRowClass}>
              {job.artifacts.map((artifact) => (
                <a key={artifact} className={buttonSecondaryClass} href={`/api/jobs/${job.id}/artifacts/${artifact}`}>
                  {artifact}
                </a>
              ))}
            </div>
          ) : (
            <p className={emptyCopyClass}>No artifacts available.</p>
          )}
        </section>
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Summary</h2>
          </div>
          <dl className={detailListGridClass}>
            <dt className={mutedCopyClass}>Created</dt>
            <dd>{job.created_at}</dd>
            <dt className={mutedCopyClass}>Updated</dt>
            <dd>{job.updated_at}</dd>
            <dt className={mutedCopyClass}>Components</dt>
            <dd>{String(result.component_count ?? '—')}</dd>
            <dt className={mutedCopyClass}>Nets</dt>
            <dd>{String(result.net_count ?? '—')}</dd>
          </dl>
        </section>
      </div>

      <div className={dashboardGridClass}>
        <JsonPanel title="Warnings" payload={warnings} />
        <JsonPanel title="Diagnostics / Debug" payload={diagnostics} />
      </div>
      {job.error ? <JsonPanel title="Error Payload" payload={job.error} /> : null}
      <JsonPanel title="Request Payload" payload={job.request} />
      {job.result ? <JsonPanel title="Result Payload" payload={job.result} /> : null}
    </div>
  )
}

function AppShell() {
  const [bootstrap, setBootstrap] = useState<UiBootstrapResponse | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void api
      .getBootstrap()
      .then((response) => {
        if (!cancelled) {
          setBootstrap(response)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorMessage(getErrorMessage(error))
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (!bootstrap) {
    return (
      <Layout bootstrap={null}>
        <div
          className={joinClasses(
            bannerBaseClass,
            statusBannerToneClass(errorMessage ? 'error' : 'active'),
            'mt-4',
          )}
        >
          {errorMessage ? (
            <strong>{errorMessage}</strong>
          ) : (
            <>
              <span className={spinnerClass} aria-hidden="true"></span>
              <strong>Loading UI bootstrap...</strong>
            </>
          )}
        </div>
      </Layout>
    )
  }

  return (
    <Layout bootstrap={bootstrap}>
      <Routes>
        <Route path="/" element={<HomePage bootstrap={bootstrap} />} />
        <Route path="/wizard" element={<WizardPage bootstrap={bootstrap} />} />
        <Route path="/wizard/:sessionId" element={<WizardPage bootstrap={bootstrap} />} />
        <Route path="/wizard/:sessionId/:step" element={<WizardPage bootstrap={bootstrap} />} />
        <Route path="/jobs/:jobId" element={<JobPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  )
}