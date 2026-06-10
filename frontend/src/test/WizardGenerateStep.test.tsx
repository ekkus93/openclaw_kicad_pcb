import { useState } from 'react'
import { describe, it, expect, vi } from 'vitest'
import { screen, fireEvent } from '@testing-library/react'

import { WizardGenerateStep } from '../routes/wizard/WizardGenerateStep'
import { wizardCurrentCheckpoint } from '../routes/wizard/wizardStepLogic'
import { renderWithProviders } from './renderWithProviders'
import { makeSession, makeJob } from './fixtures'

const SESSION = makeSession({ status: 'ir_ready_for_generation', spec_approved: true })
const CHECKPOINT = wizardCurrentCheckpoint(SESSION, 'generate')

// Stateful wrapper that mirrors the controller's confirmRegenerate logic,
// allowing us to test the full click flow through the component.
function GenerateStepHarness({
  jobStatus,
  onGenerate,
}: {
  jobStatus: 'succeeded' | 'failed' | 'running'
  onGenerate: () => void
}) {
  const [confirmRegenerate, setConfirmRegenerate] = useState(false)

  function handleGenerate() {
    if (jobStatus === 'succeeded' && !confirmRegenerate) {
      setConfirmRegenerate(true)
      return
    }
    setConfirmRegenerate(false)
    onGenerate()
  }

  return (
    <WizardGenerateStep
      session={SESSION}
      sessionId="test-session-123"
      projectLabel="Test LED blinker"
      checkpoint={CHECKPOINT}
      busyMessage={null}
      canGenerateProject
      visibleLatestJob={makeJob({ status: jobStatus })}
      confirmRegenerate={confirmRegenerate}
      setConfirmRegenerate={setConfirmRegenerate}
      onGenerateProject={handleGenerate}
    />
  )
}

describe('WizardGenerateStep — inline regenerate confirmation (task 4.2)', () => {
  it('shows Generate Again when a succeeded job exists', () => {
    renderWithProviders(<GenerateStepHarness jobStatus="succeeded" onGenerate={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Generate Again' })).toBeInTheDocument()
  })

  it('clicking Generate Again shows inline confirmation, not window.confirm', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderWithProviders(<GenerateStepHarness jobStatus="succeeded" onGenerate={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Generate Again' }))

    expect(screen.getByText(/This will replace the current generation result/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm — Generate Again' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(confirmSpy).not.toHaveBeenCalled()

    confirmSpy.mockRestore()
  })

  it('Cancel hides the confirmation banner', () => {
    renderWithProviders(<GenerateStepHarness jobStatus="succeeded" onGenerate={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Generate Again' }))
    expect(screen.getByText(/This will replace the current generation result/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByText(/This will replace the current generation result/)).not.toBeInTheDocument()
  })

  it('Confirm — Generate Again invokes the generation handler', () => {
    const mockGenerate = vi.fn()
    renderWithProviders(<GenerateStepHarness jobStatus="succeeded" onGenerate={mockGenerate} />)

    fireEvent.click(screen.getByRole('button', { name: 'Generate Again' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm — Generate Again' }))

    expect(mockGenerate).toHaveBeenCalledOnce()
  })
})

describe('WizardGenerateStep — failed job retry label/style (task 4.3)', () => {
  it('shows Retry Generation label when latest job failed', () => {
    renderWithProviders(<GenerateStepHarness jobStatus="failed" onGenerate={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Retry Generation' })).toBeInTheDocument()
  })

  it('clicking Retry Generation does not show a confirmation banner', () => {
    const mockGenerate = vi.fn()
    renderWithProviders(<GenerateStepHarness jobStatus="failed" onGenerate={mockGenerate} />)

    fireEvent.click(screen.getByRole('button', { name: 'Retry Generation' }))

    expect(screen.queryByText(/This will replace the current generation result/)).not.toBeInTheDocument()
    expect(mockGenerate).toHaveBeenCalledOnce()
  })

  it('Retry Generation uses primary style, not the destructive danger style', () => {
    renderWithProviders(<GenerateStepHarness jobStatus="failed" onGenerate={vi.fn()} />)
    const btn = screen.getByRole('button', { name: 'Retry Generation' })
    // Danger style has text-[var(--error)]; primary style does not
    expect(btn.className).not.toContain('text-[var(--error)]')
  })

  it('Generate Again on succeeded job uses the danger style', () => {
    renderWithProviders(<GenerateStepHarness jobStatus="succeeded" onGenerate={vi.fn()} />)
    const btn = screen.getByRole('button', { name: 'Generate Again' })
    expect(btn.className).toContain('text-[var(--error)]')
  })
})
