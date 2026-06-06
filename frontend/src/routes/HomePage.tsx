import { Link } from 'react-router-dom'

import { useBootstrapQuery } from '../queries/bootstrapQueries'
import { joinClasses } from '../utils'
import {
  eyebrowClass,
  headingGroupClass,
  heroLeadClass,
  pageStackClass,
  panelAccentClass,
  panelSoftClass,
} from '../styles/designTokens'

// ─── Workflow card ────────────────────────────────────────────────────────────

function WorkflowCard({
  title,
  description,
  href,
  disabled,
  disabledReason,
  tone = 'default',
}: {
  title: string
  description: string
  href: string
  disabled?: boolean
  disabledReason?: string
  tone?: 'default' | 'primary'
}) {
  const cardBase =
    'grid gap-2 rounded-[22px] border p-5 transition-[transform,box-shadow] duration-150 text-left'
  const cardEnabled =
    'border-[rgba(88,63,39,0.14)] bg-[linear-gradient(180deg,rgba(255,255,255,0.82),rgba(255,248,235,0.72))] shadow-[0_8px_24px_rgba(71,43,19,0.07)] hover:-translate-y-0.5 hover:shadow-[0_14px_32px_rgba(71,43,19,0.12)] cursor-pointer no-underline'
  const cardDisabled =
    'border-[rgba(88,63,39,0.08)] bg-[rgba(255,255,255,0.42)] opacity-60 cursor-not-allowed'
  const cardPrimary =
    'border-[rgba(109,47,20,0.18)] bg-[linear-gradient(180deg,rgba(255,248,237,0.99),rgba(246,232,209,0.96))]'

  if (disabled) {
    return (
      <div className={joinClasses(cardBase, cardDisabled, tone === 'primary' && cardPrimary)}>
        <p className="text-[0.73rem] font-bold uppercase tracking-[0.14em] text-[var(--muted)]">
          {title}
        </p>
        <p className="text-[0.9rem] leading-6 text-[var(--muted)]">{description}</p>
        {disabledReason ? (
          <p className="mt-1 text-[0.78rem] leading-5 text-[var(--muted)]">{disabledReason}</p>
        ) : null}
      </div>
    )
  }

  return (
    <Link
      to={href}
      className={joinClasses(cardBase, cardEnabled, tone === 'primary' && cardPrimary)}
    >
      <p className={joinClasses(
        'text-[0.73rem] font-bold uppercase tracking-[0.14em]',
        tone === 'primary' ? 'text-[var(--brand)]' : 'text-[var(--accent)]',
      )}>
        {title}
      </p>
      <p className="text-[0.9rem] leading-6 text-[var(--text)]">{description}</p>
    </Link>
  )
}

// ─── HomePage ─────────────────────────────────────────────────────────────────

export function HomePage() {
  const { data: bootstrap } = useBootstrapQuery()

  const llmEnabled = bootstrap?.llm_enabled ?? false
  const llmProvider = bootstrap?.llm_provider ?? 'loading…'

  return (
    <div className={pageStackClass}>
      <section className={panelAccentClass}>
        <div className={headingGroupClass}>
          <p className={eyebrowClass}>KiCad PCB Web App</p>
          <h1>Generate KiCad schematics from circuit descriptions.</h1>
          <p className={heroLeadClass}>
            Use the AI Wizard to describe a circuit and generate a KiCad project, or directly provide
            a Circuit IR JSON to generate without an LLM.
          </p>
        </div>
        <div className="mt-1 text-[0.86rem] text-[var(--muted)]">
          Provider: <strong>{llmProvider}</strong>
        </div>
      </section>

      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h2>Workflows</h2>
          <p className="text-[var(--muted)]">Choose how you want to generate a KiCad project.</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <WorkflowCard
            title="AI Wizard"
            description="Describe a circuit in plain language. The wizard drafts a spec, generates Circuit IR, and creates the KiCad project."
            href="/wizard"
            disabled={!llmEnabled}
            disabledReason={
              !llmEnabled
                ? 'LLM provider is not configured. Check Setup for details, or use Circuit IR JSON to generate without an LLM.'
                : undefined
            }
            tone="primary"
          />
          <WorkflowCard
            title="Generate from Circuit IR JSON"
            description="Paste or upload a Circuit IR JSON document to generate a KiCad project directly — no LLM required."
            href="/generate-json"
          />
          <WorkflowCard
            title="Recent Jobs"
            description="Browse previously generated KiCad projects, inspect artifacts, warnings, and build diagnostics."
            href="/jobs"
          />
          <WorkflowCard
            title="Check Setup"
            description="Run the doctor check to verify all required tools and LLM configuration are in place."
            href="/setup"
          />
          <WorkflowCard
            title="Search Symbols"
            description="Search the available KiCad symbol library to find compatible components for your circuit."
            href="/symbols"
          />
        </div>
      </section>
    </div>
  )
}
