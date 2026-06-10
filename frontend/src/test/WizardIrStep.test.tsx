import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { WizardIrStep } from '../routes/wizard/WizardIrStep'
import { wizardCurrentCheckpoint } from '../routes/wizard/wizardStepLogic'
import { renderWithProviders } from './renderWithProviders'
import { makeSession } from './fixtures'

const SESSION_IR_PENDING = makeSession({
  status: 'spec_approved',
  spec_approved: true,
  ir_json: null,
  ir_validation: null,
})
const CHECKPOINT = wizardCurrentCheckpoint(SESSION_IR_PENDING, 'ir')

const BASE_PROPS = {
  session: SESSION_IR_PENDING,
  sessionId: 'test-session-123',
  projectLabel: 'Test LED blinker',
  checkpoint: CHECKPOINT,
  busyMessage: null,
  canGenerateIr: true,
  canGenerateProject: false,
  onGenerateIr: vi.fn(),
  onClearIr: vi.fn(),
}

describe('WizardIrStep — LLM-disabled IR generation (task 4.5)', () => {
  it('disables Generate Circuit IR when llm_enabled is false', () => {
    renderWithProviders(<WizardIrStep {...BASE_PROPS} llmEnabled={false} />)
    const btn = screen.getByRole('button', { name: 'Generate Circuit IR' })
    expect(btn).toBeDisabled()
  })

  it('shows LLM unavailable help text when llm_enabled is false', () => {
    renderWithProviders(<WizardIrStep {...BASE_PROPS} llmEnabled={false} />)
    expect(
      screen.getByText(/LLM provider is not available — IR generation requires a configured provider/),
    ).toBeInTheDocument()
  })

  it('enables Generate Circuit IR when llm_enabled is true', () => {
    renderWithProviders(<WizardIrStep {...BASE_PROPS} llmEnabled />)
    const btn = screen.getByRole('button', { name: 'Generate Circuit IR' })
    expect(btn).not.toBeDisabled()
  })

  it('does not show LLM help text when llm_enabled is true', () => {
    renderWithProviders(<WizardIrStep {...BASE_PROPS} llmEnabled />)
    expect(
      screen.queryByText(/LLM provider is not available — IR generation requires a configured provider/),
    ).not.toBeInTheDocument()
  })

  it('disables Regenerate Circuit IR when llm_enabled is false and IR exists', () => {
    const sessionWithIr = makeSession({
      status: 'ir_ready_for_generation',
      spec_approved: true,
      ir_json: { components: [] },
      ir_validation: {
        valid: true,
        auto_fixed: false,
        component_count: 3,
        net_count: 5,
        warnings: [],
        fixes_applied: [],
        symbols_dirs_used: [],
      },
    })
    renderWithProviders(
      <WizardIrStep
        {...BASE_PROPS}
        session={sessionWithIr}
        checkpoint={wizardCurrentCheckpoint(sessionWithIr, 'ir')}
        llmEnabled={false}
      />,
    )
    const btn = screen.getByRole('button', { name: 'Regenerate Circuit IR' })
    expect(btn).toBeDisabled()
  })
})
