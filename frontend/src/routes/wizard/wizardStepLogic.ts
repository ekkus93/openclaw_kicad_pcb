import type { WizardSessionDetail, WizardStatus, WizardStep } from '../../types'

export const WIZARD_STEP_ORDER: Record<WizardStep, number> = {
  describe: 0,
  spec: 1,
  ir: 2,
  generate: 3,
}

export const WIZARD_STEP_META: Record<WizardStep, { label: string; abbrev: string; summary: string }> = {
  describe: { label: 'Describe Circuit', abbrev: 'Describe', summary: 'Start a session and refine the brief.' },
  spec: { label: 'Review Spec', abbrev: 'Spec', summary: 'Approve or revise the drafted specification.' },
  ir: { label: 'Circuit Plan', abbrev: 'Plan', summary: 'Generate and validate the circuit plan before project generation.' },
  generate: { label: 'Generate Project', abbrev: 'Generate', summary: 'Launch the deterministic KiCad generation path.' },
}

export function normalizeWizardStep(raw: string | undefined): WizardStep | undefined {
  if (raw === 'describe' || raw === 'spec' || raw === 'ir' || raw === 'generate') {
    return raw
  }
  return undefined
}

export function statusLabel(status: WizardStatus | string): string {
  const labels: Record<string, string> = {
    drafting_spec: 'Drafting spec…',
    awaiting_user_clarification: 'Your input needed',
    spec_ready_for_review: 'Spec ready for review',
    spec_approved: 'Spec approved',
    drafting_ir: 'Generating circuit plan…',
    ir_needs_repair: 'Circuit plan needs repair',
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

export function statusTone(status: WizardStatus | string): 'neutral' | 'active' | 'success' | 'warning' | 'error' {
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

export function displaySymbolsDir(raw: string): string {
  if (!raw) return 'None'
  const normalized = raw.replace(/\\/g, '/')
  if (normalized.includes('/resources/symbols') || normalized.includes('kicad_pcb/resources')) {
    return 'Built-in symbols'
  }
  const parts = normalized.split('/')
  return parts[parts.length - 1] || parts[parts.length - 2] || raw
}

export function asRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return {}
}

export function wizardFailureOperation(session: WizardSessionDetail): string | null {
  if (session.status !== 'failed') return null
  const operation = session.error?.details?.operation
  return typeof operation === 'string' ? operation : null
}

export function wizardCanGenerateIr(session: WizardSessionDetail): boolean {
  if (!session.spec || !session.spec_approved) return false
  if (
    session.status === 'spec_approved' ||
    session.status === 'ir_needs_repair' ||
    session.status === 'ir_ready_for_generation' ||
    session.status === 'completed'
  ) {
    return true
  }
  return session.status === 'failed' && wizardFailureOperation(session) === 'generate_ir'
}

export function wizardCanGenerateProject(session: WizardSessionDetail): boolean {
  if (!session.ir_json || !session.ir_validation?.valid) return false
  if (session.status === 'ir_ready_for_generation' || session.status === 'completed') return true
  return session.status === 'failed' && wizardFailureOperation(session) === 'generate_project'
}

export function canonicalWizardStep(session: WizardSessionDetail): WizardStep {
  if (session.status === 'spec_ready_for_review') return 'spec'
  if (
    session.status === 'spec_approved' ||
    session.status === 'drafting_ir' ||
    session.status === 'ir_needs_repair'
  ) return 'ir'
  if (
    session.status === 'ir_ready_for_generation' ||
    session.status === 'generation_started' ||
    session.status === 'completed'
  ) return 'generate'
  if (session.status === 'failed') {
    const failedOperation = wizardFailureOperation(session)
    if (failedOperation === 'generate_project' && wizardCanGenerateProject(session)) return 'generate'
    if (failedOperation === 'generate_ir' && session.spec_approved) return 'ir'
    if (session.spec) return 'spec'
  }
  return 'describe'
}

export function wizardStepUnlocked(session: WizardSessionDetail, step: WizardStep): boolean {
  return WIZARD_STEP_ORDER[step] <= WIZARD_STEP_ORDER[canonicalWizardStep(session)]
}

export function wizardStepState(
  session: WizardSessionDetail,
  currentStep: WizardStep,
  step: WizardStep,
): 'current' | 'done' | 'ready' | 'locked' {
  if (currentStep === step) return 'current'
  if (WIZARD_STEP_ORDER[step] < WIZARD_STEP_ORDER[currentStep]) return 'done'
  if (wizardStepUnlocked(session, step)) return 'ready'
  return 'locked'
}

export function wizardCurrentCheckpoint(
  session: WizardSessionDetail,
  step: WizardStep,
): { title: string; detail: string } {
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
          : 'If the purpose, blocks, ports, rails, and constraints all match intent, approve the spec to unlock circuit plan generation.',
    }
  }
  if (step === 'ir') {
    return {
      title: 'Generate and validate the circuit plan before handing off to project generation.',
      detail: session.status === 'failed' && wizardFailureOperation(session) === 'generate_ir'
        ? 'The last IR regeneration failed. The previous validated plan is retained only as a checkpoint; retry Circuit Plan generation before continuing.'
        : session.ir_validation?.valid
          ? 'The current IR validates cleanly. Review counts and warnings, then move to project generation.'
          : 'Run IR generation, inspect validation, and repair any warnings or invalid output before continuing.',
    }
  }
  return {
    title: 'Use the validated IR as the deterministic handoff into project generation.',
    detail: session.status === 'failed' && wizardFailureOperation(session) === 'generate_project'
      ? 'Project generation failed without invalidating the current IR. Review the failed job, then retry generation explicitly.'
      : session.latest_job_id
        ? 'A job already exists for this session. Review the latest artifacts or rerun generation if needed.'
        : 'Once the IR is valid, generate the project and review artifacts and diagnostics on the linked job.',
  }
}