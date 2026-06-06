import type { ReactNode } from 'react'
import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'

import { useBootstrapQuery } from './queries/bootstrapQueries'
import { queryKeys } from './queryKeys'
import { joinClasses, statusBannerToneClass } from './utils'

import {
  bannerBaseClass,
  buttonSecondaryClass,
  pageShellClass,
  spinnerClass,
} from './styles/designTokens'

import { HomePage } from './routes/HomePage'
import { WizardPage } from './routes/WizardPage'
import { JobPage } from './routes/JobPage'
import { JobsPage } from './routes/JobsPage'
import { SetupPage } from './routes/SetupPage'
import { JsonGeneratePage } from './routes/JsonGeneratePage'
import { SymbolsPage } from './routes/SymbolsPage'
import { readLastSession } from './utils/session'

// ─── Layout ───────────────────────────────────────────────────────────────────

function Layout({
  providerLabel,
  children,
}: {
  providerLabel: string | null
  children: ReactNode
}) {
  const lastSession = readLastSession()
  const { pathname } = useLocation()
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
                className={({ isActive }) => {
                  // The Wizard link may point to /wizard/:sessionId when a previous
                  // session exists. React Router NavLink won't match /wizard by prefix
                  // in that case, so we check pathname directly.
                  const active = label === 'Wizard' ? pathname.startsWith('/wizard') : isActive
                  return joinClasses(
                    'rounded-full px-4 py-2 text-sm font-medium no-underline transition-all duration-150',
                    active
                      ? '-translate-y-px bg-[rgba(161,69,26,0.12)] text-[var(--brand-deep)]'
                      : 'text-[var(--muted)] hover:-translate-y-px hover:bg-[rgba(161,69,26,0.12)] hover:text-[var(--brand-deep)]',
                  )
                }}
                to={to}
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="flex flex-col items-end gap-px text-right text-[0.86rem] text-[var(--muted)] lg:items-start lg:text-left">
            <span>Provider</span>
            <strong>{providerLabel ?? 'loading'}</strong>
          </div>
        </div>
      </header>
      <main className={pageShellClass}>{children}</main>
    </div>
  )
}

// ─── AppShell ─────────────────────────────────────────────────────────────────

function AppShell() {
  const queryClient = useQueryClient()
  const { data: bootstrap, isLoading, error } = useBootstrapQuery()

  if (isLoading || (!bootstrap && !error)) {
    return (
      <Layout providerLabel={null}>
        <div className={joinClasses(bannerBaseClass, statusBannerToneClass('active'), 'mt-4')}>
          <span className={spinnerClass} aria-hidden="true"></span>
          <strong>Loading UI bootstrap…</strong>
        </div>
      </Layout>
    )
  }

  if (error || !bootstrap) {
    const message = error instanceof Error ? error.message : 'Failed to load bootstrap.'
    return (
      <Layout providerLabel={null}>
        <div className={joinClasses(bannerBaseClass, statusBannerToneClass('error'), 'mt-4')}>
          <strong>{message}</strong>
          <button
            type="button"
            className={joinClasses(buttonSecondaryClass, 'ml-auto')}
            onClick={() => void queryClient.invalidateQueries({ queryKey: queryKeys.bootstrap })}
          >
            Retry
          </button>
        </div>
      </Layout>
    )
  }

  return (
    <Layout providerLabel={bootstrap.llm_provider}>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/wizard" element={<WizardPage />} />
        <Route path="/wizard/:sessionId" element={<WizardPage />} />
        <Route path="/wizard/:sessionId/:step" element={<WizardPage />} />
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
