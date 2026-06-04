import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
} from 'react-router-dom'

import { ApiError, api } from './api'
import type { UiBootstrapResponse } from './types'

import {
  bannerBaseClass,
  buttonSecondaryClass,
  pageShellClass,
  spinnerClass,
} from './styles/designTokens'

import { HomePage } from './routes/HomePage'
import { WizardPage, readLastSession } from './routes/WizardPage'
import { JobPage } from './routes/JobPage'
import { JobsPage } from './routes/JobsPage'
import { SetupPage } from './routes/SetupPage'
import { JsonGeneratePage } from './routes/JsonGeneratePage'
import { SymbolsPage } from './routes/SymbolsPage'

// ─── Utility functions ────────────────────────────────────────────────────────

function joinClasses(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}

function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) {
    return error.message
  }
  return 'Unexpected error.'
}

// ─── Status helpers ───────────────────────────────────────────────────────────

type StatusTone = 'neutral' | 'active' | 'success' | 'warning' | 'error'

function statusBannerToneClass(tone: StatusTone): string {
  const tones: Record<StatusTone, string> = {
    active: 'border-[rgba(22,93,143,0.2)] bg-[rgba(22,93,143,0.1)] text-[#0d4c74]',
    success: 'border-[rgba(35,102,79,0.2)] bg-[rgba(35,102,79,0.1)] text-[var(--success)]',
    warning: 'border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.11)] text-[var(--warning)]',
    error: 'border-[rgba(154,45,40,0.18)] bg-[rgba(154,45,40,0.09)] text-[var(--error)]',
    neutral: 'border-[rgba(117,99,80,0.16)] bg-[rgba(117,99,80,0.1)] text-[var(--muted)]',
  }
  return tones[tone]
}

// ─── Layout ───────────────────────────────────────────────────────────────────

function Layout({
  bootstrap,
  children,
}: {
  bootstrap: UiBootstrapResponse | null
  children: ReactNode
}) {
  const lastSession = readLastSession()
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-[var(--border)] bg-[rgba(245,239,226,0.82)] backdrop-blur-[18px]">
        <div className="mx-auto grid max-w-[1320px] grid-cols-[auto_1fr_auto] items-center gap-4 px-6 py-4 lg:px-4">
          <Link className="text-xl font-bold no-underline [font-family:var(--font-heading)] tracking-[0.01em]" to="/">
            KiCad PCB Web App
          </Link>
          <nav className="flex flex-wrap justify-center gap-2">
            {[
              { to: lastSession ? `/wizard/${lastSession}` : '/wizard', label: 'Wizard' },
              { to: '/generate-json', label: 'Circuit IR' },
              { to: '/jobs', label: 'Jobs' },
              { to: '/symbols', label: 'Symbols' },
              { to: '/setup', label: 'Setup' },
            ].map(({ to, label }) => (
              <NavLink
                key={label}
                className={({ isActive }) =>
                  joinClasses(
                    'rounded-full px-4 py-2 text-sm font-medium no-underline transition-all duration-150',
                    isActive
                      ? '-translate-y-px bg-[rgba(161,69,26,0.12)] text-[var(--brand-deep)]'
                      : 'text-[var(--muted)] hover:-translate-y-px hover:bg-[rgba(161,69,26,0.12)] hover:text-[var(--brand-deep)]',
                  )
                }
                to={to}
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="flex flex-col items-end gap-px text-right text-[0.86rem] text-[var(--muted)] lg:items-start lg:text-left">
            <span>Provider</span>
            <strong>{bootstrap?.llm_provider ?? 'loading'}</strong>
          </div>
        </div>
      </header>
      <main className={pageShellClass}>{children}</main>
    </div>
  )
}

// ─── AppShell ─────────────────────────────────────────────────────────────────

function AppShell() {
  const [bootstrap, setBootstrap] = useState<UiBootstrapResponse | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setBootstrap(null)
    setErrorMessage(null)
    void api
      .getBootstrap()
      .then((response) => {
        if (!cancelled) {
          setBootstrap(response)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorMessage(getErrorMessage(error))
        }
      })
    return () => {
      cancelled = true
    }
  }, [retryKey])

  if (!bootstrap) {
    return (
      <Layout bootstrap={null}>
        <div
          className={joinClasses(
            bannerBaseClass,
            statusBannerToneClass(errorMessage ? 'error' : 'active'),
            'mt-4',
          )}
        >
          {errorMessage ? (
            <>
              <strong>{errorMessage}</strong>
              <button
                type="button"
                className={joinClasses(buttonSecondaryClass, 'ml-auto')}
                onClick={() => setRetryKey((k) => k + 1)}
              >
                Retry
              </button>
            </>
          ) : (
            <>
              <span className={spinnerClass} aria-hidden="true"></span>
              <strong>Loading UI bootstrap…</strong>
            </>
          )}
        </div>
      </Layout>
    )
  }

  return (
    <Layout bootstrap={bootstrap}>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/wizard" element={<WizardPage bootstrap={bootstrap} />} />
        <Route path="/wizard/:sessionId" element={<WizardPage bootstrap={bootstrap} />} />
        <Route path="/wizard/:sessionId/:step" element={<WizardPage bootstrap={bootstrap} />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/jobs/:jobId" element={<JobPage />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/generate-json" element={<JsonGeneratePage />} />
        <Route path="/symbols" element={<SymbolsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  )
}
