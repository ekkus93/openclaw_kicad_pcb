import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// Replace BrowserRouter with MemoryRouter at /wizard for all tests in this file.
// This lets us control the current path without a real browser history API.
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  const { MemoryRouter } = actual
  return {
    ...actual,
    BrowserRouter: ({ children }: { children: React.ReactNode }) =>
      React.createElement(MemoryRouter, { initialEntries: ['/wizard'] }, children),
  }
})

// Stub all page components so they don't fire API queries of their own.
vi.mock('../routes/WizardPage', () => ({
  WizardPage: () => React.createElement('div', { 'data-testid': 'wizard-page' }),
}))
vi.mock('../routes/HomePage', () => ({
  HomePage: () => React.createElement('div', { 'data-testid': 'home-page' }),
}))
vi.mock('../routes/JobPage', () => ({
  JobPage: () => React.createElement('div', { 'data-testid': 'job-page' }),
}))
vi.mock('../routes/JobsPage', () => ({
  JobsPage: () => React.createElement('div', { 'data-testid': 'jobs-page' }),
}))
vi.mock('../routes/SetupPage', () => ({
  SetupPage: () => React.createElement('div', { 'data-testid': 'setup-page' }),
}))
vi.mock('../routes/JsonGeneratePage', () => ({
  JsonGeneratePage: () => React.createElement('div', { 'data-testid': 'json-page' }),
}))
vi.mock('../routes/SymbolsPage', () => ({
  SymbolsPage: () => React.createElement('div', { 'data-testid': 'symbols-page' }),
}))

vi.mock('../queries/bootstrapQueries', () => ({
  useBootstrapQuery: () => ({
    data: { llm_enabled: true, llm_provider: 'openai', example_netlist_json: {} },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}))

import App from '../App'

function renderApp() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>,
  )
}

describe('Wizard nav active state (task 4.1)', () => {
  beforeEach(() => {
    localStorage.setItem('lastWizardSession', 'abc123')
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('marks Wizard nav link as active when pathname is /wizard, even when link target is a session URL', () => {
    renderApp()
    const wizardLink = screen.getByRole('link', { name: 'Wizard' })
    // Link target should be the last session URL
    expect(wizardLink).toHaveAttribute('href', '/wizard/abc123')
    // Active state has brand-deep text colour directly (not behind hover:).
    // Split on whitespace to match exact class tokens.
    const classes = wizardLink.className.split(/\s+/)
    expect(classes).toContain('text-[var(--brand-deep)]')
    expect(classes).not.toContain('text-[var(--muted)]')
  })

  it('Wizard nav link points to /wizard when no prior session exists', () => {
    localStorage.clear()
    renderApp()
    const wizardLink = screen.getByRole('link', { name: 'Wizard' })
    expect(wizardLink).toHaveAttribute('href', '/wizard')
  })

  it('other nav links are inactive on /wizard', () => {
    renderApp()
    const jobsLink = screen.getByRole('link', { name: 'Jobs' })
    // Split on whitespace to match exact class tokens — avoids false positive from
    // hover:text-[var(--brand-deep)] containing text-[var(--brand-deep)] as a substring.
    const classes = jobsLink.className.split(/\s+/)
    expect(classes).toContain('text-[var(--muted)]')
    expect(classes).not.toContain('text-[var(--brand-deep)]')
  })
})
