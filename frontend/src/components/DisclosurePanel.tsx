import { useState } from 'react'
import type { ReactNode } from 'react'

import { joinClasses } from '../utils'
import { panelSoftClass } from '../styles/designTokens'

export function DisclosurePanel({
  title,
  defaultOpen = false,
  children,
}: {
  title: string
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className={panelSoftClass}>
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <h2>{title}</h2>
        <span
          className={joinClasses(
            'flex-shrink-0 text-[1.1rem] leading-none text-[var(--muted)] transition-transform duration-150',
            open ? 'rotate-180' : '',
          )}
          aria-hidden="true"
        >
          ▾
        </span>
      </button>
      {open ? <div className="mt-4">{children}</div> : null}
    </section>
  )
}

// ─── DiagnosticsPayload ───────────────────────────────────────────────────────

export type DiagnosticsPayload = {
  symbol_count?: number
  wire_count?: number
  label_count?: number
  global_label_count?: number
  junction_count?: number
  binding_marker_count?: number
  missing_bindings?: string[]
  unexpected_bindings?: string[]
  duplicate_bindings?: string[]
  hard_failures?: Array<{ code?: string; message?: string; details?: unknown }>
  [key: string]: unknown
}

export function BuildSummaryPanel({ diagnostics }: { diagnostics: DiagnosticsPayload }) {
  const symbols = diagnostics.symbol_count ?? 0
  const wires = diagnostics.wire_count ?? 0
  const labels = diagnostics.label_count ?? 0
  const missingBindings = diagnostics.missing_bindings ?? []
  const unexpectedBindings = diagnostics.unexpected_bindings ?? []
  const duplicateBindings = diagnostics.duplicate_bindings ?? []
  const hardFailures = diagnostics.hard_failures ?? []
  const hasIssues =
    missingBindings.length > 0 ||
    unexpectedBindings.length > 0 ||
    duplicateBindings.length > 0 ||
    hardFailures.length > 0

  return (
    <DisclosurePanel title="Build Summary">
      <div className="grid gap-4">
        <ul className="m-0 grid list-disc gap-2 pl-5 text-[0.95rem] leading-6 text-[var(--text)]">
          <li>
            <strong>{symbols}</strong>{' '}
            {symbols === 1 ? 'component' : 'components'} placed in the schematic
          </li>
          <li>
            <strong>{wires}</strong>{' '}
            wire {wires === 1 ? 'connection' : 'connections'} routed between pins
          </li>
          {labels > 0 ? (
            <li>
              <strong>{labels}</strong>{' '}
              net {labels === 1 ? 'label' : 'labels'} added to identify signal connections
            </li>
          ) : null}
        </ul>
        {hasIssues ? (
          <div className="grid gap-3">
            {hardFailures.map((f, i) => (
              <div key={i} className="rounded-[14px] border border-[rgba(154,45,40,0.2)] bg-[rgba(154,45,40,0.05)] p-3">
                <p className="text-[0.82rem] font-bold text-[var(--error)]">Generation error</p>
                {f.message ? (
                  <p className="mt-1 text-[0.92rem] leading-6 text-[var(--text)]">{f.message}</p>
                ) : null}
                {f.code ? (
                  <p className="mt-1 text-[0.78rem] text-[var(--muted)]">Code: {f.code}</p>
                ) : null}
              </div>
            ))}
            {missingBindings.length > 0 ? (
              <div className="rounded-[14px] border border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.05)] p-3">
                <p className="text-[0.82rem] font-bold text-[var(--warning)]">Missing net connections</p>
                <p className="mt-1 text-[0.88rem] leading-6 text-[var(--muted)]">
                  These nets were declared in the IR but have no corresponding wire or label in the schematic:{' '}
                  <span className="text-[var(--text)]">{missingBindings.join(', ')}</span>
                </p>
              </div>
            ) : null}
            {unexpectedBindings.length > 0 ? (
              <div className="rounded-[14px] border border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.05)] p-3">
                <p className="text-[0.82rem] font-bold text-[var(--warning)]">Unexpected net connections</p>
                <p className="mt-1 text-[0.88rem] leading-6 text-[var(--muted)]">
                  These connections appear in the schematic but were not in the IR:{' '}
                  <span className="text-[var(--text)]">{unexpectedBindings.join(', ')}</span>
                </p>
              </div>
            ) : null}
            {duplicateBindings.length > 0 ? (
              <div className="rounded-[14px] border border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.05)] p-3">
                <p className="text-[0.82rem] font-bold text-[var(--warning)]">Duplicate net connections</p>
                <p className="mt-1 text-[0.88rem] leading-6 text-[var(--muted)]">
                  These nets appear more than once in the schematic:{' '}
                  <span className="text-[var(--text)]">{duplicateBindings.join(', ')}</span>
                </p>
              </div>
            ) : null}
          </div>
        ) : (
          <p className="text-[0.88rem] text-[var(--success)]">
            All net connections verified — no issues detected.
          </p>
        )}
      </div>
    </DisclosurePanel>
  )
}
