import { describe, expect, it } from 'vitest'

import { makeSession } from './fixtures'
import {
  canonicalWizardStep,
  wizardCanGenerateIr,
  wizardCanGenerateProject,
  wizardStepUnlocked,
} from '../routes/wizard/wizardStepLogic'

const VALIDATION = {
  valid: true,
  auto_fixed: false,
  component_count: 3,
  net_count: 2,
  warnings: [],
  fixes_applied: [],
  symbols_dirs_used: [],
}

describe('wizard hardening state gates', () => {
  it('does not treat a preserved checkpoint as current after spec revision failure', () => {
    const session = makeSession({
      status: 'failed',
      spec_approved: true,
      ir_json: { version: '1' },
      ir_validation: VALIDATION,
      error: {
        message: 'Revision failed',
        details: { operation: 'revise_spec' },
      },
    })

    expect(canonicalWizardStep(session)).toBe('spec')
    expect(wizardCanGenerateIr(session)).toBe(false)
    expect(wizardCanGenerateProject(session)).toBe(false)
    expect(wizardStepUnlocked(session, 'generate')).toBe(false)
  })

  it('allows an explicit IR retry but not project generation after IR failure', () => {
    const session = makeSession({
      status: 'failed',
      spec_approved: true,
      ir_json: { version: '1' },
      ir_validation: VALIDATION,
      error: {
        message: 'IR generation failed',
        details: { operation: 'generate_ir' },
      },
    })

    expect(canonicalWizardStep(session)).toBe('ir')
    expect(wizardCanGenerateIr(session)).toBe(true)
    expect(wizardCanGenerateProject(session)).toBe(false)
    expect(wizardStepUnlocked(session, 'generate')).toBe(false)
  })

  it('allows an explicit project retry when the current IR itself did not fail', () => {
    const session = makeSession({
      status: 'failed',
      spec_approved: true,
      ir_json: { version: '1' },
      ir_validation: VALIDATION,
      error: {
        message: 'Project generation failed',
        details: { operation: 'generate_project' },
      },
    })

    expect(canonicalWizardStep(session)).toBe('generate')
    expect(wizardCanGenerateProject(session)).toBe(true)
    expect(wizardStepUnlocked(session, 'generate')).toBe(true)
  })
})