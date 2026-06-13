import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { makeSession } from './fixtures'

// Stub sub-step components to avoid needing their full prop/query setup.
vi.mock('../routes/wizard/WizardDescribeStep', () => ({
  WizardDescribeStep: () => React.createElement('div', { 'data-testid': 'describe-step' }),
}))
vi.mock('../routes/wizard/WizardSpecStep', () => ({
  WizardSpecStep: () => React.createElement('div', { 'data-testid': 'spec-step' }),
}))
vi.mock('../routes/wizard/WizardIrStep', () => ({
  WizardIrStep: () => React.createElement('div', { 'data-testid': 'ir-step' }),
}))
vi.mock('../routes/wizard/WizardGenerateStep', () => ({
  WizardGenerateStep: () => React.createElement('div', { 'data-testid': 'generate-step' }),
}))
vi.mock('../routes/wizard/WizardBreadcrumb', () => ({
  WizardBreadcrumb: () => React.createElement('div', { 'data-testid': 'wizard-breadcrumb' }),
}))
vi.mock('../routes/wizard/WizardStartStep', () => ({
  WizardStartStep: () => React.createElement('div', { 'data-testid': 'start-step' }),
}))

// Spy on the controller so tests can assert which args were passed.
vi.mock('../routes/wizard/useWizardController', () => ({
  useWizardController: vi.fn(),
}))

const mockSession = makeSession({ status: 'drafting_spec' })
const mockControllerReturn = {
  session: mockSession,
  sessionError: null,
  latestJob: undefined,
  currentStep: 'describe' as const,
  loading: false,
  projectName: '',
  setProjectName: () => undefined,
  symbolsDir: '',
  setSymbolsDir: () => undefined,
  message: '',
  setMessage: () => undefined,
  metaExpanded: false,
  setMetaExpanded: () => undefined,
  irRepairWarning: false,
  confirmRegenerate: false,
  setConfirmRegenerate: () => undefined,
  llmEnabled: true,
  llmProvider: 'openai',
  busyMessage: null,
  errorMessage: null,
  hasUnspecifiedCustomBlocks: false,
  canApproveSpec: false,
  canGenerateIr: false,
  canGenerateProject: false,
  visibleLatestJob: null,
  checkpoint: { title: 'Describe the circuit', detail: 'Provide details.' },
  projectLabel: null,
  generateIrMutationHasError: false,
  resetAll: () => undefined,
  handleCreateSession: async () => undefined,
  handleSendMessage: async () => undefined,
  handleApproveSpec: async () => undefined,
  handleGenerateIr: async () => undefined,
  handleClearIr: async () => undefined,
  handleGenerateProject: async () => undefined,
}

// Import after mocks so the module picks up mocked dependencies.
import { WizardPage } from '../routes/WizardPage'
import { useWizardController } from '../routes/wizard/useWizardController'

function renderWizardAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/wizard/:sessionId/:step?" element={<WizardPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('WizardPage — invalid route step', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useWizardController).mockReturnValue(mockControllerReturn)
  })

  it('renders the canonical describe step for an unrecognized step segment', () => {
    renderWizardAt('/wizard/abc123/not-a-step')
    expect(screen.getByTestId('describe-step')).toBeTruthy()
    expect(screen.queryByTestId('spec-step')).toBeNull()
    expect(screen.queryByTestId('ir-step')).toBeNull()
    expect(screen.queryByTestId('generate-step')).toBeNull()
  })

  it('does not render a blank or broken page for an unrecognized step', () => {
    renderWizardAt('/wizard/abc123/totally-invalid')
    expect(screen.getByTestId('wizard-breadcrumb')).toBeTruthy()
    expect(screen.getByTestId('describe-step')).toBeTruthy()
  })

  it('passes undefined to the controller for an invalid route step', () => {
    renderWizardAt('/wizard/abc123/not-a-step')
    expect(vi.mocked(useWizardController)).toHaveBeenCalledWith('abc123', undefined)
  })

  it('passes the step name to the controller for a valid route step', () => {
    renderWizardAt('/wizard/abc123/spec')
    expect(vi.mocked(useWizardController)).toHaveBeenCalledWith('abc123', 'spec')
  })
})

describe('WizardPage — NotFoundScreen (C1)', () => {
  function renderNotFound() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    vi.mocked(useWizardController).mockReturnValue({
      ...mockControllerReturn,
      session: undefined,
      loading: false,
      sessionError: new Error('Session not found'),
    })
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/wizard/missing-session/describe']}>
          <Routes>
            <Route path="/wizard/:sessionId/:step?" element={<WizardPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
  }

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('NotFoundScreen button is labelled "Start New Session"', () => {
    renderNotFound()
    expect(screen.getByRole('link', { name: 'Start New Session' })).toBeInTheDocument()
  })

  it('NotFoundScreen button links to /wizard', () => {
    renderNotFound()
    expect(screen.getByRole('link', { name: 'Start New Session' })).toHaveAttribute('href', '/wizard')
  })
})
