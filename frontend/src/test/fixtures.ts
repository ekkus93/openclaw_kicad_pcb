import type { WizardSessionDetail, JobDetail, CircuitSpec } from '../types'

const BASE_SPEC: CircuitSpec = {
  purpose: 'Test LED blinker',
  supply_rails: [{ name: '3V3', nominal_voltage: '3.3V', description: '3.3V supply' }],
  inputs: [],
  outputs: [{ name: 'LED_OUT', signal_type: 'digital', description: 'LED output' }],
  blocks: [
    {
      name: 'MCU',
      block_type: 'microcontroller',
      summary: 'Main controller',
      required_components: ['STM32F103'],
      constraints: [],
    },
  ],
  required_components: ['STM32F103'],
  constraints: [],
  assumptions: [],
  open_questions: [],
  acceptance_criteria: ['LED blinks at 1Hz'],
  unsupported_reasons: [],
}

export function makeSession(overrides: Partial<WizardSessionDetail> = {}): WizardSessionDetail {
  return {
    id: 'test-session-123',
    status: 'spec_ready_for_review',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    messages: [],
    spec: BASE_SPEC,
    spec_approved: false,
    assumptions: [],
    open_questions: [],
    unsupported_reasons: [],
    ...overrides,
  }
}

export function makeJob(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    id: 'test-job-456',
    status: 'succeeded',
    project_name: 'TestProject',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    request: {},
    result: {},
    error: null,
    artifacts: [],
    ...overrides,
  }
}
