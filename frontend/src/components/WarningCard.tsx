import { joinClasses } from '../utils'
import {
  emptyCopyClass,
  headingGroupClass,
  mutedCopyClass,
  panelSoftClass,
} from '../styles/designTokens'

export function WarningCard({ warning }: { warning: Record<string, unknown> }) {
  const code = typeof warning.code === 'string' ? warning.code : null
  const message = typeof warning.message === 'string' ? warning.message : null
  const severity = typeof warning.severity === 'string' ? warning.severity : 'warning'
  const family = typeof warning.family === 'string' ? warning.family : null
  const details = warning.details && typeof warning.details === 'object' && !Array.isArray(warning.details)
    ? (warning.details as Record<string, unknown>)
    : null

  const severityColors = {
    error:   'border-[rgba(154,45,40,0.2)]  bg-[rgba(154,45,40,0.05)]',
    warning: 'border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.05)]',
    info:    'border-[rgba(22,93,143,0.15)]  bg-[rgba(22,93,143,0.05)]',
  }
  const badgeColors = {
    error:   'bg-[rgba(154,45,40,0.1)]  text-[var(--error)]',
    warning: 'bg-[rgba(155,106,18,0.12)] text-[var(--warning)]',
    info:    'bg-[rgba(22,93,143,0.1)]  text-[#0d4c74]',
  }
  const border = severityColors[severity as keyof typeof severityColors] ?? severityColors.warning
  const badge  = badgeColors[severity as keyof typeof badgeColors] ?? badgeColors.warning

  // Suppress the 'hint' detail key when its content is already embedded in the
  // message — avoids duplicating the hint text below the main message paragraph.
  const detailEntries = details
    ? Object.entries(details).filter(([k]) => k !== 'hint' || !message?.includes('hint'))
    : []

  return (
    <div className={joinClasses('rounded-[18px] border p-4', border)}>
      <div className="flex flex-wrap items-start gap-2">
        {severity !== 'warning' ? (
          <span className={joinClasses('rounded-full px-2 py-0.5 text-[0.72rem] font-bold uppercase tracking-[0.1em]', badge)}>
            {severity}
          </span>
        ) : null}
        {family ? (
          <span className="text-[0.75rem] font-semibold uppercase tracking-[0.1em] text-[var(--muted)]">
            {family.replaceAll('_', ' ')}
          </span>
        ) : null}
        {code ? (
          <code className="ml-auto rounded bg-[rgba(88,63,39,0.08)] px-1.5 py-0.5 text-[0.75rem] text-[var(--muted)]">
            {code}
          </code>
        ) : null}
      </div>
      {message ? (
        <p className="mt-2 text-[0.95rem] leading-6 text-[var(--text)]">{message}</p>
      ) : null}
      {detailEntries.length > 0 ? (
        <dl className="mt-3 grid gap-1 text-[0.82rem]">
          {detailEntries.map(([k, v]) => (
            <div key={k} className="flex flex-wrap gap-x-2">
              <dt className="font-semibold text-[var(--muted)]">{k.replaceAll('_', ' ')}:</dt>
              <dd className="text-[var(--text)]">
                {Array.isArray(v)
                  ? v.join(', ')
                  : typeof v === 'object' && v !== null
                    ? JSON.stringify(v)
                    : String(v)}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  )
}

export function WarningsPanel({ warnings }: { warnings: unknown[] }) {
  if (!warnings.length) {
    return (
      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h2>Warnings</h2>
        </div>
        <p className={emptyCopyClass}>No warnings.</p>
      </section>
    )
  }
  return (
    <section className={panelSoftClass}>
      <div className={headingGroupClass}>
        <h2>Warnings</h2>
        <p className={mutedCopyClass}>{warnings.length} warning{warnings.length === 1 ? '' : 's'}</p>
      </div>
      <div className="grid gap-3">
        {warnings.map((w, i) => (
          <WarningCard
            key={i}
            warning={typeof w === 'object' && w !== null && !Array.isArray(w)
              ? (w as Record<string, unknown>)
              : { message: String(w) }}
          />
        ))}
      </div>
    </section>
  )
}
