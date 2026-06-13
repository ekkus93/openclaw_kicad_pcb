import React from 'react'
import { Link } from 'react-router-dom'

import { joinClasses, statusBannerToneClass } from '../../utils'
import { StatusPill } from '../../components/StatusPill'
import { WarningsPanel } from '../../components/WarningCard'
import { DisclosurePanel } from '../../components/DisclosurePanel'
import type { WizardSessionDetail } from '../../types'
import {
  bannerBaseClass,
  buttonDangerClass,
  buttonPrimaryClass,
  buttonRowClass,
  buttonSecondaryClass,
  compactStatusRowClass,
  compactSupportCopyClass,
  detailListGridClass,
  emptyCopyClass,
  eyebrowClass,
  headingGroupClass,
  helpTextClass,
  heroLeadClass,
  mutedCopyClass,
  panelAccentClass,
  panelSoftClass,
  wizardPrimaryButtonClass,
} from '../../styles/designTokens'
import { displaySymbolsDir, statusLabel, statusTone } from './wizardStepLogic'

const ReactJsonView = React.lazy(() => import('@microlink/react-json-view'))

function JsonTreeViewer({ value }: { value: unknown }) {
  if (!value || typeof value !== 'object') return null
  return (
    <div className="overflow-auto rounded-[18px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,253,248,0.95)] p-4 text-[0.85rem]">
      <React.Suspense fallback={<p className="text-[var(--muted)] text-sm">Loading viewer…</p>}>
        <ReactJsonView
          src={value as object}
          theme="rjv-default"
          iconStyle="triangle"
          collapsed={2}
          collapseStringsAfterLength={80}
          displayDataTypes={false}
          displayObjectSize={true}
          enableClipboard={true}
          style={{
            backgroundColor: 'transparent',
            fontFamily: 'var(--font-mono)',
            fontSize: '0.85rem',
            lineHeight: '1.7',
          }}
        />
      </React.Suspense>
    </div>
  )
}

interface WizardIrStepProps {
  session: WizardSessionDetail
  sessionId: string
  projectLabel: string | null
  checkpoint: { title: string; detail: string }
  llmEnabled: boolean
  busyMessage: string | null
  canGenerateIr: boolean
  canGenerateProject: boolean
  onGenerateIr: () => void
  onClearIr: () => void
}

export function WizardIrStep({
  session,
  sessionId,
  projectLabel,
  checkpoint,
  llmEnabled,
  busyMessage,
  canGenerateIr,
  canGenerateProject,
  onGenerateIr,
  onClearIr,
}: WizardIrStepProps) {
  return (
    <>
      <section className={panelAccentClass}>
        <div className={headingGroupClass}>
          <p className={eyebrowClass}>
            Step 3 of 4{projectLabel ? ` — ${projectLabel}` : ''}
          </p>
          <h1>Circuit IR</h1>
          <p className={heroLeadClass}>{checkpoint.title}</p>
          <p className={compactSupportCopyClass}>{checkpoint.detail}</p>
        </div>
        <div className={compactStatusRowClass}>
          <StatusPill tone={statusTone(session.status)}>
            {statusLabel(session.status)}
          </StatusPill>
        </div>
      </section>

      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h2>
            {session.ir_validation?.valid ? 'Circuit IR — Valid' : 'Generate Circuit IR'}
          </h2>
          <p className={mutedCopyClass}>
            {session.ir_validation?.valid
              ? 'The IR validates cleanly. Review the counts below, then continue to project generation.'
              : 'Generate the IR from the approved spec and inspect validation before creating a project.'}
          </p>
        </div>
        {session.status === 'ir_needs_repair' && !busyMessage ? (
          <div className={joinClasses(bannerBaseClass, statusBannerToneClass('warning'))}>
            <strong>
              The generated IR has errors that could not be auto-fixed. Use "Repair Circuit IR" to
              try again, or go back to Spec and clarify the circuit description.
            </strong>
          </div>
        ) : null}

        {session.ir_validation ? (
          <dl className={detailListGridClass}>
            <dt className={mutedCopyClass}>Valid</dt>
            <dd>{session.ir_validation.valid ? 'Yes' : 'No'}</dd>
            <dt className={mutedCopyClass}>Auto-fixed</dt>
            <dd>{session.ir_validation.auto_fixed ? 'Yes' : 'No'}</dd>
            <dt className={mutedCopyClass}>Components</dt>
            <dd>{session.ir_validation.component_count}</dd>
            <dt className={mutedCopyClass}>Nets</dt>
            <dd>{session.ir_validation.net_count}</dd>
            <dt className={mutedCopyClass}>Symbols</dt>
            <dd>
              {session.ir_validation.symbols_dirs_used.map(displaySymbolsDir).join(', ') || 'None'}
            </dd>
          </dl>
        ) : (
          <p className={emptyCopyClass}>No Circuit IR draft yet.</p>
        )}
        {session.ir_validation?.error_message ? (
          <div className={joinClasses(bannerBaseClass, statusBannerToneClass('warning'))}>
            <strong>{session.ir_validation.error_message}</strong>
          </div>
        ) : null}
        {!llmEnabled ? (
          <p className={helpTextClass}>
            LLM provider is not available — IR generation requires a configured provider.
          </p>
        ) : null}
        <div className={buttonRowClass}>
          {session.ir_validation?.valid ? (
            <button
              type="button"
              className={buttonDangerClass}
              disabled={!canGenerateIr || !llmEnabled || Boolean(busyMessage)}
              onClick={onGenerateIr}
            >
              Regenerate Circuit IR
            </button>
          ) : (
            <button
              type="button"
              className={buttonPrimaryClass}
              disabled={!canGenerateIr || !llmEnabled || Boolean(busyMessage)}
              onClick={onGenerateIr}
            >
              {session.status === 'ir_needs_repair' ? 'Repair Circuit IR' : 'Generate Circuit IR'}
            </button>
          )}
          {session.ir_json ? (
            <button
              type="button"
              className={buttonDangerClass}
              disabled={Boolean(busyMessage)}
              onClick={onClearIr}
            >
              Clear Circuit IR
            </button>
          ) : null}
        </div>
      </section>

      {session.ir_validation?.warnings.length ? (
        <WarningsPanel warnings={session.ir_validation.warnings} />
      ) : null}
      {session.ir_json ? (
        <DisclosurePanel
          title={`Raw Circuit IR JSON — ${session.ir_validation?.component_count ?? '?'} components, ${session.ir_validation?.net_count ?? '?'} nets`}
          defaultOpen={session.ir_validation?.valid === false}
        >
          <JsonTreeViewer value={session.ir_json} />
        </DisclosurePanel>
      ) : null}

      <div className="flex items-center justify-between border-t border-[var(--border)] pt-4">
        <Link className={buttonSecondaryClass} to={`/wizard/${sessionId}/spec`}>
          ← Back to Spec
        </Link>
        {canGenerateProject ? (
          <Link className={wizardPrimaryButtonClass} to={`/wizard/${sessionId}/generate`}>
            Continue to Generate →
          </Link>
        ) : null}
      </div>
    </>
  )
}
