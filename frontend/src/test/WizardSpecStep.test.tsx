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
