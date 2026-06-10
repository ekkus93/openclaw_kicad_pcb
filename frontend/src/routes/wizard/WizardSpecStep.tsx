import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { joinClasses, statusBannerToneClass } from '../../utils'
import { StatusPill } from '../../components/StatusPill'
import type { CircuitBlockSpec, CircuitPortSpec, CircuitRailSpec, WizardSessionDetail } from '../../types'
import {
  bannerBaseClass,
  blockCardClass,
  blockGridClass,
  buttonSecondaryClass,
  compactListItemClass,
  compactStatusRowClass,
  emptyCopyClass,
  eyebrowClass,
  headingGroupClass,
  helpTextClass,
  heroLeadClass,
  mutedCopyClass,
  panelAccentClass,
  panelSoftClass,
  recordListClass,
  wizardActionRowClass,
  wizardFieldSectionClass,
  wizardFormClass,
  wizardPrimaryButtonClass,
} from '../../styles/designTokens'
import { statusLabel, statusTone } from './wizardStepLogic'
import { WizardComposer } from './WizardComposer'

// ─── Local list components ────────────────────────────────────────────────────

function LabelList({ items }: { items: string[] }) {
  if (!items.length) return <p className={emptyCopyClass}>None recorded.</p>
  return (
    <ul className="m-0 grid list-disc gap-1.5 pl-5">
      {items.map((item) => (
        <li key={item} className="text-[0.95rem] leading-6 text-[var(--text)]">{item}</li>
      ))}
    </ul>
  )
}

function PortList({ ports }: { ports: CircuitPortSpec[] }) {
  if (!ports.length) return <p className={emptyCopyClass}>None recorded.</p>
  return (
    <ul className={recordListClass}>
      {ports.map((port) => (
        <li key={`${port.name}-${port.description ?? ''}`} className={compactListItemClass}>
          <strong>{port.name}</strong>
          <span className={mutedCopyClass}>{port.description ?? port.signal_type ?? 'No detail'}</span>
        </li>
      ))}
    </ul>
  )
}

function RailList({ rails }: { rails: CircuitRailSpec[] }) {
  if (!rails.length) return <p className={emptyCopyClass}>None recorded.</p>
  return (
    <ul className={recordListClass}>
      {rails.map((rail) => (
        <li key={`${rail.name}-${rail.nominal_voltage ?? ''}`} className={compactListItemClass}>
          <strong>{rail.name}</strong>
          <span className={mutedCopyClass}>{rail.nominal_voltage ?? rail.description ?? 'No detail'}</span>
        </li>
      ))}
    </ul>
  )
}

function BlockList({ blocks }: { blocks: CircuitBlockSpec[] }) {
  if (!blocks.length) return <p className={emptyCopyClass}>None recorded.</p>
  return (
    <div className={blockGridClass}>
      {blocks.map((block) => (
        <article key={`${block.name}-${block.block_type}`} className={blockCardClass}>
          <strong>{block.name}</strong>
          <span className={mutedCopyClass}>{block.block_type.replaceAll('_', ' ')}</span>
          <p className={mutedCopyClass}>{block.summary}</p>
        </article>
      ))}
    </div>
  )
}

// ─── WizardSpecStep ───────────────────────────────────────────────────────────

interface WizardSpecStepProps {
  session: WizardSessionDetail
  sessionId: string
  projectLabel: string | null
  checkpoint: { title: string; detail: string }
  llmEnabled: boolean
  busyMessage: string | null
  message: string
  setMessage: (v: string) => void
  canApproveSpec: boolean
  canGenerateIr: boolean
  hasUnspecifiedCustomBlocks: boolean
  onSendMessage: (event: FormEvent<HTMLFormElement>) => void
  onApproveSpec: () => void
}

export function WizardSpecStep({
  session,
  sessionId,
  projectLabel,
  checkpoint,
  llmEnabled,
  busyMessage,
  message,
  setMessage,
  canApproveSpec,
  canGenerateIr,
  hasUnspecifiedCustomBlocks,
  onSendMessage,
  onApproveSpec,
}: WizardSpecStepProps) {
  if (!session.spec) return null

  const underspecifiedBlocks = session.spec.blocks.filter(
    (b) => b.block_type === 'custom' && b.required_components.length === 0,
  )

  return (
    <>
      <section className={panelAccentClass}>
        <div className={headingGroupClass}>
          <p className={eyebrowClass}>
            Step 2 of 4{projectLabel ? ` — ${projectLabel}` : ''}
          </p>
          <h1>Review Spec</h1>
          <p className={heroLeadClass}>{checkpoint.title}</p>
          <p className="text-[0.9rem] leading-6 text-[rgba(71,43,19,0.65)]">{checkpoint.detail}</p>
        </div>
        <div className={compactStatusRowClass}>
          <StatusPill tone={statusTone(session.status)}>
            {statusLabel(session.status)}
          </StatusPill>
        </div>
      </section>

      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h2>Circuit Specification</h2>
        </div>
        {(session.open_questions.length > 0 || session.unsupported_reasons.length > 0) ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {session.open_questions.length > 0 ? (
              <div className={joinClasses(bannerBaseClass, statusBannerToneClass('warning'), 'flex-col items-start gap-2')}>
                <strong className="text-[0.8rem] font-bold uppercase tracking-[0.1em]">Open Questions</strong>
                <LabelList items={session.open_questions} />
              </div>
            ) : null}
            {session.unsupported_reasons.length > 0 ? (
              <div className={joinClasses(bannerBaseClass, statusBannerToneClass('error'), 'flex-col items-start gap-2')}>
                <strong className="text-[0.8rem] font-bold uppercase tracking-[0.1em]">Unsupported</strong>
                <LabelList items={session.unsupported_reasons} />
              </div>
            ) : null}
          </div>
        ) : null}

        {underspecifiedBlocks.length > 0 ? (
          <div className={joinClasses(bannerBaseClass, statusBannerToneClass('warning'), 'flex-col items-start gap-2')}>
            <strong className="text-[0.8rem] font-bold uppercase tracking-[0.1em]">
              Underspecified blocks — action required before approving
            </strong>
            <p className="text-sm leading-6">
              {underspecifiedBlocks.map((b) => b.name).join(', ')}{' '}
              {underspecifiedBlocks.length === 1
                ? 'is marked "custom" with no named component.'
                : 'are marked "custom" with no named component.'}{' '}
              The Circuit IR generator will have to invent a circuit for{' '}
              {underspecifiedBlocks.length === 1 ? 'it' : 'them'}, which almost always fails.
              Go back and tell the wizard which specific component (IC part number) should
              implement each of these blocks before approving.
            </p>
          </div>
        ) : null}

        <div className="rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.5)] p-4">
          <p className="mb-1 text-[0.75rem] font-bold uppercase tracking-[0.13em] text-[var(--brand)]">Purpose</p>
          <p className="text-[1rem] leading-7">{session.spec.purpose}</p>
        </div>

        <div>
          <p className="mb-2 text-[0.75rem] font-bold uppercase tracking-[0.13em] text-[var(--muted)]">Interface</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.5)] p-4">
              <p className="mb-2 text-[0.82rem] font-semibold text-[var(--text)]">Inputs</p>
              <PortList ports={session.spec.inputs} />
            </div>
            <div className="rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.5)] p-4">
              <p className="mb-2 text-[0.82rem] font-semibold text-[var(--text)]">Outputs</p>
              <PortList ports={session.spec.outputs} />
            </div>
            <div className="rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.5)] p-4">
              <p className="mb-2 text-[0.82rem] font-semibold text-[var(--text)]">Supply Rails</p>
              <RailList rails={session.spec.supply_rails} />
            </div>
          </div>
        </div>

        <div>
          <p className="mb-2 text-[0.75rem] font-bold uppercase tracking-[0.13em] text-[var(--muted)]">Requirements</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.5)] p-4">
              <p className="mb-2 text-[0.82rem] font-semibold text-[var(--text)]">Acceptance Criteria</p>
              <LabelList items={session.spec.acceptance_criteria} />
            </div>
            <div className="rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.5)] p-4">
              <p className="mb-2 text-[0.82rem] font-semibold text-[var(--text)]">Constraints</p>
              <LabelList items={session.spec.constraints} />
            </div>
          </div>
        </div>

        {session.spec.blocks.length > 0 ? (
          <div>
            <p className="mb-2 text-[0.75rem] font-bold uppercase tracking-[0.13em] text-[var(--muted)]">Architecture</p>
            <BlockList blocks={session.spec.blocks} />
          </div>
        ) : null}
      </section>

      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h2>Revise or Approve</h2>
          <p className={mutedCopyClass}>
            Approve the spec to unlock Circuit IR generation, or send revision notes to refine it first.
          </p>
        </div>
        <form className={wizardFormClass} onSubmit={onSendMessage}>
          <div className={wizardFieldSectionClass}>
            <WizardComposer
              disabled={Boolean(busyMessage)}
              label="Revision Note"
              placeholder="List only the specific spec changes you want."
              value={message}
              onChange={setMessage}
              submitLabel="Send Changes"
              submitDisabled={!llmEnabled || !message.trim() || Boolean(busyMessage)}
            />
            {!llmEnabled ? (
              <p className={helpTextClass}>
                LLM provider is not available — revision requires a configured provider.
              </p>
            ) : null}
          </div>
          <div className={wizardActionRowClass}>
            <button
              type="button"
              className={wizardPrimaryButtonClass}
              disabled={!canApproveSpec || Boolean(busyMessage)}
              onClick={onApproveSpec}
            >
              Approve Spec
            </button>
          </div>
          {!canApproveSpec ? (
            <p className={helpTextClass}>
              {session.open_questions.length > 0
                ? 'Approve Spec is locked — resolve the open questions above first.'
                : session.unsupported_reasons.length > 0
                  ? 'Approve Spec is locked — resolve the unsupported reasons above first.'
                  : hasUnspecifiedCustomBlocks
                    ? 'Approve Spec is locked — go back and ask the wizard to name a specific component for each custom block.'
                    : session.spec_approved
                      ? 'Spec is already approved.'
                      : 'Spec must be generated before it can be approved.'}
            </p>
          ) : null}
        </form>
      </section>

      <div className="flex items-center justify-between border-t border-[var(--border)] pt-4">
        <Link className={buttonSecondaryClass} to={`/wizard/${sessionId}/describe`}>
          ← Back to Describe
        </Link>
        {canGenerateIr ? (
          <Link className={wizardPrimaryButtonClass} to={`/wizard/${sessionId}/ir`}>
            Continue to Circuit IR →
          </Link>
        ) : null}
      </div>
    </>
  )
}
