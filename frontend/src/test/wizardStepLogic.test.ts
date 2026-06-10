import { describe, it, expect } from 'vitest'

import {
  statusLabel,
  statusTone,
  displaySymbolsDir,
  asRecord,
  canonicalWizardStep,
  wizardStepUnlocked,
  wizardStepState,
} from '../routes/wizard/wizardStepLogic'
import { makeSession } from './fixtures'

describe('statusLabel', () => {
  it('returns human-readable labels for known statuses', () => {
    expect(statusLabel('drafting_spec')).toBe('Drafting spec…')
    expect(statusLabel('spec_ready_for_review')).toBe('Spec ready for review')
    expect(statusLabel('spec_approved')).toBe('Spec approved')
    expect(statusLabel('ir_ready_for_generation')).toBe('IR ready')
    expect(statusLabel('succeeded')).toBe('Succeeded')
    expect(statusLabel('failed')).toBe('Failed')
    expect(statusLabel('running')).toBe('Running')
    expect(statusLabel('queued')).toBe('Queued')
  })

  it('replaces underscores for unknown statuses', () => {
    expect(statusLabel('some_unknown_status')).toBe('some unknown status')
  })
})

describe('statusTone', () => {
  it('returns active for drafting/generation-in-progress statuses', () => {
    expect(statusTone('spec_ready_for_review')).toBe('active')
    expect(statusTone('spec_approved')).toBe('active')
    expect(statusTone('drafting_ir')).toBe('active')
    expect(statusTone('generation_started')).toBe('active')
  })

  it('returns success for completed/valid statuses', () => {
    expect(statusTone('completed')).toBe('success')
    expect(statusTone('succeeded')).toBe('success')
    expect(statusTone('ir_ready_for_generation')).toBe('success')
  })

  it('returns warning for user-action/repair statuses', () => {
    expect(statusTone('awaiting_user_clarification')).toBe('warning')
    expect(statusTone('ir_needs_repair')).toBe('warning')
    expect(statusTone('running')).toBe('warning')
  })

  it('returns error for failure and cancellation', () => {
    expect(statusTone('failed')).toBe('error')
    expect(statusTone('cancelled')).toBe('error')
  })

  it('returns neutral for initial/unknown statuses', () => {
    expect(statusTone('drafting_spec')).toBe('neutral')
    expect(statusTone('queued')).toBe('neutral')
    expect(statusTone('unknown_status')).toBe('neutral')
  })
})

describe('displaySymbolsDir', () => {
  it('returns None for empty string', () => {
    expect(displaySymbolsDir('')).toBe('None')
  })

  it('returns Built-in symbols for built-in paths', () => {
    expect(displaySymbolsDir('/path/to/resources/symbols')).toBe('Built-in symbols')
    expect(displaySymbolsDir('/path/kicad_pcb/resources/anything')).toBe('Built-in symbols')
  })

  it('returns folder name for custom paths', () => {
    expect(displaySymbolsDir('/home/user/my_symbols')).toBe('my_symbols')
    expect(displaySymbolsDir('C:\\Users\\user\\symbols')).toBe('symbols')
  })
})

describe('asRecord', () => {
  it('returns a plain object as-is', () => {
    expect(asRecord({ a: 1, b: 'x' })).toEqual({ a: 1, b: 'x' })
  })

  it('returns empty object for non-objects', () => {
    expect(asRecord('string')).toEqual({})
    expect(asRecord(null)).toEqual({})
    expect(asRecord(undefined)).toEqual({})
    expect(asRecord([1, 2, 3])).toEqual({})
    expect(asRecord(42)).toEqual({})
  })
})

describe('canonicalWizardStep', () => {
  it('returns describe for initial/drafting statuses', () => {
    expect(canonicalWizardStep(makeSession({ status: 'drafting_spec' }))).toBe('describe')
    expect(canonicalWizardStep(makeSession({ status: 'awaiting_user_clarification' }))).toBe('describe')
  })

  it('returns spec when spec is ready for review', () => {
    expect(canonicalWizardStep(makeSession({ status: 'spec_ready_for_review' }))).toBe('spec')
  })

  it('returns ir for spec_approved, drafting_ir, ir_needs_repair', () => {
    expect(canonicalWizardStep(makeSession({ status: 'spec_approved' }))).toBe('ir')
    expect(canonicalWizardStep(makeSession({ status: 'drafting_ir' }))).toBe('ir')
    expect(canonicalWizardStep(makeSession({ status: 'ir_needs_repair' }))).toBe('ir')
  })

  it('returns generate when IR is ready or project is running/done', () => {
    expect(canonicalWizardStep(makeSession({ status: 'ir_ready_for_generation' }))).toBe('generate')
    expect(canonicalWizardStep(makeSession({ status: 'generation_started' }))).toBe('generate')
    expect(canonicalWizardStep(makeSession({ status: 'completed' }))).toBe('generate')
  })

  it('returns generate on failure when IR/validation exists', () => {
    expect(
      canonicalWizardStep(makeSession({ status: 'failed', ir_json: { components: [] } })),
    ).toBe('generate')
  })

  it('returns spec on failure when only spec exists', () => {
    expect(
      canonicalWizardStep(makeSession({ status: 'failed', ir_json: null })),
    ).toBe('spec')
  })
})

describe('wizardStepUnlocked', () => {
  it('unlocks steps at or before the canonical step', () => {
    const session = makeSession({ status: 'spec_ready_for_review' })
    expect(wizardStepUnlocked(session, 'describe')).toBe(true)
    expect(wizardStepUnlocked(session, 'spec')).toBe(true)
  })

  it('locks steps beyond the canonical step', () => {
    const session = makeSession({ status: 'spec_ready_for_review' })
    expect(wizardStepUnlocked(session, 'ir')).toBe(false)
    expect(wizardStepUnlocked(session, 'generate')).toBe(false)
  })
})

describe('wizardStepState', () => {
  it('returns current for the active step', () => {
    const session = makeSession({ status: 'spec_ready_for_review' })
    expect(wizardStepState(session, 'spec', 'spec')).toBe('current')
  })

  it('returns done for earlier steps', () => {
    const session = makeSession({ status: 'spec_ready_for_review' })
    expect(wizardStepState(session, 'spec', 'describe')).toBe('done')
  })

  it('returns ready for unlocked future steps', () => {
    const session = makeSession({ status: 'ir_ready_for_generation' })
    expect(wizardStepState(session, 'ir', 'generate')).toBe('ready')
  })

  it('returns locked for locked future steps', () => {
    const session = makeSession({ status: 'drafting_spec' })
    expect(wizardStepState(session, 'describe', 'spec')).toBe('locked')
    expect(wizardStepState(session, 'describe', 'ir')).toBe('locked')
  })
})
