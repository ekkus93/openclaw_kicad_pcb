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

function renderGenerateStep(sessionOverrides: Parameters<typeof makeSession>[0] = {}, busyMessage: string | null = null) {
  const session = makeSession({ status: 'ir_ready_for_generation', spec_approved: true, ...sessionOverrides })
  const checkpoint = wizardCurrentCheckpoint(session, 'generate')
  return renderWithProviders(
    <WizardGenerateStep
      session={session}
      sessionId="test-session-123"
      projectLabel={null}
      checkpoint={checkpoint}
      busyMessage={busyMessage}
      canGenerateProject
      visibleLatestJob={null}
      confirmRegenerate={false}
      setConfirmRegenerate={() => undefined}
      onGenerateProject={() => undefined}
    />,
  )
}

describe('WizardGenerateStep — job polling indicator (A3)', () => {
  function renderWithJob(jobStatus: string, busyMessage: string | null) {
    const session = makeSession({ status: 'ir_ready_for_generation', spec_approved: true })
    const checkpoint = wizardCurrentCheckpoint(session, 'generate')
    return renderWithProviders(
      <WizardGenerateStep
        session={session}
        sessionId="test-session-123"
        projectLabel={null}
        checkpoint={checkpoint}
        busyMessage={busyMessage}
        canGenerateProject
        visibleLatestJob={makeJob({ status: jobStatus as 'queued' | 'running' | 'succeeded' })}
        confirmRegenerate={false}
        setConfirmRegenerate={() => undefined}
        onGenerateProject={() => undefined}
      />,
    )
  }

  it('shows polling indicator when job is queued and not busy', () => {
    renderWithJob('queued', null)
    expect(screen.getByText(/Generation in progress/)).toBeInTheDocument()
  })

  it('shows polling indicator when job is running and not busy', () => {
    renderWithJob('running', null)
    expect(screen.getByText(/Generation in progress/)).toBeInTheDocument()
  })

  it('hides polling indicator when busyMessage is set', () => {
    renderWithJob('running', 'Submitting job…')
    expect(screen.queryByText(/Generation in progress/)).not.toBeInTheDocument()
  })

  it('does not show polling indicator for succeeded jobs', () => {
    renderWithJob('succeeded', null)
    expect(screen.queryByText(/Generation in progress/)).not.toBeInTheDocument()
  })

  it('does not show polling indicator for failed jobs', () => {
    renderWithJob('failed', null)
    expect(screen.queryByText(/Generation in progress/)).not.toBeInTheDocument()
  })
})

describe('WizardGenerateStep — project settings summary (task 3)', () => {
  it('displays the project name when present', () => {
    renderGenerateStep({ project_name: 'LED Blinker' })
    expect(screen.getByText('LED Blinker')).toBeInTheDocument()
  })

  it('displays the symbols directory (formatted) when present', () => {
    renderGenerateStep({ symbols_dir: '/home/user/my_symbols' })
    expect(screen.getByText('my_symbols')).toBeInTheDocument()
  })

  it('shows fallback text for missing project name', () => {
    renderGenerateStep({ project_name: undefined })
    const notSetEls = screen.getAllByText('Not set')
    expect(notSetEls.length).toBeGreaterThanOrEqual(1)
  })

  it('shows fallback text for missing symbols directory', () => {
    renderGenerateStep({ symbols_dir: undefined })
    const notSetEls = screen.getAllByText('Not set')
    expect(notSetEls.length).toBeGreaterThanOrEqual(1)
  })

  it('shows an Edit project details link when not busy', () => {
    renderGenerateStep()
    const link = screen.getByRole('link', { name: 'Edit project details' })
    expect(link).toHaveAttribute('href', '/wizard/test-session-123/describe')
  })

  it('disables the Edit project details link when a busy action is running', () => {
    renderGenerateStep({}, 'Generating…')
    expect(screen.queryByRole('link', { name: 'Edit project details' })).toBeNull()
    expect(screen.getByText(/Edit project details/)).toBeInTheDocument()
  })
})

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
