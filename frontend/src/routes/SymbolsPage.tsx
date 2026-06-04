import { useState } from 'react'

import { useSymbolSearchQuery } from '../queries/symbolQueries'
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

function joinClasses(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}

// ─── SymbolsPage ──────────────────────────────────────────────────────────────

export function SymbolsPage() {
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')

  // Simple debounce via timeout
  function handleQueryChange(value: string) {
    setQuery(value)
    const trimmed = value.trim()
    // Immediately clear debounced query when input is empty
    if (!trimmed) {
      setDebouncedQuery('')
      return
    }
    const timer = setTimeout(() => {
      setDebouncedQuery(trimmed)
    }, 300)
    return () => clearTimeout(timer)
  }

  const { data, isLoading, error } = useSymbolSearchQuery(debouncedQuery)

  const results = data?.results ?? []
  const hasQuery = debouncedQuery.length > 0

  return (
    <div className={pageStackClass}>
      <section className={panelAccentClass}>
        <div className={headingGroupClass}>
          <p className={eyebrowClass}>Symbols</p>
          <h1>Search KiCad Symbols</h1>
          <p className={heroLeadClass}>
            Search the available KiCad symbol libraries to find components for use in your circuit.
          </p>
        </div>

        <label className="grid gap-2">
          <span className="text-[0.86rem] font-semibold text-[var(--text)]">Search symbols</span>
          <input
            type="search"
            className="rounded-[18px] border border-[rgba(88,63,39,0.16)] bg-[rgba(255,255,255,0.82)] px-4 py-3 text-[0.95rem] shadow-[0_4px_12px_rgba(71,43,19,0.06)] transition-[border-color,box-shadow] duration-150 focus:border-[rgba(109,47,20,0.3)] focus:outline-none focus:ring-2 focus:ring-[rgba(109,47,20,0.12)]"
            placeholder="e.g. LM358, NE555, ATmega328"
            value={query}
            onChange={(e) => handleQueryChange(e.target.value)}
            autoFocus
          />
        </label>
      </section>

      {isLoading ? (
        <div className={joinClasses(bannerBaseClass, 'border-[rgba(22,93,143,0.2)] bg-[rgba(22,93,143,0.1)] text-[#0d4c74]')}>
          <span className={spinnerClass} aria-hidden="true"></span>
          <strong>Searching…</strong>
        </div>
      ) : null}

      {error ? (
        <div className={joinClasses(bannerBaseClass, 'border-[rgba(154,45,40,0.18)] bg-[rgba(154,45,40,0.09)] text-[var(--error)]')}>
          <strong>
            {error instanceof Error ? error.message : 'Failed to search symbols.'}
          </strong>
        </div>
      ) : null}

      {hasQuery && !isLoading && !error && results.length === 0 ? (
        <section className={panelSoftClass}>
          <p className="text-[var(--muted)]">
            No symbols found for <strong>{debouncedQuery}</strong>. Try a different part number or keyword.
          </p>
        </section>
      ) : null}

      {!hasQuery && !isLoading ? (
        <section className={panelSoftClass}>
          <p className="text-[var(--muted)]">
            Enter a part number, name, or keyword above to search the symbol libraries.
          </p>
        </section>
      ) : null}

      {results.length > 0 ? (
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Results</h2>
            <p className="text-[var(--muted)]">
              {results.length} result{results.length === 1 ? '' : 's'} for <strong>{debouncedQuery}</strong>
            </p>
          </div>
          <div className="grid gap-2">
            {results.map((symbol) => (
              <div
                key={symbol.qualified_name}
                className="grid gap-1 rounded-[18px] border border-[rgba(88,63,39,0.12)] bg-[rgba(255,255,255,0.6)] p-4"
              >
                <div className="flex flex-wrap items-baseline gap-2">
                  <strong className="text-[0.95rem] text-[var(--text)]">{symbol.name}</strong>
                  <span className="text-[0.78rem] font-mono text-[var(--muted)]">{symbol.qualified_name}</span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-full bg-[rgba(88,63,39,0.08)] px-2.5 py-0.5 text-[0.76rem] font-semibold text-[var(--muted)]">
                    {symbol.library}
                  </span>
                  {symbol.keywords.length > 0 ? (
                    <span className="text-[0.76rem] text-[var(--muted)]">
                      {symbol.keywords.slice(0, 6).join(', ')}
                    </span>
                  ) : null}
                </div>
                {symbol.aliases.length > 0 ? (
                  <p className="text-[0.76rem] text-[var(--muted)]">
                    Aliases: {symbol.aliases.join(', ')}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  )
}
