/**
 * Smoke tests: verify each wizard step component renders without crashing
 * with representative data, and survives partial/missing optional data.
 * (task 4.8)
 */
import { describe, it, vi, expect } from 'vitest'
import { screen } from '@testing-library/react'

import { WizardStartStep } from '../routes/wizard/WizardStartStep'
import { WizardDescribeStep } from '../routes/wizard/WizardDescribeStep'
import { WizardSpecStep } from '../routes/wizard/WizardSpecStep'
import { WizardIrStep } from '../routes/wizard/WizardIrStep'
import { WizardGenerateStep } from '../routes/wizard/WizardGenerateStep'
import { wizardCurrentCheckpoint } from '../routes/wizard/wizardStepLogic'
import { renderWithProviders } from './renderWithProviders'
import { makeSession, makeJob } from './fixtures'

// ─── WizardStartStep ──────────────────────────────────────────────────────────

describe('WizardStartStep smoke (task 4.8)', () => {
  const BASE = {
    projectName: '',
    setProjectName: vi.fn(),
    symbolsDir: '',
    setSymbolsDir: vi.fn(),
    message: '',
    setMessage: vi.fn(),
    llmEnabled: true,
    llmProvider: 'openai',
    busyMessage: null,
    errorMessage: null,
    onDismissError: vi.fn(),
    onSubmit: vi.fn(),
  }

  it('renders without crashing with normal props', () => {
    renderWithProviders(<WizardStartStep {...BASE} />)
    screen.getByRole('heading', { name: /Start a circuit session/i })
  })

  it('renders busy banner when busyMessage is set', () => {
    renderWithProviders(<WizardStartStep {...BASE} busyMessage="Creating session…" />)
    screen.getByText('Creating session…')
  })

  it('renders error banner when errorMessage is set', () => {
    renderWithProviders(<WizardStartStep {...BASE} errorMessage="Something went wrong." />)
    screen.getByText('Something went wrong.')
  })

  it('error dismiss button is labelled "Edit and try again" (C2)', () => {
    renderWithProviders(<WizardStartStep {...BASE} errorMessage="Something went wrong." />)
    expect(screen.getByRole('button', { name: 'Edit and try again' })).toBeInTheDocument()
  })

  it('renders LLM unavailable note when llmEnabled is false', () => {
    renderWithProviders(<WizardStartStep {...BASE} llmEnabled={false} />)
    screen.getByText(/No LLM provider is configured/i)
  })
})

// ─── WizardDescribeStep ───────────────────────────────────────────────────────

describe('WizardDescribeStep smoke (task 4.8)', () => {
  const SESSION = makeSession({ status: 'drafting_spec', messages: [] })
  const BASE = {
    session: SESSION,
    sessionId: 'test-session-123',
    projectLabel: 'My Project',
    checkpoint: wizardCurrentCheckpoint(SESSION, 'describe'),
    llmEnabled: true,
    llmProvider: 'openai',
    busyMessage: null,
    message: '',
    setMessage: vi.fn(),
    projectName: 'Test Project',
    setProjectName: vi.fn(),
    symbolsDir: '',
    setSymbolsDir: vi.fn(),
    metaExpanded: false,
    setMetaExpanded: vi.fn() as (updater: boolean | ((prev: boolean) => boolean)) => void,
    onSendMessage: vi.fn(),
  }

  it('renders without crashing', () => {
    renderWithProviders(<WizardDescribeStep {...BASE} />)
    screen.getByRole('heading', { name: /Describe Circuit/i })
  })

  it('shows drafting indicator when status is drafting_spec and not busy (A1)', () => {
    renderWithProviders(<WizardDescribeStep {...BASE} />)
    // The status banner contains unique text not present in the StatusPill label
    expect(screen.getByText(/The assistant is preparing the first draft/)).toBeInTheDocument()
  })

  it('hides drafting indicator when busyMessage is set (A1)', () => {
    renderWithProviders(<WizardDescribeStep {...BASE} busyMessage="Sending…" />)
    expect(screen.queryByText(/The assistant is preparing the first draft/)).not.toBeInTheDocument()
  })

  it('does not show drafting indicator when status is not drafting_spec (A1)', () => {
    const sessionReady = makeSession({ status: 'spec_ready_for_review', messages: [] })
    renderWithProviders(
      <WizardDescribeStep
        {...BASE}
        session={sessionReady}
        checkpoint={wizardCurrentCheckpoint(sessionReady, 'describe')}
      />,
    )
    expect(screen.queryByText(/The assistant is preparing the first draft/)).not.toBeInTheDocument()
  })

  it('renders with conversation messages', () => {
    const sessionWithMsgs = makeSession({
      status: 'drafting_spec',
      messages: [
        { role: 'user', content: 'I want an LED blinker.' },
        { role: 'assistant', content: 'Tell me more about the circuit.' },
      ],
    })
    renderWithProviders(
      <WizardDescribeStep
        {...BASE}
        session={sessionWithMsgs}
        checkpoint={wizardCurrentCheckpoint(sessionWithMsgs, 'describe')}
      />,
    )
    screen.getByText('I want an LED blinker.')
    screen.getByText('Tell me more about the circuit.')
  })
})

// ─── WizardSpecStep ───────────────────────────────────────────────────────────

describe('WizardSpecStep smoke (task 4.8)', () => {
  const SESSION = makeSession({ status: 'spec_ready_for_review' })
  const BASE = {
    session: SESSION,
    sessionId: 'test-session-123',
    projectLabel: 'My Project',
    checkpoint: wizardCurrentCheckpoint(SESSION, 'spec'),
    llmEnabled: true,
    busyMessage: null,
    message: '',
    setMessage: vi.fn(),
    canApproveSpec: true,
    canGenerateIr: false,
    hasUnspecifiedCustomBlocks: false,
    onSendMessage: vi.fn(),
    onApproveSpec: vi.fn(),
  }

  it('renders spec data without crashing', () => {
    renderWithProviders(<WizardSpecStep {...BASE} />)
    screen.getByRole('heading', { name: /Review Spec/i })
  })

  it('returns null when session has no spec', () => {
    const { container } = renderWithProviders(
      <WizardSpecStep {...BASE} session={makeSession({ spec: null })} />,
    )
    // Early return → renders nothing
    expect(container).toBeEmptyDOMElement()
  })

  it('shows open-questions warning when present', () => {
    const sessionWithQ = makeSession({
      status: 'spec_ready_for_review',
      open_questions: ['What supply voltage?'],
    })
    renderWithProviders(
      <WizardSpecStep
        {...BASE}
        session={sessionWithQ}
        checkpoint={wizardCurrentCheckpoint(sessionWithQ, 'spec')}
        canApproveSpec={false}
      />,
    )
    screen.getByText('What supply voltage?')
  })
})

// ─── WizardIrStep ─────────────────────────────────────────────────────────────

describe('WizardIrStep smoke (task 4.8)', () => {
  const SESSION = makeSession({ status: 'spec_approved', spec_approved: true })
  const BASE = {
    session: SESSION,
    sessionId: 'test-session-123',
    projectLabel: 'My Project',
    checkpoint: wizardCurrentCheckpoint(SESSION, 'ir'),
    llmEnabled: true,
    busyMessage: null,
    canGenerateIr: true,
    canGenerateProject: false,
    onGenerateIr: vi.fn(),
    onClearIr: vi.fn(),
  }

  it('renders without crashing when no IR exists', () => {
    renderWithProviders(<WizardIrStep {...BASE} />)
    // Level 1 avoids ambiguity with the "Generate Circuit IR" h2 subheading
    screen.getByRole('heading', { name: /Circuit IR/i, level: 1 })
    screen.getByText('No Circuit IR draft yet.')
  })

  it('renders validation summary when IR exists and is valid', () => {
    const sessionWithIr = makeSession({
      status: 'ir_ready_for_generation',
      spec_approved: true,
      ir_json: { components: [] },
      ir_validation: {
        valid: true,
        auto_fixed: false,
        component_count: 4,
        net_count: 6,
        warnings: [],
        fixes_applied: [],
        symbols_dirs_used: [],
      },
    })
    renderWithProviders(
      <WizardIrStep
        {...BASE}
        session={sessionWithIr}
        checkpoint={wizardCurrentCheckpoint(sessionWithIr, 'ir')}
        canGenerateProject
      />,
    )
    screen.getByRole('heading', { name: /Circuit IR — Valid/i })
  })

  it('shows repair button when status is ir_needs_repair', () => {
    const sessionRepair = makeSession({
      status: 'ir_needs_repair',
      spec_approved: true,
    })
    renderWithProviders(
      <WizardIrStep
        {...BASE}
        session={sessionRepair}
        checkpoint={wizardCurrentCheckpoint(sessionRepair, 'ir')}
      />,
    )
    screen.getByRole('button', { name: 'Repair Circuit IR' })
  })
})

// ─── WizardGenerateStep ───────────────────────────────────────────────────────

describe('WizardGenerateStep smoke (task 4.8)', () => {
  const SESSION = makeSession({ status: 'ir_ready_for_generation', spec_approved: true })
  const BASE = {
    session: SESSION,
    sessionId: 'test-session-123',
    projectLabel: 'My Project',
    checkpoint: wizardCurrentCheckpoint(SESSION, 'generate'),
    busyMessage: null,
    canGenerateProject: true,
    visibleLatestJob: null,
    confirmRegenerate: false,
    setConfirmRegenerate: vi.fn(),
    onGenerateProject: vi.fn(),
  }

  it('renders without crashing when no job exists', () => {
    renderWithProviders(<WizardGenerateStep {...BASE} />)
    screen.getByRole('heading', { name: /Generate Project/i })
    screen.getByText('No generation job linked yet.')
  })

  it('renders job summary when a succeeded job exists', () => {
    renderWithProviders(
      <WizardGenerateStep {...BASE} visibleLatestJob={makeJob({ status: 'succeeded' })} />,
    )
    screen.getByRole('heading', { name: 'Latest Job' })
    screen.getByRole('button', { name: 'Generate Again' })
  })

  it('renders failed-job error banner when latest job failed', () => {
    renderWithProviders(
      <WizardGenerateStep
        {...BASE}
        visibleLatestJob={makeJob({ status: 'failed', error: { message: 'Build error' } })}
      />,
    )
    screen.getByRole('alert')
    screen.getByText(/Generation failed/)
  })
})
