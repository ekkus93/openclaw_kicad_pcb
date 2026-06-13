import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { WizardSpecStep } from '../routes/wizard/WizardSpecStep'
import { wizardCurrentCheckpoint } from '../routes/wizard/wizardStepLogic'
import { renderWithProviders } from './renderWithProviders'
import { makeSession } from './fixtures'

const SESSION = makeSession({
  status: 'spec_ready_for_review',
  spec_approved: false,
  open_questions: [],
  unsupported_reasons: [],
})
const CHECKPOINT = wizardCurrentCheckpoint(SESSION, 'spec')

const BASE_PROPS = {
  session: SESSION,
  sessionId: 'test-session-123',
  projectLabel: 'Test LED blinker',
  checkpoint: CHECKPOINT,
  busyMessage: null,
  message: 'Please add a power LED indicator.',
  setMessage: vi.fn(),
  canApproveSpec: true,
  canGenerateIr: false,
  hasUnspecifiedCustomBlocks: false,
  onSendMessage: vi.fn(),
  onApproveSpec: vi.fn(),
}

describe('WizardSpecStep — D1: revision section copy', () => {
  it('shows updated revision description text', () => {
    renderWithProviders(<WizardSpecStep {...BASE_PROPS} llmEnabled />)
    expect(
      screen.getByText(/Send revision notes to regenerate the spec from scratch/),
    ).toBeInTheDocument()
  })

  it('does not show old description text', () => {
    renderWithProviders(<WizardSpecStep {...BASE_PROPS} llmEnabled />)
    expect(
      screen.queryByText(/Approve the spec to unlock/),
    ).not.toBeInTheDocument()
  })
})

describe('WizardSpecStep — D2: underspecified alert in revise section', () => {
  it('shows underspecified alert in revise section when hasUnspecifiedCustomBlocks and not busy', () => {
    renderWithProviders(
      <WizardSpecStep {...BASE_PROPS} llmEnabled hasUnspecifiedCustomBlocks={true} busyMessage={null} />,
    )
    expect(screen.getByText(/Action required before approving/)).toBeInTheDocument()
    expect(
      screen.getByText(/Go back to Describe and name a specific component/),
    ).toBeInTheDocument()
  })

  it('hides the revise-section alert when busyMessage is set', () => {
    renderWithProviders(
      <WizardSpecStep {...BASE_PROPS} llmEnabled hasUnspecifiedCustomBlocks={true} busyMessage="Sending…" />,
    )
    expect(screen.queryByText(/Action required before approving/)).not.toBeInTheDocument()
  })

  it('does not show the revise-section alert when hasUnspecifiedCustomBlocks is false', () => {
    renderWithProviders(
      <WizardSpecStep {...BASE_PROPS} llmEnabled hasUnspecifiedCustomBlocks={false} busyMessage={null} />,
    )
    expect(screen.queryByText(/Action required before approving/)).not.toBeInTheDocument()
  })
})

describe('WizardSpecStep — LLM-disabled spec revision (task 4.4)', () => {
  it('disables Send Changes button when llm_enabled is false', () => {
    renderWithProviders(
      <WizardSpecStep {...BASE_PROPS} llmEnabled={false} />,
    )
    const sendBtn = screen.getByRole('button', { name: 'Send Changes' })
    expect(sendBtn).toBeDisabled()
  })

  it('shows LLM unavailable help text when llm_enabled is false', () => {
    renderWithProviders(
      <WizardSpecStep {...BASE_PROPS} llmEnabled={false} />,
    )
    expect(
      screen.getByText(/LLM provider is not available — revision requires a configured provider/),
    ).toBeInTheDocument()
  })

  it('enables Send Changes button when llm_enabled is true and message is non-empty', () => {
    renderWithProviders(
      <WizardSpecStep {...BASE_PROPS} llmEnabled />,
    )
    const sendBtn = screen.getByRole('button', { name: 'Send Changes' })
    expect(sendBtn).not.toBeDisabled()
  })

  it('does not show LLM help text when llm_enabled is true', () => {
    renderWithProviders(
      <WizardSpecStep {...BASE_PROPS} llmEnabled />,
    )
    expect(
      screen.queryByText(/LLM provider is not available — revision requires a configured provider/),
    ).not.toBeInTheDocument()
  })

  it('Approve Spec remains available even when llm_enabled is false', () => {
    renderWithProviders(
      <WizardSpecStep {...BASE_PROPS} llmEnabled={false} canApproveSpec />,
    )
    const approveBtn = screen.getByRole('button', { name: 'Approve Spec' })
    expect(approveBtn).not.toBeDisabled()
  })
})
