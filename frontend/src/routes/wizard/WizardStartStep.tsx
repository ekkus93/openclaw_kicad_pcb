import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { joinClasses, statusBannerToneClass } from '../../utils'
import { StatusPill } from '../../components/StatusPill'
import {
  bannerBaseClass,
  buttonSecondaryClass,
  compactStatusRowClass,
  compactSupportCopyClass,
  eyebrowClass,
  headingGroupClass,
  helpTextClass,
  heroLeadClass,
  pageStackClass,
  panelAccentClass,
  spinnerClass,
  wizardFieldSectionClass,
  wizardFormClass,
  workflowStepItemClass,
  workflowStepListClass,
} from '../../styles/designTokens'
import { WizardComposer } from './WizardComposer'

interface WizardStartStepProps {
  projectName: string
  setProjectName: (v: string) => void
  symbolsDir: string
  setSymbolsDir: (v: string) => void
  message: string
  setMessage: (v: string) => void
  llmEnabled: boolean
  llmProvider: string
  busyMessage: string | null
  errorMessage: string | null
  onDismissError: () => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}

export function WizardStartStep({
  projectName,
  setProjectName,
  symbolsDir,
  setSymbolsDir,
  message,
  setMessage,
  llmEnabled,
  llmProvider,
  busyMessage,
  errorMessage,
  onDismissError,
  onSubmit,
}: WizardStartStepProps) {
  return (
    <div className={pageStackClass}>
      <section className={panelAccentClass}>
        <div className={headingGroupClass}>
          <p className={eyebrowClass}>Wizard</p>
          <h1>Start a circuit session.</h1>
          <p className={heroLeadClass}>Describe the circuit. Review the spec. Validate the IR. Generate the project.</p>
        </div>
        <ol className={workflowStepListClass}>
          <li className={workflowStepItemClass}>1. Describe the circuit.</li>
          <li className={workflowStepItemClass}>2. Approve the drafted spec.</li>
          <li className={workflowStepItemClass}>3. Validate the Circuit IR.</li>
          <li className={workflowStepItemClass}>4. Generate the KiCad project.</li>
        </ol>
        <div className="rounded-[18px] border border-[rgba(88,63,39,0.12)] bg-[rgba(255,255,255,0.5)] p-4">
          <p className="mb-1.5 text-[0.75rem] font-bold uppercase tracking-[0.12em] text-[var(--muted)]">
            Example prompt
          </p>
          <p className="font-[var(--font-mono)] text-[0.85rem] leading-6 text-[var(--text)] whitespace-pre-wrap">{`Make a 555 timer LED blinker.\nSupply: 5V.\nOutput: one LED.\nConstraints: through-hole parts, use NE555, about 1 Hz blink rate.`}</p>
        </div>
        <div className={compactStatusRowClass}>
          <StatusPill tone={llmEnabled ? 'success' : 'neutral'}>
            {llmEnabled ? 'Provider ready' : 'Provider disabled'}
          </StatusPill>
          <span className={compactSupportCopyClass}>{llmProvider}</span>
        </div>

        {errorMessage ? (
          <div role="alert" className={joinClasses(bannerBaseClass, statusBannerToneClass('error'), 'flex-wrap gap-2')}>
            <strong className="flex-1">{errorMessage}</strong>
            <button type="button" className={buttonSecondaryClass} onClick={onDismissError}>
              Dismiss
            </button>
          </div>
        ) : null}
        {busyMessage ? (
          <div role="status" aria-live="polite" className={joinClasses(bannerBaseClass, statusBannerToneClass('active'))}>
            <span className={spinnerClass} aria-hidden="true"></span>
            <strong>{busyMessage}</strong>
          </div>
        ) : null}

        <form className={wizardFormClass} onSubmit={onSubmit}>
          <div className={wizardFieldSectionClass}>
            <label>
              <span>Project Name</span>
              <input
                placeholder="e.g. LED Blinker"
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
            <p className={helpTextClass}>
              Symbols Directory: absolute path to a KiCad symbol library directory.
              Leave blank to use the built-in symbols.
            </p>
          </div>
          <div className={wizardFieldSectionClass}>
            <WizardComposer
              disabled={Boolean(busyMessage)}
              label="Circuit Request"
              placeholder="Goal, rails, inputs, outputs, constraints."
              value={message}
              onChange={setMessage}
              submitLabel="Start Session"
              submitDisabled={!llmEnabled || !message.trim() || Boolean(busyMessage)}
            />
          </div>
          {!llmEnabled ? (
            <p className={helpTextClass}>
              No LLM provider is configured. Set a provider in{' '}
              <code className="rounded bg-[rgba(88,63,39,0.08)] px-1 py-0.5">kicad_pcb_web.toml</code>{' '}
              to enable the wizard.
            </p>
          ) : null}
        </form>
        {!llmEnabled ? (
          <div className="mt-4 rounded-[18px] border border-[rgba(88,63,39,0.12)] bg-[rgba(255,255,255,0.5)] p-4">
            <p className="mb-3 text-[0.82rem] font-semibold text-[var(--muted)]">
              You can still use these workflows without an LLM provider:
            </p>
            <div className="flex flex-wrap gap-2">
              <Link className={buttonSecondaryClass} to="/generate-json">
                Generate from Circuit IR JSON
              </Link>
              <Link className={buttonSecondaryClass} to="/jobs">
                View Recent Jobs
              </Link>
              <Link className={buttonSecondaryClass} to="/setup">
                Check Setup
              </Link>
            </div>
          </div>
        ) : null}
      </section>
    </div>
  )
}
