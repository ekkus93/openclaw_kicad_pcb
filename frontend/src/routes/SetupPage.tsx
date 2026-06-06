import { useDoctorQuery } from '../queries/setupQueries'
import { joinClasses } from '../utils'
import {
  bannerBaseClass,
  eyebrowClass,
  headingGroupClass,
  heroLeadClass,
  pageStackClass,
  panelAccentClass,
  panelSoftClass,
  spinnerClass,
} from '../styles/designTokens'

// ─── Required vs optional tool heuristics ────────────────────────────────────

const OPTIONAL_TOOL_NAMES = new Set([
  'openai',
  'anthropic',
  'llm_provider',
  'llm',
])

function isOptionalCheck(name: string): boolean {
  const lower = name.toLowerCase()
  return (
    OPTIONAL_TOOL_NAMES.has(lower) ||
    lower.includes('llm') ||
    lower.includes('provider') ||
    lower.includes('optional')
  )
}

// ─── CheckRow ─────────────────────────────────────────────────────────────────

function CheckRow({
  name,
  ok,
  detail,
  optional,
}: {
  name: string
  ok: boolean
  detail: string
  optional: boolean
}) {
  const statusColor = ok
    ? 'text-[var(--success)]'
    : optional
      ? 'text-[var(--warning)]'
      : 'text-[var(--error)]'
  const statusLabel = ok ? 'OK' : optional ? 'Missing (optional)' : 'Failed'
  const rowBg = ok
    ? 'border-[rgba(35,102,79,0.12)] bg-[rgba(35,102,79,0.04)]'
    : optional
      ? 'border-[rgba(155,106,18,0.15)] bg-[rgba(155,106,18,0.04)]'
      : 'border-[rgba(154,45,40,0.15)] bg-[rgba(154,45,40,0.04)]'

  return (
    <div className={joinClasses('rounded-[18px] border p-4', rowBg)}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <div className="flex items-center gap-2">
            <span
              className={joinClasses(
                'flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-[0.7rem] font-bold text-white',
                ok
                  ? 'bg-[var(--success)]'
                  : optional
                    ? 'bg-[var(--warning)]'
                    : 'bg-[var(--error)]',
              )}
              aria-hidden="true"
            >
              {ok ? '✓' : '✕'}
            </span>
            <strong className="text-[0.95rem] text-[var(--text)]">{name}</strong>
            {optional ? (
              <span className="rounded-full bg-[rgba(117,99,80,0.1)] px-2 py-0.5 text-[0.7rem] font-semibold uppercase tracking-[0.1em] text-[var(--muted)]">
                optional
              </span>
            ) : null}
          </div>
          {detail ? (
            <p className="ml-7 text-[0.88rem] leading-6 text-[var(--muted)]">{detail}</p>
          ) : null}
        </div>
        <span className={joinClasses('text-[0.82rem] font-bold', statusColor)}>{statusLabel}</span>
      </div>
    </div>
  )
}

// ─── SetupPage ────────────────────────────────────────────────────────────────

export function SetupPage() {
  const { data: doctor, isLoading, error } = useDoctorQuery()

  const allOk = doctor?.ok ?? false
  const checks = doctor?.checks ?? []
  const requiredChecks = checks.filter((c) => !isOptionalCheck(c.name))
  const optionalChecks = checks.filter((c) => isOptionalCheck(c.name))
  const failedRequired = requiredChecks.filter((c) => !c.ok)

  return (
    <div className={pageStackClass}>
      <section className={panelAccentClass}>
        <div className={headingGroupClass}>
          <p className={eyebrowClass}>Setup</p>
          <h1>Doctor Check</h1>
          <p className={heroLeadClass}>
            Verify all required tools, dependencies, and LLM configuration are in place.
          </p>
        </div>
        {doctor ? (
          <div
            className={joinClasses(
              'flex items-center gap-3 rounded-[18px] border px-[1.1rem] py-[0.9rem]',
              allOk
                ? 'border-[rgba(35,102,79,0.2)] bg-[rgba(35,102,79,0.08)] text-[var(--success)]'
                : 'border-[rgba(154,45,40,0.2)] bg-[rgba(154,45,40,0.08)] text-[var(--error)]',
            )}
          >
            <span
              className={joinClasses(
                'flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[0.8rem] font-bold text-white',
                allOk ? 'bg-[var(--success)]' : 'bg-[var(--error)]',
              )}
              aria-hidden="true"
            >
              {allOk ? '✓' : '✕'}
            </span>
            <strong>
              {allOk
                ? 'All required checks passed.'
                : `${failedRequired.length} required check${failedRequired.length === 1 ? '' : 's'} failed.`}
            </strong>
          </div>
        ) : null}
      </section>

      {isLoading ? (
        <div className={joinClasses(bannerBaseClass, 'border-[rgba(22,93,143,0.2)] bg-[rgba(22,93,143,0.1)] text-[#0d4c74] mt-2')}>
          <span className={spinnerClass} aria-hidden="true"></span>
          <strong>Running doctor checks…</strong>
        </div>
      ) : null}

      {error ? (
        <div className={joinClasses(bannerBaseClass, 'border-[rgba(154,45,40,0.18)] bg-[rgba(154,45,40,0.09)] text-[var(--error)] mt-2')}>
          <strong>
            {error instanceof Error ? error.message : 'Failed to load doctor results.'}
          </strong>
        </div>
      ) : null}

      {requiredChecks.length > 0 ? (
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Required Tools</h2>
            <p className="text-[var(--muted)]">
              These must pass for KiCad project generation to work.
            </p>
          </div>
          <div className="grid gap-3">
            {requiredChecks.map((check) => (
              <CheckRow
                key={check.name}
                name={check.name}
                ok={check.ok}
                detail={check.detail}
                optional={false}
              />
            ))}
          </div>
        </section>
      ) : null}

      {optionalChecks.length > 0 ? (
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Optional / LLM Configuration</h2>
            <p className="text-[var(--muted)]">
              Required for the AI Wizard. Not needed for Circuit IR JSON generation.
            </p>
          </div>
          <div className="grid gap-3">
            {optionalChecks.map((check) => (
              <CheckRow
                key={check.name}
                name={check.name}
                ok={check.ok}
                detail={check.detail}
                optional={true}
              />
            ))}
          </div>
        </section>
      ) : null}

      {!isLoading && checks.length === 0 && !error ? (
        <section className={panelSoftClass}>
          <p className="text-[var(--muted)]">No doctor checks returned.</p>
        </section>
      ) : null}
    </div>
  )
}
