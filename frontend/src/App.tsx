import { startTransition, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
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

const pageStackClass = 'grid gap-4 sm:gap-5'
const stackColumnClass = 'grid gap-4 sm:gap-5'
const pageShellClass = 'relative mx-auto max-w-[1320px] px-4 py-4 sm:px-6 sm:py-6 lg:px-4'
const dashboardGridClass = 'grid gap-4 xl:[grid-template-columns:minmax(0,1.5fr)_minmax(320px,0.9fr)] sm:gap-5'
const heroPanelClass =
  "relative grid gap-5 overflow-hidden rounded-[26px] border border-[rgba(109,47,20,0.16)] bg-[linear-gradient(140deg,rgba(255,248,238,0.98),rgba(245,230,203,0.94)),radial-gradient(circle_at_top_right,rgba(16,78,74,0.28),transparent_36%)] p-5 shadow-[0_28px_80px_rgba(71,43,19,0.14)] sm:gap-7 sm:rounded-[32px] sm:p-[2rem] xl:[grid-template-columns:minmax(0,1.35fr)_minmax(300px,0.75fr)] before:pointer-events-none before:absolute before:inset-x-[6%] before:top-0 before:h-px before:bg-[linear-gradient(90deg,transparent,rgba(255,255,255,0.85),transparent)] before:content-[''] after:pointer-events-none after:absolute after:inset-[auto_-48px_-92px_auto] after:h-[280px] after:w-[280px] after:bg-[radial-gradient(circle,rgba(242,196,138,0.7),transparent_70%)] after:content-['']"
const heroCardClass =
  'relative z-[1] grid gap-3 rounded-[24px] border border-[rgba(88,63,39,0.12)] bg-[linear-gradient(180deg,rgba(255,255,255,0.82),rgba(255,248,238,0.76))] p-4 shadow-[0_18px_40px_rgba(71,43,19,0.1)] backdrop-blur-sm sm:rounded-[28px] sm:p-[1.35rem]'
const panelBaseClass =
  'rounded-[24px] border border-[rgba(88,63,39,0.12)] p-4 shadow-[0_16px_34px_rgba(71,43,19,0.08)] sm:rounded-[28px] sm:p-[1.35rem]'
const panelSoftClass = `${panelBaseClass} bg-[linear-gradient(180deg,rgba(255,252,247,0.95),rgba(250,244,233,0.94))]`
const panelAccentClass = `${panelBaseClass} bg-[linear-gradient(180deg,rgba(255,248,237,0.99),rgba(246,232,209,0.96))]`
const headingGroupClass = 'mb-4 grid gap-1.5'
const eyebrowClass = 'm-0 text-[0.83rem] font-bold uppercase tracking-[0.14em] text-[var(--brand)]'
const mutedCopyClass = 'text-[var(--muted)]'
const buttonRowClass = 'flex flex-wrap gap-3'
const buttonBaseClass =
  'rounded-full border border-transparent px-[1.2rem] py-[0.8rem] font-semibold no-underline transition-[transform,opacity,box-shadow,background-color] duration-150 hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-[0.55] disabled:transform-none'
const buttonPrimaryClass =
  `${buttonBaseClass} bg-[linear-gradient(135deg,var(--brand)_0%,var(--brand-deep)_100%)] text-[#fff8f1] shadow-[0_18px_36px_rgba(109,47,20,0.2)] hover:shadow-[0_22px_44px_rgba(109,47,20,0.24)]`
const buttonSecondaryClass = `${buttonBaseClass} border-[rgba(24,75,69,0.12)] bg-[rgba(255,255,255,0.72)] text-[var(--accent)] hover:bg-[rgba(255,255,255,0.9)]`
const bannerBaseClass = 'flex items-center gap-3 rounded-[18px] border px-[1.1rem] py-[0.9rem] shadow-[0_10px_24px_rgba(71,43,19,0.06)]'
const spinnerClass = 'h-4 w-4 animate-spin rounded-full border-2 border-[rgba(13,76,116,0.16)] border-t-current'
const jsonBlockClass =
  'overflow-auto rounded-[18px] bg-[rgba(40,31,23,0.95)] p-4 font-[var(--font-mono)] text-[0.85rem] leading-[1.55] whitespace-pre-wrap break-words text-[#f7ead6]'
const emptyCopyClass = 'text-[var(--muted)]'
const recordListClass = 'm-0 grid list-none gap-3 p-0'
const compactListItemClass = 'flex items-baseline justify-between gap-3 lg:flex-col lg:items-start'
const tagListClass = 'm-0 grid list-none gap-3 p-0 [grid-template-columns:repeat(auto-fit,minmax(160px,max-content))]'
const tagItemClass = 'rounded-full border border-[rgba(109,47,20,0.12)] bg-[rgba(255,236,208,0.82)] px-[0.8rem] py-[0.45rem]'
const detailListClass = 'm-0 grid list-none gap-x-3 gap-y-2 p-0 [grid-template-columns:max-content_minmax(0,1fr)]'
const detailListGridClass = `${detailListClass} md:[grid-template-columns:repeat(2,max-content_minmax(0,1fr))]`
const blockGridClass = 'grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(220px,1fr))]'
const blockCardClass = 'grid gap-2 rounded-[20px] border border-[rgba(88,63,39,0.14)] bg-[linear-gradient(180deg,rgba(255,255,255,0.7),rgba(255,247,235,0.62))] p-4 shadow-[0_12px_28px_rgba(71,43,19,0.06)]'
const stepTrackerClass = 'm-0 grid list-none gap-3 p-0 lg:grid-cols-4'
const transcriptListClass = 'grid gap-4'
const transcriptEntryClass = 'grid gap-2 rounded-[22px] border p-4 shadow-[0_10px_24px_rgba(71,43,19,0.05)] sm:max-w-[88%]'
const heroLeadClass = 'max-w-[60ch] text-[1.02rem] leading-7 text-[var(--muted)]'
const heroStatGridClass = 'mt-6 grid gap-3 sm:grid-cols-3'
const heroStatCardClass = 'rounded-[22px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.58)] px-4 py-3 backdrop-blur-sm'
const sideKickerClass = 'text-[0.75rem] font-semibold uppercase tracking-[0.16em] text-[var(--accent)]'
const stepCardClass = 'grid gap-3 rounded-[22px] border border-[rgba(88,63,39,0.14)] bg-[rgba(255,255,255,0.7)] p-4 shadow-[0_10px_24px_rgba(71,43,19,0.05)]'
const stepBadgeClass = 'inline-flex items-center rounded-full px-2.5 py-1 text-[0.72rem] font-bold uppercase tracking-[0.12em]'
const wizardFormClass = 'grid gap-4 sm:gap-5'
const wizardFieldGridClass = 'grid gap-3 sm:gap-4 md:grid-cols-2'
const wizardFieldSectionClass = 'grid gap-4 rounded-[22px] border border-[rgba(88,63,39,0.12)] bg-[rgba(255,255,255,0.46)] p-3.5 sm:p-4'
const wizardTextareaClass =
  'min-h-[10rem] text-base leading-7 sm:min-h-[12rem] sm:text-[1.02rem] [&::-webkit-resizer]:hidden'
const wizardActionRowClass = 'flex flex-col gap-3 sm:flex-row sm:flex-wrap'
const wizardPrimaryButtonClass = `${buttonPrimaryClass} w-full justify-center text-center sm:w-auto`
const wizardSecondaryButtonClass = `${buttonSecondaryClass} w-full justify-center text-center sm:w-auto`
const compactSupportCopyClass = 'text-sm leading-6 text-[var(--muted)]'
const transcriptMetaClass = 'flex items-center justify-between gap-3 text-[0.76rem] font-semibold uppercase tracking-[0.12em]'
const transcriptBodyClass = 'whitespace-pre-wrap break-words text-[0.98rem] leading-7 text-[var(--text)]'
const composerCardClass = 'grid gap-3 rounded-[22px] border border-[rgba(88,63,39,0.12)] bg-[rgba(255,255,255,0.58)] p-3.5 shadow-[0_8px_20px_rgba(71,43,19,0.04)] sm:p-4'
const composerMetaRowClass = 'flex items-center justify-between gap-3 text-[0.82rem] text-[var(--muted)] sm:text-[0.86rem]'
const workflowStepListClass = 'm-0 grid list-none gap-2 p-0 sm:grid-cols-2'
const workflowStepItemClass = 'rounded-[18px] border border-[rgba(88,63,39,0.12)] bg-[rgba(255,255,255,0.68)] px-4 py-3 text-sm font-medium text-[var(--text)]'
const compactStatusRowClass = 'flex flex-wrap items-center gap-2'

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
}: {
  label: string
  placeholder: string
  value: string
  onChange: (value: string) => void
  disabled: boolean
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
        <span>{value.trim() ? 'Ready to send' : 'Draft a message'}</span>
        <span>Ctrl/Cmd+Enter to send</span>
      </div>
    </div>
  )
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

function wizardStepActionLabel(step: WizardStep): string {
  if (step === 'describe') {
    return 'Refine the brief'
  }
  if (step === 'spec') {
    return 'Approve the drafted spec'
  }
  if (step === 'ir') {
    return 'Validate the generated IR'
  }
  return 'Run project generation'
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

function WizardStepCard({
  session,
  currentStep,
  step,
  sessionId,
}: {
  session: WizardSessionDetail
  currentStep: WizardStep
  step: WizardStep
  sessionId: string
}) {
  const state = wizardStepState(session, currentStep, step)
  const toneClass = {
    current: 'border-[rgba(161,69,26,0.28)] bg-[linear-gradient(180deg,rgba(255,247,234,0.98),rgba(251,238,216,0.88))]',
    done: 'border-[rgba(35,102,79,0.18)] bg-[rgba(235,247,241,0.82)]',
    ready: 'border-[rgba(22,93,143,0.18)] bg-[rgba(236,245,251,0.76)]',
    locked: 'opacity-75',
  }[state]
  const badgeToneClass = {
    current: 'bg-[rgba(161,69,26,0.12)] text-[var(--brand-deep)]',
    done: 'bg-[rgba(35,102,79,0.12)] text-[var(--success)]',
    ready: 'bg-[rgba(22,93,143,0.1)] text-[#0d4c74]',
    locked: 'bg-[rgba(117,99,80,0.1)] text-[var(--muted)]',
  }[state]
  const badgeLabel = {
    current: 'Current',
    done: 'Done',
    ready: 'Ready',
    locked: 'Locked',
  }[state]

  return (
    <li className={joinClasses(stepCardClass, toneClass)}>
      <div className="flex items-start justify-between gap-3">
        <div className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-[rgba(161,69,26,0.12)] font-bold text-[var(--brand-deep)]">
          {WIZARD_STEP_ORDER[step] + 1}
        </div>
        <span className={joinClasses(stepBadgeClass, badgeToneClass)}>{badgeLabel}</span>
      </div>
      <div className="grid gap-1.5">
        {state === 'locked' ? (
          <strong>{WIZARD_STEP_META[step].label}</strong>
        ) : (
          <Link className="font-semibold text-[var(--brand-deep)] no-underline" to={`/wizard/${sessionId}/${step}`}>
            {WIZARD_STEP_META[step].label}
          </Link>
        )}
        <p className={mutedCopyClass}>{WIZARD_STEP_META[step].summary}</p>
      </div>
      <p className="text-sm leading-6 text-[var(--muted)]">{wizardStepActionLabel(step)}</p>
    </li>
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
          <Link className="text-xl font-bold no-underline [font-family:var(--font-heading)] tracking-[0.01em]" to="/wizard">
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
  void bootstrap
  return <Navigate to="/wizard" replace />
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
          <div className={compactStatusRowClass}>
            <StatusPill tone={bootstrap.llm_enabled ? 'success' : 'neutral'}>
              {bootstrap.llm_enabled ? 'provider ready' : 'provider disabled'}
            </StatusPill>
            <span className={compactSupportCopyClass}>{bootstrap.llm_provider}</span>
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
          <form className={wizardFormClass} onSubmit={(event) => void handleCreateSession(event)}>
            <div className={wizardFieldSectionClass}>
              <div className={wizardFieldGridClass}>
                <label>
                  <span>Project Name</span>
                  <input value={projectName} onChange={(event) => setProjectName(event.target.value)} />
                </label>
                <label>
                  <span>Symbols Directory</span>
                  <input value={symbolsDir} onChange={(event) => setSymbolsDir(event.target.value)} />
                </label>
              </div>
              <p className={compactSupportCopyClass}>Keep this short. Put the real detail into the request box.</p>
            </div>
            <div className={wizardFieldSectionClass}>
              <WizardComposer
                disabled={Boolean(busyMessage)}
                label="Circuit Request"
                placeholder="Goal, rails, inputs, outputs, constraints."
                value={message}
                onChange={setMessage}
              />
            </div>
            <div className={wizardActionRowClass}>
              <button
                type="submit"
                className={wizardPrimaryButtonClass}
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
  const checkpoint = wizardCurrentCheckpoint(session, currentStep)

  return (
    <div className={pageStackClass}>
      <section className={heroPanelClass}>
        <div className="grid gap-3">
          <p className={eyebrowClass}>Session {session.id}</p>
          <h1>{WIZARD_STEP_META[currentStep].label}</h1>
          <p className={heroLeadClass}>{checkpoint.title}</p>
          <p className={compactSupportCopyClass}>{checkpoint.detail}</p>
        </div>
        <div className={heroCardClass}>
          <div className={compactStatusRowClass}>
            <StatusPill tone={statusTone(session.status)}>{session.status.replaceAll('_', ' ')}</StatusPill>
            <span className={compactSupportCopyClass}>{session.llm_provider ?? bootstrap.llm_provider}</span>
          </div>
          <p className={compactSupportCopyClass}>{bannerForSession(session)}</p>
          {session.latest_job_id ? (
            <Link className={buttonSecondaryClass} to={`/jobs/${session.latest_job_id}`}>
              Open latest job
            </Link>
          ) : null}
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
        <div className={headingGroupClass}>
          <div className={sideKickerClass}>Workflow</div>
          <h2>Step tracker</h2>
          <p className={mutedCopyClass}>Each step says whether it is current, ready, done, or still locked.</p>
        </div>
        <ol className={stepTrackerClass}>
          {(Object.keys(WIZARD_STEP_META) as WizardStep[]).map((wizardStep) => {
            return (
              <WizardStepCard
                key={wizardStep}
                currentStep={currentStep}
                session={session}
                sessionId={session.id}
                step={wizardStep}
              />
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
          <section className="mt-5 grid gap-2 rounded-[20px] border border-[rgba(161,69,26,0.12)] bg-[rgba(255,249,241,0.86)] p-4">
            <div className={sideKickerClass}>Current checkpoint</div>
            <strong className="text-[var(--text)]">{checkpoint.title}</strong>
            <p className="text-sm leading-6 text-[var(--muted)]">{checkpoint.detail}</p>
          </section>
        </aside>

        <div className={stackColumnClass}>
          {currentStep === 'describe' ? (
            <>
              <section className={panelAccentClass}>
                <div className={headingGroupClass}>
                  <h2>Describe Circuit</h2>
                  <p className={mutedCopyClass}>Keep refining the prompt until the spec is ready for review.</p>
                </div>
                <form className={wizardFormClass} onSubmit={(event) => void handleSendMessage(event)}>
                  <div className={wizardFieldSectionClass}>
                    <div className={wizardFieldGridClass}>
                      <label>
                        <span>Project Name</span>
                        <input value={projectName} onChange={(event) => setProjectName(event.target.value)} />
                      </label>
                      <label>
                        <span>Symbols Directory</span>
                        <input value={symbolsDir} onChange={(event) => setSymbolsDir(event.target.value)} />
                      </label>
                    </div>
                    <p className={compactSupportCopyClass}>Adjust metadata only when the session context actually changed.</p>
                  </div>
                  <div className={wizardFieldSectionClass}>
                    <WizardComposer
                      disabled={Boolean(busyMessage)}
                      label="Circuit Request"
                      placeholder="Clarify only the missing or changed details."
                      value={message}
                      onChange={setMessage}
                    />
                  </div>
                  <div className={wizardActionRowClass}>
                    <button
                      type="submit"
                      className={wizardPrimaryButtonClass}
                      disabled={!bootstrap.llm_enabled || !message.trim() || Boolean(busyMessage)}
                    >
                      Send Update
                    </button>
                  </div>
                </form>
              </section>

              <section className={panelSoftClass}>
                <div className={headingGroupClass}>
                  <h2>Conversation</h2>
                  <p className={mutedCopyClass}>Your notes stay separated from the wizard replies so each turn is easier to scan.</p>
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
                <form className={wizardFormClass} onSubmit={(event) => void handleSendMessage(event)}>
                  <div className={wizardFieldSectionClass}>
                    <WizardComposer
                      disabled={Boolean(busyMessage)}
                      label="Revision Note"
                      placeholder="List only the specific spec changes you want."
                      value={message}
                      onChange={setMessage}
                    />
                  </div>
                  <div className={wizardActionRowClass}>
                    <button
                      type="submit"
                      className={wizardSecondaryButtonClass}
                      disabled={!message.trim() || Boolean(busyMessage)}
                    >
                      Send Changes
                    </button>
                    <button
                      type="button"
                      className={wizardPrimaryButtonClass}
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
          <p className={heroLeadClass}>Inspect the full generation result, artifacts, diagnostics, and raw payloads.</p>
          <div className={heroStatGridClass}>
            <MetricCard label="Artifacts" tone="warm" value={String(job.artifacts.length)} />
            <MetricCard label="Components" tone="cool" value={String(result.component_count ?? '—')} />
            <MetricCard label="Nets" tone="neutral" value={String(result.net_count ?? '—')} />
          </div>
        </div>
        <div className={heroCardClass}>
          <p className={eyebrowClass}>Status</p>
          <StatusPill tone={statusTone(job.status)}>{job.status}</StatusPill>
          <span className={mutedCopyClass}>{job.updated_at}</span>
          <p className="text-sm leading-6 text-[var(--muted)]">
            Review the artifact set first, then use warnings and diagnostics to understand any layout or generation issues.
          </p>
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