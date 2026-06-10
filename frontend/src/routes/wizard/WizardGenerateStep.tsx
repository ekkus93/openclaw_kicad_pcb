import { Link } from 'react-router-dom'

import { formatDate, joinClasses, statusBannerToneClass } from '../../utils'
import { StatusPill } from '../../components/StatusPill'
import { WarningsPanel } from '../../components/WarningCard'
import { BuildSummaryPanel } from '../../components/DisclosurePanel'
import type { DiagnosticsPayload } from '../../components/DisclosurePanel'
import type { JobDetail, WizardSessionDetail } from '../../types'
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
  heroLeadClass,
  mutedCopyClass,
  panelAccentClass,
  panelSoftClass,
  stackColumnClass,
} from '../../styles/designTokens'
import { asRecord, displaySymbolsDir, statusLabel, statusTone } from './wizardStepLogic'

// ─── JobSummaryPanel ──────────────────────────────────────────────────────────

function JobSummaryPanel({ job, sessionId }: { job: JobDetail; sessionId?: string }) {
  const result = asRecord(job.result)
  const warnings = Array.isArray(result.warnings) ? result.warnings : []
  const diagnostics = result.generated_schematic_diagnostics ?? null
  const jobPath = sessionId ? `/jobs/${job.id}?from=${encodeURIComponent(sessionId)}` : `/jobs/${job.id}`
  const hasPreview = job.artifacts.includes('schematic_preview.png')
  const downloadArtifacts = job.artifacts.filter((a) => a !== 'schematic_preview.png' && !a.endsWith('.svg'))
  return (
    <div className={stackColumnClass}>
      {hasPreview ? (
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>Schematic Preview</h2>
            <p className={mutedCopyClass}>
              Generated from{' '}
              <code className="rounded bg-[rgba(88,63,39,0.08)] px-1 py-0.5 text-[0.85rem]">
                OpenClaw_Managed.kicad_sch
              </code>
            </p>
          </div>
          <img
            src={`/api/jobs/${job.id}/artifacts/schematic_preview.png`}
            alt="Generated schematic preview"
            className="w-full rounded-[18px] border border-[rgba(88,63,39,0.1)]"
            style={{ background: '#fff' }}
          />
        </section>
      ) : null}
      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h2>Latest Job</h2>
        </div>
        <dl className={detailListGridClass}>
          <dt className={mutedCopyClass}>Project</dt>
          <dd>{job.project_name}</dd>
          <dt className={mutedCopyClass}>Status</dt>
          <dd>
            <StatusPill tone={statusTone(job.status)}>{statusLabel(job.status)}</StatusPill>
          </dd>
          <dt className={mutedCopyClass}>Updated</dt>
          <dd>{formatDate(job.updated_at)}</dd>
        </dl>
        <div className={buttonRowClass}>
          <Link className={buttonSecondaryClass} to={jobPath}>
            Open Job Detail
          </Link>
          {downloadArtifacts.map((artifact) => (
            <a key={artifact} className={buttonSecondaryClass} href={`/api/jobs/${job.id}/artifacts/${artifact}`}>
              {artifact}
            </a>
          ))}
        </div>
        {job.artifacts.length === 0 ? (
          <p className={compactSupportCopyClass}>No artifacts — generation did not complete.</p>
        ) : null}
      </section>
      <WarningsPanel warnings={warnings} />
      {diagnostics ? <BuildSummaryPanel diagnostics={diagnostics as DiagnosticsPayload} /> : null}
    </div>
  )
}

// ─── WizardGenerateStep ───────────────────────────────────────────────────────

interface WizardGenerateStepProps {
  session: WizardSessionDetail
  sessionId: string
  projectLabel: string | null
  checkpoint: { title: string; detail: string }
  busyMessage: string | null
  canGenerateProject: boolean
  visibleLatestJob: JobDetail | null
  confirmRegenerate: boolean
  setConfirmRegenerate: (v: boolean) => void
  onGenerateProject: () => void
}

export function WizardGenerateStep({
  session,
  sessionId,
  projectLabel,
  checkpoint,
  busyMessage,
  canGenerateProject,
  visibleLatestJob,
  confirmRegenerate,
  setConfirmRegenerate,
  onGenerateProject,
}: WizardGenerateStepProps) {
  return (
    <>
      <section className={panelAccentClass}>
        <div className={headingGroupClass}>
          <p className={eyebrowClass}>
            Step 4 of 4{projectLabel ? ` — ${projectLabel}` : ''}
          </p>
          <h1>Generate Project</h1>
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
          <h2>Project settings</h2>
        </div>
        <dl className={detailListGridClass}>
          <dt className={mutedCopyClass}>Project name</dt>
          <dd>{session.project_name || 'Not set'}</dd>
          <dt className={mutedCopyClass}>Symbols directory</dt>
          <dd>{session.symbols_dir ? displaySymbolsDir(session.symbols_dir) : 'Not set'}</dd>
        </dl>
        <div className={buttonRowClass}>
          {busyMessage ? (
            <span aria-disabled="true" className="text-[0.84rem] text-[var(--muted)]">
              Edit project details (not available while action is running)
            </span>
          ) : (
            <Link className={buttonSecondaryClass} to={`/wizard/${sessionId}/describe`}>
              Edit project details
            </Link>
          )}
        </div>
      </section>

      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h2>Generate KiCad Project</h2>
          <p className={mutedCopyClass}>
            Use the validated Circuit IR as the handoff into the deterministic generation pipeline.
          </p>
        </div>
        {confirmRegenerate ? (
          <div aria-live="polite" className={joinClasses(bannerBaseClass, statusBannerToneClass('warning'), 'flex-col items-start gap-3')}>
            <strong>This will replace the current generation result.</strong>
            <div className={buttonRowClass}>
              <button
                type="button"
                className={buttonDangerClass}
                disabled={Boolean(busyMessage)}
                onClick={onGenerateProject}
              >
                Confirm — Generate Again
              </button>
              <button
                type="button"
                className={buttonSecondaryClass}
                onClick={() => setConfirmRegenerate(false)}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className={buttonRowClass}>
            <button
              type="button"
              className={visibleLatestJob?.status === 'succeeded' ? buttonDangerClass : buttonPrimaryClass}
              disabled={!canGenerateProject || Boolean(busyMessage)}
              onClick={onGenerateProject}
            >
              {!visibleLatestJob
                ? 'Generate Project'
                : visibleLatestJob.status === 'failed'
                  ? 'Retry Generation'
                  : 'Generate Again'}
            </button>
          </div>
        )}
      </section>

      {visibleLatestJob ? (
        <>
          {visibleLatestJob.status === 'failed' ? (() => {
            const errMsg = String(asRecord(visibleLatestJob.error).message ?? '')
            const isIrProblem =
              asRecord(visibleLatestJob.error).code === 'IR_SEMANTIC_INVALID' ||
              errMsg.includes('LibraryName:PartName') ||
              errMsg.includes('unqualified') ||
              errMsg.includes('pin') ||
              errMsg.includes('symbol')
            return (
              <div role="alert" className={joinClasses(bannerBaseClass, statusBannerToneClass('error'), 'flex-col items-start gap-2')}>
                <strong>
                  Generation failed{errMsg ? ` — ${errMsg}` : ''}.
                </strong>
                {isIrProblem ? (
                  <p className="text-sm leading-6">
                    The Circuit IR has an error that must be fixed before generating.{' '}
                    <Link className="font-semibold underline" to={`/wizard/${sessionId}/ir`}>
                      Go back to the Circuit IR step
                    </Link>
                    , clear the IR, and regenerate it.
                  </p>
                ) : (
                  <p className="text-sm leading-6">Open the job for full details.</p>
                )}
              </div>
            )
          })() : null}
          <JobSummaryPanel job={visibleLatestJob} sessionId={sessionId} />
        </>
      ) : (
        <p className={emptyCopyClass}>No generation job linked yet.</p>
      )}

      <div className="flex justify-start border-t border-[var(--border)] pt-4">
        <Link className={buttonSecondaryClass} to={`/wizard/${sessionId}/ir`}>
          ← Back to Circuit IR
        </Link>
      </div>
    </>
  )
}
