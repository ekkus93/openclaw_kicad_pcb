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
  return <span className={`status-pill status-${tone}`}>{children}</span>
}

function JsonPanel({ title, payload }: { title: string; payload: unknown }) {
  return (
    <section className="panel panel-soft">
      <div className="section-heading compact-heading">
        <h2>{title}</h2>
      </div>
      <pre className="json-block">{formatJson(payload)}</pre>
    </section>
  )
}

function LabelList({ items }: { items: string[] }) {
  if (!items.length) {
    return <p className="empty-copy">None recorded.</p>
  }
  return (
    <ul className="tag-list">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  )
}

function PortList({ ports }: { ports: CircuitPortSpec[] }) {
  if (!ports.length) {
    return <p className="empty-copy">None recorded.</p>
  }
  return (
    <ul className="record-list compact-list">
      {ports.map((port) => (
        <li key={`${port.name}-${port.description ?? ''}`}>
          <strong>{port.name}</strong>
          <span>{port.description ?? port.signal_type ?? 'No detail'}</span>
        </li>
      ))}
    </ul>
  )
}

function RailList({ rails }: { rails: CircuitRailSpec[] }) {
  if (!rails.length) {
    return <p className="empty-copy">None recorded.</p>
  }
  return (
    <ul className="record-list compact-list">
      {rails.map((rail) => (
        <li key={`${rail.name}-${rail.nominal_voltage ?? ''}`}>
          <strong>{rail.name}</strong>
          <span>{rail.nominal_voltage ?? rail.description ?? 'No detail'}</span>
        </li>
      ))}
    </ul>
  )
}

function BlockList({ blocks }: { blocks: CircuitBlockSpec[] }) {
  if (!blocks.length) {
    return <p className="empty-copy">None recorded.</p>
  }
  return (
    <div className="block-grid">
      {blocks.map((block) => (
        <article key={`${block.name}-${block.block_type}`} className="block-card">
          <strong>{block.name}</strong>
          <span>{block.block_type.replaceAll('_', ' ')}</span>
          <p>{block.summary}</p>
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
    <div className="app-shell">
      <header className="site-header">
        <div className="site-header__inner">
          <Link className="brand-mark" to="/">
            KiCad PCB Web App
          </Link>
          <nav className="site-nav">
            <NavLink to="/" end>
              Overview
            </NavLink>
            <NavLink to="/wizard">Wizard</NavLink>
          </nav>
          <div className="provider-chip">
            <span>Provider</span>
            <strong>{bootstrap?.llm_provider ?? 'loading'}</strong>
          </div>
        </div>
      </header>
      <main className="page-shell">{children}</main>
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
    <div className="page-stack">
      <section className="hero-panel hero-panel--overview">
        <div>
          <p className="eyebrow">React + TypeScript frontend</p>
          <h1>One frontend now owns the whole web UI.</h1>
          <p className="hero-copy">
            The browser stays responsive while it talks to the FastAPI backend. Use the
            wizard for guided flows or work directly with Circuit IR and job artifacts here.
          </p>
        </div>
        <div className="hero-card">
          <p className="hero-card__label">Wizard status</p>
          <strong>{bootstrap.llm_enabled ? 'Ready to start' : 'Disabled in config'}</strong>
          <span>{bootstrap.llm_provider}</span>
          <button type="button" className="button button-primary" onClick={() => navigate('/wizard')}>
            Open Wizard
          </button>
        </div>
      </section>

      {busyLabel ? (
        <div className="status-banner status-active">
          <span className="spinner" aria-hidden="true"></span>
          <strong>{busyLabel}</strong>
        </div>
      ) : null}

      <div className="dashboard-grid">
        <section className="panel panel-accent">
          <div className="section-heading">
            <h2>Direct Circuit IR</h2>
            <p>Paste, validate, and generate without leaving the SPA.</p>
          </div>
          <div className="field-grid two-up">
            <label>
              <span>Project Name</span>
              <input value={projectName} onChange={(event) => setProjectName(event.target.value)} />
            </label>
            <label>
              <span>Symbols Directory</span>
              <input value={symbolsDir} onChange={(event) => setSymbolsDir(event.target.value)} placeholder="/path/to/symbols" />
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
          <div className="button-row">
            <button type="button" className="button button-secondary" onClick={() => void handleValidate()}>
              Validate
            </button>
            <button type="button" className="button button-primary" onClick={() => void handleGenerate()}>
              Generate
            </button>
          </div>
        </section>

        <div className="stack-column">
          <section className="panel panel-soft">
            <div className="section-heading">
              <h2>Doctor</h2>
              <p>Current backend health report.</p>
            </div>
            <pre className="json-block json-block--compact">{doctorText}</pre>
          </section>
          <section className="panel panel-soft">
            <div className="section-heading">
              <h2>Symbol Search</h2>
              <p>Query the installed symbol libraries without leaving the app.</p>
            </div>
            <div className="inline-form">
              <input value={symbolQuery} onChange={(event) => setSymbolQuery(event.target.value)} placeholder="resistor" />
              <button type="button" className="button button-secondary" onClick={() => void handleSearchSymbols()}>
                Search
              </button>
            </div>
            <pre className="json-block json-block--compact">{symbolResults}</pre>
          </section>
        </div>
      </div>

      <div className="dashboard-grid dashboard-grid--results">
        <section className="panel panel-soft">
          <div className="section-heading">
            <h2>Validation / Generation Results</h2>
            <p>Raw API payloads are still available when you need detail.</p>
          </div>
          <pre className="json-block">{resultText}</pre>
        </section>
        <section className="panel panel-soft">
          <div className="section-heading">
            <h2>Recent Jobs</h2>
            <p>Jump straight into job detail pages and artifact downloads.</p>
          </div>
          {jobsError ? <p className="empty-copy">{jobsError}</p> : null}
          {jobs.length ? (
            <ul className="record-list">
              {jobs.map((job) => (
                <li key={job.id}>
                  <Link to={`/jobs/${job.id}`}>{job.project_name}</Link>
                  <StatusPill tone={statusTone(job.status)}>{job.status}</StatusPill>
                  <span>{job.updated_at}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty-copy">No jobs yet.</p>
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
    <div className="stack-column">
      <section className="panel panel-soft">
        <div className="section-heading compact-heading">
          <h2>Latest Job</h2>
        </div>
        <dl className="detail-list detail-list--grid">
          <dt>Project</dt>
          <dd>{job.project_name}</dd>
          <dt>Status</dt>
          <dd>
            <StatusPill tone={statusTone(job.status)}>{job.status}</StatusPill>
          </dd>
          <dt>Updated</dt>
          <dd>{job.updated_at}</dd>
        </dl>
        <div className="button-row wrap-row">
          <Link className="button button-secondary" to={`/jobs/${job.id}`}>
            Open Job Detail
          </Link>
          {job.artifacts.map((artifact) => (
            <a key={artifact} className="button button-secondary" href={`/api/jobs/${job.id}/artifacts/${artifact}`}>
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
      <div className="page-stack">
        <section className="hero-panel hero-panel--wizard">
          <div>
            <p className="eyebrow">Guided circuit workflow</p>
            <h1>Wizard flow, now on React + TypeScript.</h1>
            <p className="hero-copy">
              The wizard runs through the same backend API, but the client now keeps the page alive
              while requests are in flight instead of blocking a full-page form submit.
            </p>
          </div>
          <div className="hero-card">
            <p className="hero-card__label">Provider</p>
            <strong>{bootstrap.llm_provider}</strong>
            <span>{bootstrap.llm_enabled ? 'Enabled' : 'Disabled in config'}</span>
          </div>
        </section>

        {errorMessage ? <div className="status-banner status-error"><strong>{errorMessage}</strong></div> : null}
        {busyMessage ? (
          <div className="status-banner status-active">
            <span className="spinner" aria-hidden="true"></span>
            <strong>{busyMessage}</strong>
          </div>
        ) : null}

        <section className="panel panel-accent panel-form">
          <div className="section-heading">
            <h2>Create a New Session</h2>
            <p>Describe the goal, rails, inputs, outputs, and constraints. The client keeps control while the request runs.</p>
          </div>
          <form className="form-stack" onSubmit={(event) => void handleCreateSession(event)}>
            <div className="field-grid two-up">
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
            <div className="button-row">
              <button type="submit" className="button button-primary" disabled={!bootstrap.llm_enabled || !message.trim() || Boolean(busyMessage)}>
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
      <div className="status-banner status-active full-width-banner">
        <span className="spinner" aria-hidden="true"></span>
        <strong>Loading wizard session...</strong>
      </div>
    )
  }

  if (!session) {
    return (
      <div className="status-banner status-error full-width-banner">
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
    <div className="page-stack">
      <section className="hero-panel hero-panel--session">
        <div>
          <p className="eyebrow">Session {session.id}</p>
          <h1>{WIZARD_STEP_META[currentStep].label}</h1>
          <p className="hero-copy">{bannerForSession(session)}</p>
        </div>
        <div className="hero-card">
          <p className="hero-card__label">Current status</p>
          <StatusPill tone={statusTone(session.status)}>{session.status.replaceAll('_', ' ')}</StatusPill>
          <span>{session.llm_provider ?? bootstrap.llm_provider}</span>
        </div>
      </section>

      {busyMessage ? (
        <div className="status-banner status-active">
          <span className="spinner" aria-hidden="true"></span>
          <strong>{busyMessage}</strong>
        </div>
      ) : null}
      {errorMessage ? <div className="status-banner status-error"><strong>{errorMessage}</strong></div> : null}
      {session.error?.message ? <div className="status-banner status-error"><strong>{session.error.message}</strong></div> : null}

      <section className="panel panel-soft">
        <ol className="step-tracker">
          {(Object.keys(WIZARD_STEP_META) as WizardStep[]).map((wizardStep, index) => {
            const unlocked = wizardStepUnlocked(session, wizardStep)
            const active = currentStep === wizardStep
            return (
              <li key={wizardStep} className={`step-chip ${active ? 'is-active' : unlocked ? 'is-complete' : 'is-upcoming'}`}>
                <span>{index + 1}</span>
                <div>
                  {unlocked ? (
                    <Link to={`/wizard/${session.id}/${wizardStep}`}>{WIZARD_STEP_META[wizardStep].label}</Link>
                  ) : (
                    <strong>{WIZARD_STEP_META[wizardStep].label}</strong>
                  )}
                  <p>{WIZARD_STEP_META[wizardStep].summary}</p>
                </div>
              </li>
            )
          })}
        </ol>
      </section>

      <div className="dashboard-grid dashboard-grid--wizard">
        <aside className="panel panel-soft panel-sidebar">
          <div className="section-heading compact-heading">
            <h2>Session</h2>
          </div>
          <dl className="detail-list">
            <dt>Project</dt>
            <dd>{session.project_name ?? session.spec?.project_name ?? 'Not set'}</dd>
            <dt>Provider</dt>
            <dd>{session.llm_provider ?? bootstrap.llm_provider}</dd>
            <dt>Updated</dt>
            <dd>{session.updated_at}</dd>
          </dl>
          <div className="sidebar-actions">
            <Link className="button button-secondary" to="/wizard">
              New Session
            </Link>
            {session.latest_job_id ? (
              <Link className="button button-secondary" to={`/jobs/${session.latest_job_id}`}>
                Open Job
              </Link>
            ) : null}
          </div>
        </aside>

        <div className="stack-column">
          {currentStep === 'describe' ? (
            <>
              <section className="panel panel-accent panel-form">
                <div className="section-heading">
                  <h2>Describe Circuit</h2>
                  <p>Keep refining the prompt until the spec is ready for review.</p>
                </div>
                <form className="form-stack" onSubmit={(event) => void handleSendMessage(event)}>
                  <div className="field-grid two-up">
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
                  <div className="button-row">
                    <button type="submit" className="button button-primary" disabled={!bootstrap.llm_enabled || !message.trim() || Boolean(busyMessage)}>
                      {session.messages.length > 1 ? 'Send Revision Note' : 'Start Wizard'}
                    </button>
                  </div>
                </form>
              </section>

              <section className="panel panel-soft">
                <div className="section-heading compact-heading">
                  <h2>Conversation</h2>
                </div>
                <div className="transcript-list">
                  {session.messages.map((entry, index) => (
                    <article key={`${entry.role}-${index}`} className={`transcript-entry transcript-entry--${entry.role}`}>
                      <strong>{entry.role === 'user' ? 'You' : 'Wizard'}</strong>
                      <p>{entry.content}</p>
                    </article>
                  ))}
                </div>
              </section>
            </>
          ) : null}

          {currentStep === 'spec' && session.spec ? (
            <>
              <section className="panel panel-accent">
                <div className="section-heading">
                  <h2>Spec Review</h2>
                  <p>Review the drafted circuit spec before allowing Circuit IR generation.</p>
                </div>
                <div className="spec-grid">
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
                  <div className="spec-grid__full">
                    <h3>Blocks</h3>
                    <BlockList blocks={session.spec.blocks} />
                  </div>
                </div>
              </section>

              <section className="panel panel-soft panel-form">
                <div className="section-heading compact-heading">
                  <h2>Revise or Approve</h2>
                </div>
                <form className="form-stack" onSubmit={(event) => void handleSendMessage(event)}>
                  <label>
                    <span>Revision Note</span>
                    <textarea rows={6} value={message} onChange={(event) => setMessage(event.target.value)} />
                  </label>
                  <div className="button-row wrap-row">
                    <button type="submit" className="button button-secondary" disabled={!message.trim() || Boolean(busyMessage)}>
                      Send Revision Note
                    </button>
                    <button type="button" className="button button-primary" disabled={!canApproveSpec || Boolean(busyMessage)} onClick={() => void handleApproveSpec()}>
                      Approve Spec
                    </button>
                  </div>
                </form>
              </section>
            </>
          ) : null}

          {currentStep === 'ir' ? (
            <>
              <section className="panel panel-accent">
                <div className="section-heading">
                  <h2>Circuit IR</h2>
                  <p>Generate the IR from the approved spec and inspect validation before creating a project.</p>
                </div>
                {session.ir_validation ? (
                  <dl className="detail-list detail-list--grid">
                    <dt>Valid</dt>
                    <dd>{session.ir_validation.valid ? 'Yes' : 'No'}</dd>
                    <dt>Auto-fixed</dt>
                    <dd>{session.ir_validation.auto_fixed ? 'Yes' : 'No'}</dd>
                    <dt>Components</dt>
                    <dd>{session.ir_validation.component_count}</dd>
                    <dt>Nets</dt>
                    <dd>{session.ir_validation.net_count}</dd>
                    <dt>Symbols Dirs Used</dt>
                    <dd>{session.ir_validation.symbols_dirs_used.join(', ') || 'None'}</dd>
                  </dl>
                ) : (
                  <p className="empty-copy">No Circuit IR draft yet.</p>
                )}
                {session.ir_validation?.error_message ? (
                  <div className="status-banner status-warning">
                    <strong>{session.ir_validation.error_message}</strong>
                  </div>
                ) : null}
                <div className="button-row">
                  <button type="button" className="button button-primary" disabled={!canGenerateIr || Boolean(busyMessage)} onClick={() => void handleGenerateIr()}>
                    {session.status === 'ir_needs_repair' ? 'Repair Circuit IR' : 'Generate Circuit IR'}
                  </button>
                </div>
              </section>
              {session.ir_validation?.warnings.length ? <JsonPanel title="Validation Warnings" payload={session.ir_validation.warnings} /> : null}
              {session.ir_json ? <JsonPanel title="Raw Circuit IR JSON" payload={session.ir_json} /> : null}
            </>
          ) : null}

          {currentStep === 'generate' ? (
            <>
              <section className="panel panel-accent">
                <div className="section-heading">
                  <h2>Generate Project</h2>
                  <p>Use the validated Circuit IR as the handoff into the deterministic generation pipeline.</p>
                </div>
                <div className="button-row">
                  <button type="button" className="button button-primary" disabled={!canGenerateProject || Boolean(busyMessage)} onClick={() => void handleGenerateProject()}>
                    {visibleLatestJob ? 'Generate Again' : 'Generate Project'}
                  </button>
                </div>
              </section>
              {visibleLatestJob ? <JobSummaryPanel job={visibleLatestJob} /> : <p className="empty-copy">No generation job linked yet.</p>}
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
      <div className="status-banner status-error full-width-banner">
        <strong>Job id is required.</strong>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="status-banner status-active full-width-banner">
        <span className="spinner" aria-hidden="true"></span>
        <strong>Loading job detail...</strong>
      </div>
    )
  }

  if (!job) {
    return (
      <div className="status-banner status-error full-width-banner">
        <strong>{errorMessage ?? 'Job not found.'}</strong>
      </div>
    )
  }

  const result = asRecord(job.result)
  const warnings = Array.isArray(result.warnings) ? result.warnings : []
  const diagnostics = result.generated_schematic_diagnostics ?? { message: 'No diagnostic summary recorded.' }

  return (
    <div className="page-stack">
      <section className="hero-panel hero-panel--job">
        <div>
          <p className="eyebrow">Job {job.id}</p>
          <h1>{job.project_name}</h1>
          <p className="hero-copy">Inspect the full generation result, artifacts, diagnostics, and raw payloads.</p>
        </div>
        <div className="hero-card">
          <p className="hero-card__label">Status</p>
          <StatusPill tone={statusTone(job.status)}>{job.status}</StatusPill>
          <span>{job.updated_at}</span>
        </div>
      </section>

      <div className="dashboard-grid dashboard-grid--results">
        <section className="panel panel-soft">
          <div className="section-heading compact-heading">
            <h2>Artifacts</h2>
          </div>
          {job.artifacts.length ? (
            <div className="button-row wrap-row">
              {job.artifacts.map((artifact) => (
                <a key={artifact} className="button button-secondary" href={`/api/jobs/${job.id}/artifacts/${artifact}`}>
                  {artifact}
                </a>
              ))}
            </div>
          ) : (
            <p className="empty-copy">No artifacts available.</p>
          )}
        </section>
        <section className="panel panel-soft">
          <div className="section-heading compact-heading">
            <h2>Summary</h2>
          </div>
          <dl className="detail-list detail-list--grid">
            <dt>Created</dt>
            <dd>{job.created_at}</dd>
            <dt>Updated</dt>
            <dd>{job.updated_at}</dd>
            <dt>Components</dt>
            <dd>{String(result.component_count ?? '—')}</dd>
            <dt>Nets</dt>
            <dd>{String(result.net_count ?? '—')}</dd>
          </dl>
        </section>
      </div>

      <div className="dashboard-grid dashboard-grid--results">
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
        <div className={`status-banner ${errorMessage ? 'status-error' : 'status-active'} full-width-banner`}>
          {errorMessage ? (
            <strong>{errorMessage}</strong>
          ) : (
            <>
              <span className="spinner" aria-hidden="true"></span>
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