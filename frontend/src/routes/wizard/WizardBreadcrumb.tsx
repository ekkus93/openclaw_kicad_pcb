import { Link } from 'react-router-dom'

import { joinClasses } from '../../utils'
import type { WizardSessionDetail, WizardStep } from '../../types'
import { WIZARD_STEP_META, wizardStepState } from './wizardStepLogic'

interface WizardBreadcrumbProps {
  session: WizardSessionDetail
  currentStep: WizardStep
  sessionId: string
}

export function WizardBreadcrumb({ session, currentStep, sessionId }: WizardBreadcrumbProps) {
  return (
    <nav
      aria-label="Wizard steps"
      className="mb-2 rounded-[20px] border border-[var(--border)] bg-[rgba(255,252,247,0.9)] px-4 py-3 shadow-[0_6px_16px_rgba(71,43,19,0.06)]"
    >
      <ol className="flex flex-wrap items-center gap-1">
        {(Object.keys(WIZARD_STEP_META) as WizardStep[]).map((step, idx, arr) => {
          const state = wizardStepState(session, currentStep, step)
          const isLast = idx === arr.length - 1
          const pill = (
            <span
              aria-current={state === 'current' ? 'step' : undefined}
              className={joinClasses(
                'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[0.82rem] font-medium transition-colors duration-150',
                state === 'current'
                  ? 'bg-[rgba(161,69,26,0.14)] text-[var(--brand-deep)]'
                  : state === 'done'
                    ? 'text-[var(--success)] hover:bg-[rgba(35,102,79,0.08)]'
                    : state === 'ready'
                      ? 'text-[#0d4c74] hover:bg-[rgba(22,93,143,0.08)]'
                      : 'text-[var(--muted)]',
              )}
            >
              <span
                className={joinClasses(
                  'flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-[0.68rem] font-bold',
                  state === 'current'
                    ? 'bg-[var(--brand)] text-white'
                    : state === 'done'
                      ? 'bg-[var(--success)] text-white'
                      : 'bg-[rgba(117,99,80,0.15)] text-[var(--muted)]',
                )}
              >
                {state === 'done' ? '✓' : idx + 1}
              </span>
              {WIZARD_STEP_META[step].abbrev}
            </span>
          )
          return (
            <li key={step} className="flex items-center">
              {state === 'locked' || state === 'current' ? (
                pill
              ) : (
                <Link className="no-underline" to={`/wizard/${sessionId}/${step}`}>
                  {pill}
                </Link>
              )}
              {!isLast && (
                <span aria-hidden className="mx-1 select-none text-[var(--muted)]">
                  ›
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
