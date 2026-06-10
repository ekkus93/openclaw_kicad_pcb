import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { joinClasses } from '../../utils'
import { StatusPill } from '../../components/StatusPill'
import type { WizardSessionDetail } from '../../types'
import {
  buttonSecondaryClass,
  compactStatusRowClass,
  compactSupportCopyClass,
  eyebrowClass,
  headingGroupClass,
  helpTextClass,
  heroLeadClass,
  mutedCopyClass,
  panelAccentClass,
  panelSoftClass,
  transcriptBodyClass,
  transcriptEntryClass,
  transcriptListClass,
  transcriptMetaClass,
  wizardFieldGridClass,
  wizardFieldSectionClass,
  wizardFormClass,
  wizardPrimaryButtonClass,
} from '../../styles/designTokens'
import { statusLabel, statusTone, wizardStepUnlocked } from './wizardStepLogic'
import { WizardComposer } from './WizardComposer'

// ─── TranscriptEntryCard ──────────────────────────────────────────────────────

function TranscriptEntryCard({
  content,
  index,
  role,
}: {
  content: string
  index: number
  role: string
}) {
  const isUser = role === 'user'
  return (
    <article
      className={joinClasses(
        transcriptEntryClass,
        isUser
          ? 'ml-auto border-[rgba(22,93,143,0.14)] bg-[linear-gradient(180deg,rgba(236,245,243,0.96),rgba(226,240,238,0.88))]'
          : 'mr-auto border-[rgba(161,69,26,0.14)] bg-[linear-gradient(180deg,rgba(255,245,228,0.94),rgba(251,239,217,0.9))]',
      )}
    >
      <div className={transcriptMetaClass}>
        <span
          className={joinClasses(
            'inline-flex items-center rounded-full px-2.5 py-1',
            isUser ? 'bg-[rgba(22,93,143,0.1)] text-[#0d4c74]' : 'bg-[rgba(161,69,26,0.1)] text-[var(--brand-deep)]',
          )}
        >
          {isUser ? 'You' : 'Wizard'}
        </span>
        <span>Turn {index + 1}</span>
      </div>
      <p className={transcriptBodyClass}>{content}</p>
    </article>
  )
}

// ─── WizardDescribeStep ───────────────────────────────────────────────────────

interface WizardDescribeStepProps {
  session: WizardSessionDetail
  sessionId: string
  projectLabel: string | null
  checkpoint: { title: string; detail: string }
  llmProvider: string
  llmEnabled: boolean
  busyMessage: string | null
  message: string
  setMessage: (v: string) => void
  projectName: string
  setProjectName: (v: string) => void
  symbolsDir: string
  setSymbolsDir: (v: string) => void
  metaExpanded: boolean
  setMetaExpanded: (updater: boolean | ((prev: boolean) => boolean)) => void
  onSendMessage: (event: FormEvent<HTMLFormElement>) => void
}

export function WizardDescribeStep({
  session,
  sessionId,
  projectLabel,
  checkpoint,
  llmProvider,
  llmEnabled,
  busyMessage,
  message,
  setMessage,
  projectName,
  setProjectName,
  symbolsDir,
  setSymbolsDir,
  metaExpanded,
  setMetaExpanded,
  onSendMessage,
}: WizardDescribeStepProps) {
  return (
    <>
      <section className={panelAccentClass}>
        <div className={headingGroupClass}>
          <p className={eyebrowClass}>
            Step 1 of 4{projectLabel ? ` — ${projectLabel}` : ''}
          </p>
          <h1>Describe Circuit</h1>
          <p className={heroLeadClass}>{checkpoint.title}</p>
          <p className={compactSupportCopyClass}>{checkpoint.detail}</p>
        </div>
        <div className={compactStatusRowClass}>
          <StatusPill tone={statusTone(session.status)}>
            {statusLabel(session.status)}
          </StatusPill>
          <span className={compactSupportCopyClass}>
            {session.llm_provider ?? llmProvider}
          </span>
        </div>
      </section>

      {session.messages.length > 0 ? (
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Conversation</h2>
            <p className={mutedCopyClass}>
              Your notes stay separated from the wizard replies so each turn is easier to scan.
            </p>
          </div>
          <div className={transcriptListClass}>
            {session.messages.map((entry, index) => (
              <TranscriptEntryCard
                key={`${entry.role}-${index}`}
                content={entry.content}
                index={index}
                role={entry.role}
              />
            ))}
          </div>
        </section>
      ) : null}

      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h2>{session.messages.length > 0 ? 'Continue the conversation' : 'Circuit Request'}</h2>
          <p className={mutedCopyClass}>
            {session.messages.length > 0
              ? "Add more detail, answer the wizard's questions, or refine the brief."
              : 'Keep refining the prompt until the spec is ready for review.'}
          </p>
        </div>
        <form className={wizardFormClass} onSubmit={onSendMessage}>
          <div className={wizardFieldSectionClass}>
            <WizardComposer
              disabled={Boolean(busyMessage)}
              label="Your message"
              placeholder="Clarify only the missing or changed details."
              value={message}
              onChange={setMessage}
              submitLabel="Send"
              submitDisabled={!llmEnabled || !message.trim() || Boolean(busyMessage)}
            />
          </div>
          <div className="flex">
            <button
              type="button"
              className="flex items-center gap-1.5 rounded-full px-3 py-2 text-[0.82rem] font-medium text-[var(--accent)] transition-colors hover:bg-[rgba(24,75,69,0.06)]"
              onClick={() => setMetaExpanded((v) => !v)}
            >
              <span
                className={joinClasses(
                  'text-[0.9rem] leading-none transition-transform duration-150',
                  metaExpanded ? 'rotate-90' : '',
                )}
                aria-hidden="true"
              >
                ▸
              </span>
              {metaExpanded ? 'Hide metadata' : 'Edit metadata'}
            </button>
          </div>
          {metaExpanded ? (
            <div className={wizardFieldSectionClass}>
              <div className={wizardFieldGridClass}>
                <label>
                  <span>Project Name</span>
                  <input
                    value={projectName}
                    onChange={(event) => setProjectName(event.target.value)}
                  />
                </label>
                <label>
                  <span>Symbols Directory{' '}
                    <span className="font-normal text-[var(--muted)]">(optional)</span>
                  </span>
                  <input
                    placeholder="Leave blank to use built-in symbols"
                    value={symbolsDir}
                    onChange={(event) => setSymbolsDir(event.target.value)}
                  />
                </label>
              </div>
              <p className={helpTextClass}>
                Adjust metadata only when the session context actually changed.
              </p>
            </div>
          ) : null}
        </form>
      </section>

      <div className="flex items-center justify-between border-t border-[var(--border)] pt-4">
        <Link className={buttonSecondaryClass} to="/wizard">
          Start New Session
        </Link>
        {wizardStepUnlocked(session, 'spec') ? (
          <Link className={wizardPrimaryButtonClass} to={`/wizard/${sessionId}/spec`}>
            Continue to Spec Review →
          </Link>
        ) : null}
      </div>
    </>
  )
}
