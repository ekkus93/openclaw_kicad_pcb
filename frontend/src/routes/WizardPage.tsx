import React, { startTransition, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, api } from '../api'
import type {
  CircuitBlockSpec,
  CircuitPortSpec,
  CircuitRailSpec,
  JobDetail,
  UiBootstrapResponse,
  WizardGenerateProjectResponse,
  WizardSessionDetail,
  WizardStatus,
  WizardStep,
} from '../types'

import {
  bannerBaseClass,
  blockCardClass,
  blockGridClass,
  buttonDangerClass,
  buttonPrimaryClass,
  buttonRowClass,
  buttonSecondaryClass,
  composerCardClass,
  composerMetaRowClass,
  compactListItemClass,
  compactStatusRowClass,
  compactSupportCopyClass,
  detailListGridClass,
  emptyCopyClass,
  eyebrowClass,
  headingGroupClass,
  helpTextClass,
  heroLeadClass,
  mutedCopyClass,
  pageStackClass,
  panelAccentClass,
  panelSoftClass,
  recordListClass,
  spinnerClass,
  stackColumnClass,
  transcriptBodyClass,
  transcriptEntryClass,
  transcriptListClass,
  transcriptMetaClass,
  wizardActionRowClass,
  wizardFieldGridClass,
  wizardFieldSectionClass,
  wizardFormClass,
  wizardPrimaryButtonClass,
  wizardTextareaClass,
  workflowStepItemClass,
  workflowStepListClass,
} from '../styles/designTokens'

// ─── Constants ────────────────────────────────────────────────────────────────

const WIZARD_STEP_ORDER: Record<WizardStep, number> = {
  describe: 0,
  spec: 1,
  ir: 2,
  generate: 3,
}

const WIZARD_STEP_META: Record<WizardStep, { label: string; abbrev: string; summary: string }> = {
  describe: { label: 'Describe Circuit', abbrev: 'Describe', summary: 'Start a session and refine the brief.' },
  spec: { label: 'Review Spec', abbrev: 'Spec', summary: 'Approve or revise the drafted specification.' },
  ir: { label: 'Review Circuit IR', abbrev: 'IR', summary: 'Generate, validate, and inspect the IR payload.' },
  generate: { label: 'Generate Project', abbrev: 'Generate', summary: 'Launch the deterministic KiCad generation path.' },
}

const LS_LAST_SESSION = 'lastWizardSession'

// ─── Utility functions ────────────────────────────────────────────────────────

function statusLabel(status: WizardStatus | string): string {
  const labels: Record<string, string> = {
    drafting_spec: 'Drafting spec…',
    awaiting_user_clarification: 'Your input needed',
    spec_ready_for_review: 'Spec ready for review',
    spec_approved: 'Spec approved',
    drafting_ir: 'Generating Circuit IR…',
    ir_needs_repair: 'IR needs repair',
    ir_ready_for_generation: 'IR ready',
    generation_started: 'Generating project…',
    completed: 'Completed',
    failed: 'Failed',
    succeeded: 'Succeeded',
    queued: 'Queued',
    running: 'Running',
    cancelled: 'Cancelled',
  }
  return labels[status] ?? String(status).replaceAll('_', ' ')
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

function displaySymbolsDir(raw: string): string {
  if (!raw) return 'None'
  const normalized = raw.replace(/\\/g, '/')
  if (normalized.includes('/resources/symbols') || normalized.includes('kicad_pcb/resources')) {
    return 'Built-in symbols'
  }
  const parts = normalized.split('/')
  return parts[parts.length - 1] || parts[parts.length - 2] || raw
}

function joinClasses(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
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

function readLastSession(): string | null {
  try { return localStorage.getItem(LS_LAST_SESSION) } catch { return null }
}

function writeLastSession(id: string): void {
  try { localStorage.setItem(LS_LAST_SESSION, id) } catch { /* ignore */ }
}

// ─── Status helpers ───────────────────────────────────────────────────────────

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

// ─── Wizard step helpers ──────────────────────────────────────────────────────

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

function wizardStepState(session: WizardSessionDetail, currentStep: WizardStep, step: WizardStep): 'current' | 'done' | 'ready' | 'locked' {
  if (currentStep === step) {
    return 'current'
  }
  if (WIZARD_STEP_ORDER[step] < WIZARD_STEP_ORDER[currentStep]) {
    return 'done'
  }
  if (wizardStepUnlocked(session, step)) {
    return 'ready'
  }
  return 'locked'
}

function wizardCurrentCheckpoint(session: WizardSessionDetail, step: WizardStep): { title: string; detail: string } {
  if (step === 'describe') {
    return {
      title: 'Describe the circuit in enough detail to draft a reviewable spec.',
      detail:
        session.status === 'awaiting_user_clarification'
          ? 'Answer the missing questions directly in the request box so the next spec draft closes the open gaps.'
          : 'Include the circuit purpose, rails, I/O, and constraints. Keep iterating until the spec route unlocks.',
    }
  }
  if (step === 'spec') {
    return {
      title: 'Treat this as the approval gate before any IR is generated.',
      detail:
        session.open_questions.length || session.unsupported_reasons.length
          ? 'Resolve every open question and unsupported reason before approving the spec.'
          : 'If the purpose, blocks, ports, rails, and constraints all match intent, approve the spec to unlock IR generation.',
    }
  }
  if (step === 'ir') {
    return {
      title: 'Generate and inspect Circuit IR before handing off generation.',
      detail: session.ir_validation?.valid
        ? 'The current IR validates cleanly. Review counts and warnings, then move to project generation.'
        : 'Run IR generation, inspect validation, and repair any warnings or invalid output before continuing.',
    }
  }
  return {
    title: 'Use the validated IR as the deterministic handoff into project generation.',
    detail: session.latest_job_id
      ? 'A job already exists for this session. Review the latest artifacts or rerun generation if needed.'
      : 'Once the IR is valid, generate the project and review artifacts and diagnostics on the linked job.',
  }
}

// ─── Small UI components ──────────────────────────────────────────────────────

function StatusPill({ tone, children }: { tone: ReturnType<typeof statusTone>; children: string }) {
  return (
    <span
      className={joinClasses(
        'inline-flex items-center rounded-full px-[0.7rem] py-[0.35rem] text-[0.83rem] font-bold',
        statusPillToneClass(tone),
      )}
    >
      {children}
    </span>
  )
}


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

function WarningCard({ warning }: { warning: Record<string, unknown> }) {
  const code = typeof warning.code === 'string' ? warning.code : null
  const message = typeof warning.message === 'string' ? warning.message : null
  const severity = typeof warning.severity === 'string' ? warning.severity : 'warning'
  const family = typeof warning.family === 'string' ? warning.family : null
  const details = warning.details && typeof warning.details === 'object' && !Array.isArray(warning.details)
    ? (warning.details as Record<string, unknown>)
    : null

  const severityColors = {
    error:   'border-[rgba(154,45,40,0.2)]  bg-[rgba(154,45,40,0.05)]',
    warning: 'border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.05)]',
    info:    'border-[rgba(22,93,143,0.15)]  bg-[rgba(22,93,143,0.05)]',
  }
  const badgeColors = {
    error:   'bg-[rgba(154,45,40,0.1)]  text-[var(--error)]',
    warning: 'bg-[rgba(155,106,18,0.12)] text-[var(--warning)]',
    info:    'bg-[rgba(22,93,143,0.1)]  text-[#0d4c74]',
  }
  const border = severityColors[severity as keyof typeof severityColors] ?? severityColors.warning
  const badge  = badgeColors[severity as keyof typeof badgeColors] ?? badgeColors.warning

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

type DiagnosticsPayload = {
  symbol_count?: number
  wire_count?: number
  label_count?: number
  global_label_count?: number
  junction_count?: number
  binding_marker_count?: number
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
          <li>
            <strong>{symbols}</strong>{' '}
            {symbols === 1 ? 'component' : 'components'} placed in the schematic
          </li>
          <li>
            <strong>{wires}</strong>{' '}
            wire {wires === 1 ? 'connection' : 'connections'} routed between pins
          </li>
          {labels > 0 ? (
            <li>
              <strong>{labels}</strong>{' '}
              net {labels === 1 ? 'label' : 'labels'} added to identify signal connections
            </li>
          ) : null}
        </ul>
        {hasIssues ? (
          <div className="grid gap-3">
            {hardFailures.map((f, i) => (
              <div key={i} className="rounded-[14px] border border-[rgba(154,45,40,0.2)] bg-[rgba(154,45,40,0.05)] p-3">
                <p className="text-[0.82rem] font-bold text-[var(--error)]">Generation error</p>
                {f.message ? (
                  <p className="mt-1 text-[0.92rem] leading-6 text-[var(--text)]">{f.message}</p>
                ) : null}
                {f.code ? (
                  <p className="mt-1 text-[0.78rem] text-[var(--muted)]">Code: {f.code}</p>
                ) : null}
              </div>
            ))}
            {missingBindings.length > 0 ? (
              <div className="rounded-[14px] border border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.05)] p-3">
                <p className="text-[0.82rem] font-bold text-[var(--warning)]">Missing net connections</p>
                <p className="mt-1 text-[0.88rem] leading-6 text-[var(--muted)]">
                  These nets were declared in the IR but have no corresponding wire or label in the schematic:{' '}
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
                  These nets appear more than once in the schematic:{' '}
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

// ─── JSON tree viewer ────────────────────────────────────────────────────────

const ReactJsonView = React.lazy(() => import('@microlink/react-json-view'))

function JsonTreeViewer({ value }: { value: unknown }) {
  if (!value || typeof value !== 'object') return null
  return (
    <div className="overflow-auto rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,253,248,0.95)] p-4 text-[0.85rem]">
      <React.Suspense fallback={<p className="text-[var(--muted)] text-sm">Loading viewer…</p>}>
        <ReactJsonView
          src={value as object}
          theme="rjv-default"
          iconStyle="triangle"
          collapsed={2}
          collapseStringsAfterLength={80}
          displayDataTypes={false}
          displayObjectSize={true}
          enableClipboard={true}
          style={{
            backgroundColor: 'transparent',
            fontFamily: 'var(--font-mono)',
            fontSize: '0.85rem',
            lineHeight: '1.7',
          }}
        />
      </React.Suspense>
    </div>
  )
}

function NotFoundScreen({ heading, message }: { heading: string; message?: string }) {
  return (
    <div className={pageStackClass}>
      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h1>{heading}</h1>
          {message ? <p className={mutedCopyClass}>{message}</p> : null}
        </div>
        <div className={buttonRowClass}>
          <Link className={buttonPrimaryClass} to="/wizard">
            ← Back to Wizard
          </Link>
        </div>
      </section>
    </div>
  )
}

function LabelList({ items }: { items: string[] }) {
  if (!items.length) {
    return <p className={emptyCopyClass}>None recorded.</p>
  }
  return (
    <ul className="m-0 grid list-disc gap-1.5 pl-5">
      {items.map((item) => (
        <li key={item} className="text-[0.95rem] leading-6 text-[var(--text)]">
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

// ─── Transcript / Composer ────────────────────────────────────────────────────

function TranscriptEntryCard({
  content,
  index,
  role,
}: {
  content: string
  index: number
  role: 'user' | 'assistant' | string
}) {
  const isUser = role === 'user'
  return (
    <article
      className={joinClasses(
        transcriptEntryClass,
        isUser
          ? 'ml-auto border-[rgba(22,93,143,0.14)] bg-[linear-gradient(180deg,rgba(236,245,243,0.96),rgba(226,240,238,0.88))]'
          : 'mr-auto border-[rgba(161,69,26,0.14)] bg-[linear-gradient(180deg,rgba(255,245,228,0.94),rgba(251,239,217,0.9))]',
      )}
    >
      <div className={transcriptMetaClass}>
        <span className={joinClasses('inline-flex items-center rounded-full px-2.5 py-1', isUser ? 'bg-[rgba(22,93,143,0.1)] text-[#0d4c74]' : 'bg-[rgba(161,69,26,0.1)] text-[var(--brand-deep)]')}>
          {isUser ? 'You' : 'Wizard'}
        </span>
        <span>Turn {index + 1}</span>
      </div>
      <p className={transcriptBodyClass}>{content}</p>
    </article>
  )
}

function WizardComposer({
  label,
  placeholder,
  value,
  onChange,
  disabled,
  submitLabel,
  submitDisabled,
}: {
  label: string
  placeholder: string
  value: string
  onChange: (value: string) => void
  disabled: boolean
  submitLabel?: string
  submitDisabled?: boolean
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    const node = textareaRef.current
    if (!node) {
      return
    }
    node.style.height = '0px'
    node.style.height = `${Math.min(node.scrollHeight, 320)}px`
  }, [value])

  return (
    <div className={composerCardClass}>
      <label>
        <span>{label}</span>
        <textarea
          ref={textareaRef}
          className={wizardTextareaClass}
          data-wizard-input="true"
          disabled={disabled}
          placeholder={placeholder}
          required
          rows={1}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }}
        />
      </label>
      <div className={composerMetaRowClass}>
        {disabled ? (
          <span>Waiting for response…</span>
        ) : (
          <span className="text-[var(--muted)]">Ctrl/Cmd+Enter to send</span>
        )}
        {submitLabel ? (
          <button
            type="submit"
            className={joinClasses(
              'rounded-full px-4 py-1.5 text-sm font-semibold transition-[transform,opacity,background-color] duration-150 hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-[0.55] disabled:transform-none',
              'bg-[linear-gradient(135deg,var(--brand)_0%,var(--brand-deep)_100%)] text-[#fff8f1] shadow-[0_4px_12px_rgba(109,47,20,0.2)]',
            )}
            disabled={submitDisabled ?? disabled}
          >
            {submitLabel}
          </button>
        ) : null}
      </div>
    </div>
  )
}

// ─── WizardBreadcrumb ─────────────────────────────────────────────────────────

function WizardBreadcrumb({
  session,
  currentStep,
  sessionId,
}: {
  session: WizardSessionDetail
  currentStep: WizardStep
  sessionId: string
}) {
  return (
    <nav
      aria-label="Wizard steps"
      className="mb-2 rounded-[20px] border border-[var(--border)] bg-[rgba(255,252,247,0.9)] px-4 py-3 shadow-[0_6px_16px_rgba(71,43,19,0.06)]"
    >
      <ol className="flex flex-wrap items-center gap-1">
        {(Object.keys(WIZARD_STEP_META) as WizardStep[]).map((step, idx, arr) => {
          const state = wizardStepState(session, currentStep, step)
          const isLast = idx === arr.length - 1
          const pill = (
            <span
              aria-current={state === 'current' ? 'step' : undefined}
              className={joinClasses(
                'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[0.82rem] font-medium transition-colors duration-150',
                state === 'current'
                  ? 'bg-[rgba(161,69,26,0.14)] text-[var(--brand-deep)]'
                  : state === 'done'
                    ? 'text-[var(--success)] hover:bg-[rgba(35,102,79,0.08)]'
                    : state === 'ready'
                      ? 'text-[#0d4c74] hover:bg-[rgba(22,93,143,0.08)]'
                      : 'text-[var(--muted)]',
              )}
            >
              <span
                className={joinClasses(
                  'flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-[0.68rem] font-bold',
                  state === 'current'
                    ? 'bg-[var(--brand)] text-white'
                    : state === 'done'
                      ? 'bg-[var(--success)] text-white'
                      : 'bg-[rgba(117,99,80,0.15)] text-[var(--muted)]',
                )}
              >
                {state === 'done' ? '✓' : idx + 1}
              </span>
              {WIZARD_STEP_META[step].abbrev}
            </span>
          )
          return (
            <li key={step} className="flex items-center">
              {state === 'locked' || state === 'current' ? (
                pill
              ) : (
                <Link className="no-underline" to={`/wizard/${sessionId}/${step}`}>
                  {pill}
                </Link>
              )}
              {!isLast && (
                <span aria-hidden className="mx-1 select-none text-[var(--muted)]">
                  ›
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

// ─── JobSummaryPanel ──────────────────────────────────────────────────────────

function JobSummaryPanel({ job, sessionId }: { job: JobDetail; sessionId?: string }) {
  const result = asRecord(job.result)
  const warnings = Array.isArray(result.warnings) ? result.warnings : []
  const diagnostics = result.generated_schematic_diagnostics ?? null
  const jobPath = sessionId ? `/jobs/${job.id}?from=${encodeURIComponent(sessionId)}` : `/jobs/${job.id}`
  const hasPreview = job.artifacts.includes('schematic_preview.png')
  const downloadArtifacts = job.artifacts.filter((a) => a !== 'schematic_preview.png' && !a.endsWith('.svg'))
  return (
    <div className={stackColumnClass}>
      {hasPreview ? (
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Schematic Preview</h2>
            <p className={mutedCopyClass}>
              Generated from <code className="rounded bg-[rgba(88,63,39,0.08)] px-1 py-0.5 text-[0.85rem]">OpenClaw_Managed.kicad_sch</code>
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
      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h2>Latest Job</h2>
        </div>
        <dl className={detailListGridClass}>
          <dt className={mutedCopyClass}>Project</dt>
          <dd>{job.project_name}</dd>
          <dt className={mutedCopyClass}>Status</dt>
          <dd>
            <StatusPill tone={statusTone(job.status)}>{statusLabel(job.status)}</StatusPill>
          </dd>
          <dt className={mutedCopyClass}>Updated</dt>
          <dd>{formatDate(job.updated_at)}</dd>
        </dl>
        <div className={buttonRowClass}>
          <Link className={buttonSecondaryClass} to={jobPath}>
            Open Job Detail
          </Link>
          {downloadArtifacts.length > 0
            ? downloadArtifacts.map((artifact) => (
                <a key={artifact} className={buttonSecondaryClass} href={`/api/jobs/${job.id}/artifacts/${artifact}`}>
                  {artifact}
                </a>
              ))
            : null}
        </div>
        {job.artifacts.length === 0 ? (
          <p className={compactSupportCopyClass}>No artifacts — generation did not complete.</p>
        ) : null}
      </section>
      <WarningsPanel warnings={warnings} />
      {diagnostics ? <BuildSummaryPanel diagnostics={diagnostics as DiagnosticsPayload} /> : null}
    </div>
  )
}

// ─── WizardPage ───────────────────────────────────────────────────────────────

export function WizardPage({ bootstrap }: { bootstrap: UiBootstrapResponse }) {
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
  const [metaExpanded, setMetaExpanded] = useState(false)
  const loading = Boolean(sessionId) && loadedSessionId !== sessionId && failedSessionId !== sessionId

  useEffect(() => {
    if (sessionId) {
      writeLastSession(sessionId)
    }
  }, [sessionId])

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

  useEffect(() => {
    const base = 'KiCad PCB Web App'
    if (!sessionId) {
      document.title = `New Session — ${base}`
      return
    }
    if (session) {
      const projectLabel = session.project_name ?? session.spec?.project_name ?? null
      const stepLabel = WIZARD_STEP_META[currentStep].label
      document.title = projectLabel
        ? `${stepLabel} — ${projectLabel} — ${base}`
        : `${stepLabel} — ${base}`
    }
  }, [currentStep, session, sessionId])

  async function handleCreateSession(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setBusyMessage(`Talking to ${bootstrap.llm_provider} to draft the first spec…`)
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
      writeLastSession(response.id)
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
    setBusyMessage(`Talking to ${bootstrap.llm_provider} to revise the spec draft…`)
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
    setBusyMessage('Locking this spec checkpoint and moving to Circuit IR…')
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
    setBusyMessage('Generating Circuit IR… the backend will attempt automatic repair if needed.')
    setErrorMessage(null)
    try {
      const response = await api.generateWizardIr(session.id)
      if (response.status === 'ir_needs_repair') {
        setErrorMessage(
          'The backend could not produce valid Circuit IR after its repair passes. ' +
          'Review the error below, then click "Repair Circuit IR" to try again or ' +
          'go back to the spec and revise the circuit description.',
        )
      }
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

  async function handleClearIr(): Promise<void> {
    if (!session) {
      return
    }
    setBusyMessage('Clearing Circuit IR…')
    setErrorMessage(null)
    try {
      const response = await api.clearWizardIr(session.id)
      setSession(response)
      setLatestJob(null)
      startTransition(() => {
        navigate(`/wizard/${response.id}/ir`)
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
    if (
      visibleLatestJob?.status === 'succeeded' &&
      !window.confirm('This will replace the current job result. Continue?')
    ) {
      return
    }
    setBusyMessage('Generating the KiCad project from the validated Circuit IR…')
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

  // ── Wizard start page (no session) ──────────────────────────────────────────
  if (!sessionId) {
    return (
      <div className={pageStackClass}>
        <section className={panelAccentClass}>
          <div className={headingGroupClass}>
            <p className={eyebrowClass}>Wizard</p>
            <h1>Start a circuit session.</h1>
            <p className={heroLeadClass}>Describe the circuit. Review the spec. Validate the IR. Generate the project.</p>
          </div>
          <ol className={workflowStepListClass}>
            <li className={workflowStepItemClass}>1. Describe the circuit.</li>
            <li className={workflowStepItemClass}>2. Approve the drafted spec.</li>
            <li className={workflowStepItemClass}>3. Validate the Circuit IR.</li>
            <li className={workflowStepItemClass}>4. Generate the KiCad project.</li>
          </ol>
          <div className="rounded-[18px] border border-[rgba(88,63,39,0.12)] bg-[rgba(255,255,255,0.5)] p-4">
            <p className="mb-1.5 text-[0.75rem] font-bold uppercase tracking-[0.12em] text-[var(--muted)]">
              Example prompt
            </p>
            <p className="font-[var(--font-mono)] text-[0.85rem] leading-6 text-[var(--text)] whitespace-pre-wrap">{`Make a 555 timer LED blinker.
Supply: 5V.
Output: one LED.
Constraints: through-hole parts, use NE555, about 1 Hz blink rate.`}</p>
          </div>
          <div className={compactStatusRowClass}>
            <StatusPill tone={bootstrap.llm_enabled ? 'success' : 'neutral'}>
              {bootstrap.llm_enabled ? 'Provider ready' : 'Provider disabled'}
            </StatusPill>
            <span className={compactSupportCopyClass}>{bootstrap.llm_provider}</span>
          </div>

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

          <form className={wizardFormClass} onSubmit={(event) => void handleCreateSession(event)}>
            <div className={wizardFieldSectionClass}>
              <label>
                <span>Project Name</span>
                <input
                  placeholder="e.g. LED Blinker"
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                />
              </label>
              <label>
                <span>Symbols Directory <span className="font-normal text-[var(--muted)]">(optional)</span></span>
                <input
                  placeholder="Leave blank to use built-in symbols"
                  value={symbolsDir}
                  onChange={(event) => setSymbolsDir(event.target.value)}
                />
              </label>
              <p className={helpTextClass}>
                Symbols Directory: absolute path to a KiCad symbol library directory.
                Leave blank to use the built-in symbols.
              </p>
            </div>
            <div className={wizardFieldSectionClass}>
              <WizardComposer
                disabled={Boolean(busyMessage)}
                label="Circuit Request"
                placeholder="Goal, rails, inputs, outputs, constraints."
                value={message}
                onChange={setMessage}
                submitLabel="Start Session"
                submitDisabled={!bootstrap.llm_enabled || !message.trim() || Boolean(busyMessage)}
              />
            </div>
            {!bootstrap.llm_enabled ? (
              <p className={helpTextClass}>
                No LLM provider is configured. Set a provider in{' '}
                <code className="rounded bg-[rgba(88,63,39,0.08)] px-1 py-0.5">kicad_pcb_web.toml</code>{' '}
                to enable the wizard.
              </p>
            ) : null}
          </form>
          {!bootstrap.llm_enabled ? (
            <div className="mt-4 rounded-[18px] border border-[rgba(88,63,39,0.12)] bg-[rgba(255,255,255,0.5)] p-4">
              <p className="mb-3 text-[0.82rem] font-semibold text-[var(--muted)]">
                You can still use these workflows without an LLM provider:
              </p>
              <div className="flex flex-wrap gap-2">
                <Link className={buttonSecondaryClass} to="/generate-json">
                  Generate from Circuit IR JSON
                </Link>
                <Link className={buttonSecondaryClass} to="/jobs">
                  View Recent Jobs
                </Link>
                <Link className={buttonSecondaryClass} to="/setup">
                  Check Setup
                </Link>
              </div>
            </div>
          ) : null}
        </section>
      </div>
    )
  }

  // ── Session loading / error states ──────────────────────────────────────────
  if (loading) {
    return (
      <div className={joinClasses(bannerBaseClass, statusBannerToneClass('active'), 'mt-4')}>
        <span className={spinnerClass} aria-hidden="true"></span>
        <strong>Loading wizard session…</strong>
      </div>
    )
  }

  if (!session) {
    return (
      <NotFoundScreen
        heading="Session not found"
        message={errorMessage ?? 'This wizard session does not exist or could not be loaded.'}
      />
    )
  }

  const hasUnspecifiedCustomBlocks =
    session.spec?.blocks.some(
      (b) => b.block_type === 'custom' && b.required_components.length === 0,
    ) ?? false
  const canApproveSpec =
    Boolean(session.spec) &&
    !session.spec_approved &&
    !session.open_questions.length &&
    !session.unsupported_reasons.length &&
    !hasUnspecifiedCustomBlocks
  const canGenerateIr = Boolean(session.spec) && session.spec_approved
  const canGenerateProject = Boolean(session.ir_validation?.valid)
  const visibleLatestJob = session.latest_job_id ? latestJob : null
  const checkpoint = wizardCurrentCheckpoint(session, currentStep)
  const projectLabel = session.project_name ?? session.spec?.project_name ?? null

  return (
    <div className={pageStackClass}>
      <WizardBreadcrumb currentStep={currentStep} session={session} sessionId={session.id} />

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

      {/* ── Describe step ──────────────────────────────────────────────────── */}
      {currentStep === 'describe' ? (
        <>
          <section className={panelAccentClass}>
            <div className={headingGroupClass}>
              <p className={eyebrowClass}>
                Step 1 of 4{projectLabel ? ` — ${projectLabel}` : ''}
              </p>
              <h1>Describe Circuit</h1>
              <p className={heroLeadClass}>{checkpoint.title}</p>
              <p className={compactSupportCopyClass}>{checkpoint.detail}</p>
            </div>
            <div className={compactStatusRowClass}>
              <StatusPill tone={statusTone(session.status)}>
                {statusLabel(session.status)}
              </StatusPill>
              <span className={compactSupportCopyClass}>
                {session.llm_provider ?? bootstrap.llm_provider}
              </span>
            </div>
          </section>

          {session.messages.length > 0 ? (
            <section className={panelSoftClass}>
              <div className={headingGroupClass}>
                <h2>Conversation</h2>
                <p className={mutedCopyClass}>
                  Your notes stay separated from the wizard replies so each turn is easier to scan.
                </p>
              </div>
              <div className={transcriptListClass}>
                {session.messages.map((entry, index) => (
                  <TranscriptEntryCard
                    key={`${entry.role}-${index}`}
                    content={entry.content}
                    index={index}
                    role={entry.role}
                  />
                ))}
              </div>
            </section>
          ) : null}

          <section className={panelSoftClass}>
            <div className={headingGroupClass}>
              <h2>{session.messages.length > 0 ? 'Continue the conversation' : 'Circuit Request'}</h2>
              <p className={mutedCopyClass}>
                {session.messages.length > 0
                  ? 'Add more detail, answer the wizard\'s questions, or refine the brief.'
                  : 'Keep refining the prompt until the spec is ready for review.'}
              </p>
            </div>
            <form className={wizardFormClass} onSubmit={(event) => void handleSendMessage(event)}>
              <div className={wizardFieldSectionClass}>
                <WizardComposer
                  disabled={Boolean(busyMessage)}
                  label="Your message"
                  placeholder="Clarify only the missing or changed details."
                  value={message}
                  onChange={setMessage}
                  submitLabel="Send"
                  submitDisabled={!bootstrap.llm_enabled || !message.trim() || Boolean(busyMessage)}
                />
              </div>
              <div className="flex">
                <button
                  type="button"
                  className="flex items-center gap-1.5 rounded-full px-3 py-2 text-[0.82rem] font-medium text-[var(--accent)] transition-colors hover:bg-[rgba(24,75,69,0.06)]"
                  onClick={() => setMetaExpanded((v) => !v)}
                >
                  <span
                    className={joinClasses(
                      'text-[0.9rem] leading-none transition-transform duration-150',
                      metaExpanded ? 'rotate-90' : '',
                    )}
                    aria-hidden="true"
                  >
                    ▸
                  </span>
                  {metaExpanded ? 'Hide metadata' : 'Edit metadata'}
                </button>
              </div>
              {metaExpanded ? (
                <div className={wizardFieldSectionClass}>
                  <div className={wizardFieldGridClass}>
                    <label>
                      <span>Project Name</span>
                      <input
                        value={projectName}
                        onChange={(event) => setProjectName(event.target.value)}
                      />
                    </label>
                    <label>
                      <span>Symbols Directory <span className="font-normal text-[var(--muted)]">(optional)</span></span>
                      <input
                        placeholder="Leave blank to use built-in symbols"
                        value={symbolsDir}
                        onChange={(event) => setSymbolsDir(event.target.value)}
                      />
                    </label>
                  </div>
                  <p className={helpTextClass}>
                    Adjust metadata only when the session context actually changed.
                  </p>
                </div>
              ) : null}
            </form>
          </section>

          <div className="flex items-center justify-between border-t border-[var(--border)] pt-4">
            <Link className={buttonSecondaryClass} to="/wizard">
              Start New Session
            </Link>
            {wizardStepUnlocked(session, 'spec') ? (
              <Link className={wizardPrimaryButtonClass} to={`/wizard/${session.id}/spec`}>
                Continue to Spec Review →
              </Link>
            ) : null}
          </div>
        </>
      ) : null}

      {/* ── Spec step ──────────────────────────────────────────────────────── */}
      {currentStep === 'spec' && session.spec ? (
        <>
          <section className={panelAccentClass}>
            <div className={headingGroupClass}>
              <p className={eyebrowClass}>
                Step 2 of 4{projectLabel ? ` — ${projectLabel}` : ''}
              </p>
              <h1>Review Spec</h1>
              <p className={heroLeadClass}>{checkpoint.title}</p>
              <p className={compactSupportCopyClass}>{checkpoint.detail}</p>
            </div>
            <div className={compactStatusRowClass}>
              <StatusPill tone={statusTone(session.status)}>
                {statusLabel(session.status)}
              </StatusPill>
            </div>
          </section>

          <section className={panelSoftClass}>
            <div className={headingGroupClass}>
              <h2>Circuit Specification</h2>
            </div>
            {(session.open_questions.length > 0 || session.unsupported_reasons.length > 0) ? (
              <div className="grid gap-3 sm:grid-cols-2">
                {session.open_questions.length > 0 ? (
                  <div className={joinClasses(bannerBaseClass, statusBannerToneClass('warning'), 'flex-col items-start gap-2')}>
                    <strong className="text-[0.8rem] font-bold uppercase tracking-[0.1em]">Open Questions</strong>
                    <LabelList items={session.open_questions} />
                  </div>
                ) : null}
                {session.unsupported_reasons.length > 0 ? (
                  <div className={joinClasses(bannerBaseClass, statusBannerToneClass('error'), 'flex-col items-start gap-2')}>
                    <strong className="text-[0.8rem] font-bold uppercase tracking-[0.1em]">Unsupported</strong>
                    <LabelList items={session.unsupported_reasons} />
                  </div>
                ) : null}
              </div>
            ) : null}

            {session.spec.blocks.some(
              (b) => b.block_type === 'custom' && b.required_components.length === 0,
            ) ? (
              <div className={joinClasses(bannerBaseClass, statusBannerToneClass('warning'), 'flex-col items-start gap-2')}>
                <strong className="text-[0.8rem] font-bold uppercase tracking-[0.1em]">
                  Underspecified blocks — action required before approving
                </strong>
                <p className="text-sm leading-6">
                  {session.spec.blocks
                    .filter((b) => b.block_type === 'custom' && b.required_components.length === 0)
                    .map((b) => b.name)
                    .join(', ')}{' '}
                  {session.spec.blocks.filter(
                    (b) => b.block_type === 'custom' && b.required_components.length === 0,
                  ).length === 1
                    ? 'is marked "custom" with no named component.'
                    : 'are marked "custom" with no named component.'}{' '}
                  The Circuit IR generator will have to invent a circuit for{' '}
                  {session.spec.blocks.filter(
                    (b) => b.block_type === 'custom' && b.required_components.length === 0,
                  ).length === 1
                    ? 'it'
                    : 'them'}
                  , which almost always fails. Go back and tell the wizard which specific
                  component (IC part number) should implement each of these blocks before
                  approving.
                </p>
              </div>
            ) : null}

            <div className="rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.5)] p-4">
              <p className="mb-1 text-[0.75rem] font-bold uppercase tracking-[0.13em] text-[var(--brand)]">Purpose</p>
              <p className="text-[1rem] leading-7">{session.spec.purpose}</p>
            </div>

            <div>
              <p className="mb-2 text-[0.75rem] font-bold uppercase tracking-[0.13em] text-[var(--muted)]">Interface</p>
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.5)] p-4">
                  <p className="mb-2 text-[0.82rem] font-semibold text-[var(--text)]">Inputs</p>
                  <PortList ports={session.spec.inputs} />
                </div>
                <div className="rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.5)] p-4">
                  <p className="mb-2 text-[0.82rem] font-semibold text-[var(--text)]">Outputs</p>
                  <PortList ports={session.spec.outputs} />
                </div>
                <div className="rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.5)] p-4">
                  <p className="mb-2 text-[0.82rem] font-semibold text-[var(--text)]">Supply Rails</p>
                  <RailList rails={session.spec.supply_rails} />
                </div>
              </div>
            </div>

            <div>
              <p className="mb-2 text-[0.75rem] font-bold uppercase tracking-[0.13em] text-[var(--muted)]">Requirements</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.5)] p-4">
                  <p className="mb-2 text-[0.82rem] font-semibold text-[var(--text)]">Acceptance Criteria</p>
                  <LabelList items={session.spec.acceptance_criteria} />
                </div>
                <div className="rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.5)] p-4">
                  <p className="mb-2 text-[0.82rem] font-semibold text-[var(--text)]">Constraints</p>
                  <LabelList items={session.spec.constraints} />
                </div>
              </div>
            </div>

            {session.spec.blocks.length > 0 ? (
              <div>
                <p className="mb-2 text-[0.75rem] font-bold uppercase tracking-[0.13em] text-[var(--muted)]">Architecture</p>
                <BlockList blocks={session.spec.blocks} />
              </div>
            ) : null}
          </section>

          <section className={panelSoftClass}>
            <div className={headingGroupClass}>
              <h2>Revise or Approve</h2>
              <p className={mutedCopyClass}>
                Approve the spec to unlock Circuit IR generation, or send revision notes to refine it first.
              </p>
            </div>
            <form className={wizardFormClass} onSubmit={(event) => void handleSendMessage(event)}>
              <div className={wizardFieldSectionClass}>
                <WizardComposer
                  disabled={Boolean(busyMessage)}
                  label="Revision Note"
                  placeholder="List only the specific spec changes you want."
                  value={message}
                  onChange={setMessage}
                  submitLabel="Send Changes"
                  submitDisabled={!message.trim() || Boolean(busyMessage)}
                />
              </div>
              <div className={wizardActionRowClass}>
                <button
                  type="button"
                  className={wizardPrimaryButtonClass}
                  disabled={!canApproveSpec || Boolean(busyMessage)}
                  onClick={() => void handleApproveSpec()}
                >
                  Approve Spec
                </button>
              </div>
              {!canApproveSpec ? (
                <p className={helpTextClass}>
                  {session.open_questions.length > 0
                    ? 'Approve Spec is locked — resolve the open questions above first.'
                    : session.unsupported_reasons.length > 0
                      ? 'Approve Spec is locked — resolve the unsupported reasons above first.'
                      : hasUnspecifiedCustomBlocks
                        ? 'Approve Spec is locked — go back and ask the wizard to name a specific component for each custom block.'
                        : session.spec_approved
                          ? 'Spec is already approved.'
                          : 'Spec must be generated before it can be approved.'}
                </p>
              ) : null}
            </form>
          </section>

          <div className="flex items-center justify-between border-t border-[var(--border)] pt-4">
            <Link className={buttonSecondaryClass} to={`/wizard/${session.id}/describe`}>
              ← Back to Describe
            </Link>
            {canGenerateIr ? (
              <Link className={wizardPrimaryButtonClass} to={`/wizard/${session.id}/ir`}>
                Continue to Circuit IR →
              </Link>
            ) : null}
          </div>
        </>
      ) : null}

      {/* ── Circuit IR step ────────────────────────────────────────────────── */}
      {currentStep === 'ir' ? (
        <>
          <section className={panelAccentClass}>
            <div className={headingGroupClass}>
              <p className={eyebrowClass}>
                Step 3 of 4{projectLabel ? ` — ${projectLabel}` : ''}
              </p>
              <h1>Circuit IR</h1>
              <p className={heroLeadClass}>{checkpoint.title}</p>
              <p className={compactSupportCopyClass}>{checkpoint.detail}</p>
            </div>
            <div className={compactStatusRowClass}>
              <StatusPill tone={statusTone(session.status)}>
                {statusLabel(session.status)}
              </StatusPill>
            </div>
          </section>

          <section className={panelSoftClass}>
            <div className={headingGroupClass}>
              <h2>
                {session.ir_validation?.valid ? 'Circuit IR — Valid' : 'Generate Circuit IR'}
              </h2>
              <p className={mutedCopyClass}>
                {session.ir_validation?.valid
                  ? 'The IR validates cleanly. Review the counts below, then continue to project generation.'
                  : 'Generate the IR from the approved spec and inspect validation before creating a project.'}
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
                <dt className={mutedCopyClass}>Symbols</dt>
                <dd>
                  {session.ir_validation.symbols_dirs_used.map(displaySymbolsDir).join(', ') || 'None'}
                </dd>
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
              {session.ir_validation?.valid ? (
                <button
                  type="button"
                  className={buttonDangerClass}
                  disabled={!canGenerateIr || Boolean(busyMessage)}
                  onClick={() => void handleGenerateIr()}
                >
                  Regenerate Circuit IR
                </button>
              ) : (
                <button
                  type="button"
                  className={buttonPrimaryClass}
                  disabled={!canGenerateIr || Boolean(busyMessage)}
                  onClick={() => void handleGenerateIr()}
                >
                  {session.status === 'ir_needs_repair' ? 'Repair Circuit IR' : 'Generate Circuit IR'}
                </button>
              )}
              {session.ir_json ? (
                <button
                  type="button"
                  className={buttonDangerClass}
                  disabled={Boolean(busyMessage)}
                  onClick={() => void handleClearIr()}
                >
                  Clear Circuit IR
                </button>
              ) : null}
            </div>
          </section>

          {session.ir_validation?.warnings.length ? (
            <WarningsPanel warnings={session.ir_validation.warnings} />
          ) : null}
          {session.ir_json ? (
            <DisclosurePanel
              title={`Raw Circuit IR JSON — ${session.ir_validation?.component_count ?? '?'} components, ${session.ir_validation?.net_count ?? '?'} nets`}
              defaultOpen={session.ir_validation?.valid === false}
            >
              <JsonTreeViewer value={session.ir_json} />
            </DisclosurePanel>
          ) : null}

          <div className="flex items-center justify-between border-t border-[var(--border)] pt-4">
            <Link className={buttonSecondaryClass} to={`/wizard/${session.id}/spec`}>
              ← Back to Spec
            </Link>
            {canGenerateProject ? (
              <Link className={wizardPrimaryButtonClass} to={`/wizard/${session.id}/generate`}>
                Continue to Generate →
              </Link>
            ) : null}
          </div>
        </>
      ) : null}

      {/* ── Generate step ──────────────────────────────────────────────────── */}
      {currentStep === 'generate' ? (
        <>
          <section className={panelAccentClass}>
            <div className={headingGroupClass}>
              <p className={eyebrowClass}>
                Step 4 of 4{projectLabel ? ` — ${projectLabel}` : ''}
              </p>
              <h1>Generate Project</h1>
              <p className={heroLeadClass}>{checkpoint.title}</p>
              <p className={compactSupportCopyClass}>{checkpoint.detail}</p>
            </div>
            <div className={compactStatusRowClass}>
              <StatusPill tone={statusTone(session.status)}>
                {statusLabel(session.status)}
              </StatusPill>
            </div>
          </section>

          <section className={panelSoftClass}>
            <div className={headingGroupClass}>
              <h2>Generate KiCad Project</h2>
              <p className={mutedCopyClass}>
                Use the validated Circuit IR as the handoff into the deterministic generation pipeline.
              </p>
            </div>
            {visibleLatestJob?.status === 'succeeded' ? (
              <p className={compactSupportCopyClass}>
                A project has already been generated. Generating again will replace the current job result.
              </p>
            ) : null}
            <div className={buttonRowClass}>
              <button
                type="button"
                className={visibleLatestJob?.status === 'succeeded' ? buttonDangerClass : buttonPrimaryClass}
                disabled={!canGenerateProject || Boolean(busyMessage)}
                onClick={() => void handleGenerateProject()}
              >
                {visibleLatestJob ? 'Generate Again' : 'Generate Project'}
              </button>
            </div>
          </section>

          {visibleLatestJob ? (
            <>
              {visibleLatestJob.status === 'failed' ? (() => {
                const errMsg = String(asRecord(visibleLatestJob.error).message ?? '')
                const isIrProblem =
                  asRecord(visibleLatestJob.error).code === 'IR_SEMANTIC_INVALID' ||
                  errMsg.includes('LibraryName:PartName') ||
                  errMsg.includes('unqualified') ||
                  errMsg.includes('pin') ||
                  errMsg.includes('symbol')
                return (
                  <div className={joinClasses(bannerBaseClass, statusBannerToneClass('error'), 'flex-col items-start gap-2')}>
                    <strong>
                      Generation failed{errMsg ? ` — ${errMsg}` : ''}.
                    </strong>
                    {isIrProblem ? (
                      <p className="text-sm leading-6">
                        The Circuit IR has an error that must be fixed before generating.{' '}
                        <Link
                          className="font-semibold underline"
                          to={`/wizard/${session.id}/ir`}
                        >
                          Go back to the Circuit IR step
                        </Link>
                        , clear the IR, and regenerate it.
                      </p>
                    ) : (
                      <p className="text-sm leading-6">Open the job for full details.</p>
                    )}
                  </div>
                )
              })() : null}
              <JobSummaryPanel job={visibleLatestJob} sessionId={session.id} />
            </>
          ) : (
            <p className={emptyCopyClass}>No generation job linked yet.</p>
          )}

          <div className="flex justify-start border-t border-[var(--border)] pt-4">
            <Link className={buttonSecondaryClass} to={`/wizard/${session.id}/ir`}>
              ← Back to Circuit IR
            </Link>
          </div>
        </>
      ) : null}
    </div>
  )
}

// Re-export readLastSession for use in Layout
export { readLastSession }
