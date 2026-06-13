import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'

import { joinClasses, statusBannerToneClass } from '../utils'
import {
  bannerBaseClass,
  buttonPrimaryClass,
  buttonRowClass,
  buttonSecondaryClass,
  headingGroupClass,
  mutedCopyClass,
  pageStackClass,
  panelSoftClass,
  spinnerClass,
} from '../styles/designTokens'
import { WIZARD_STEP_META, normalizeWizardStep } from './wizard/wizardStepLogic'
import { useWizardController } from './wizard/useWizardController'
import { WizardBreadcrumb } from './wizard/WizardBreadcrumb'
import { WizardStartStep } from './wizard/WizardStartStep'
import { WizardDescribeStep } from './wizard/WizardDescribeStep'
import { WizardSpecStep } from './wizard/WizardSpecStep'
import { WizardIrStep } from './wizard/WizardIrStep'
import { WizardGenerateStep } from './wizard/WizardGenerateStep'

function NotFoundScreen({ heading, message }: { heading: string; message?: string }) {
  return (
    <div className={pageStackClass}>
      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h1>{heading}</h1>
          {message ? <p className={mutedCopyClass}>{message}</p> : null}
        </div>
        <div className={buttonRowClass}>
          <Link className={buttonPrimaryClass} to="/wizard">
            Start New Session
          </Link>
        </div>
      </section>
    </div>
  )
}

export function WizardPage() {
  const { sessionId, step } = useParams<{ sessionId?: string; step?: string }>()
  const routeStep = normalizeWizardStep(step)

  const ctrl = useWizardController(sessionId, routeStep)
  const { session, currentStep } = ctrl

  useEffect(() => {
    const base = 'KiCad PCB Web App'
    if (!sessionId) {
      document.title = `New Session — ${base}`
      return
    }
    if (session) {
      const projectLabel = session.project_name ?? session.spec?.project_name ?? null
      const stepLabel = WIZARD_STEP_META[currentStep].label
      document.title = projectLabel
        ? `${stepLabel} — ${projectLabel} — ${base}`
        : `${stepLabel} — ${base}`
    }
  }, [currentStep, session, sessionId])

  // ── Start page (no session) ──────────────────────────────────────────────────
  if (!sessionId) {
    return (
      <WizardStartStep
        projectName={ctrl.projectName}
        setProjectName={ctrl.setProjectName}
        symbolsDir={ctrl.symbolsDir}
        setSymbolsDir={ctrl.setSymbolsDir}
        message={ctrl.message}
        setMessage={ctrl.setMessage}
        llmEnabled={ctrl.llmEnabled}
        llmProvider={ctrl.llmProvider}
        busyMessage={ctrl.busyMessage}
        errorMessage={ctrl.errorMessage}
        onDismissError={ctrl.resetAll}
        onSubmit={(e) => void ctrl.handleCreateSession(e)}
      />
    )
  }

  // ── Loading / error ──────────────────────────────────────────────────────────
  if (ctrl.loading) {
    return (
      <div role="status" aria-live="polite" className={joinClasses(bannerBaseClass, statusBannerToneClass('active'), 'mt-4')}>
        <span className={spinnerClass} aria-hidden="true"></span>
        <strong>Loading wizard session…</strong>
      </div>
    )
  }

  if (!session) {
    return (
      <NotFoundScreen
        heading="Session not found"
        message={ctrl.sessionError instanceof Error ? ctrl.sessionError.message : 'This wizard session does not exist or could not be loaded.'}
      />
    )
  }

  return (
    <div className={pageStackClass}>
      <WizardBreadcrumb currentStep={currentStep} session={session} sessionId={session.id} />

      {ctrl.busyMessage ? (
        <div role="status" aria-live="polite" className={joinClasses(bannerBaseClass, statusBannerToneClass('active'))}>
          <span className={spinnerClass} aria-hidden="true"></span>
          <strong>{ctrl.busyMessage}</strong>
        </div>
      ) : null}
      {ctrl.errorMessage ? (
        <div role="alert" className={joinClasses(bannerBaseClass, statusBannerToneClass('error'), 'flex-wrap gap-2')}>
          <strong className="flex-1 min-w-0">{ctrl.errorMessage}</strong>
          {ctrl.generateIrMutationHasError && !ctrl.irRepairWarning ? (
            <button
              type="button"
              className={buttonSecondaryClass}
              onClick={() => void ctrl.handleGenerateIr()}
            >
              Try again
            </button>
          ) : !ctrl.irRepairWarning ? (
            <button
              type="button"
              className={buttonSecondaryClass}
              onClick={ctrl.resetAll}
            >
              Dismiss
            </button>
          ) : null}
        </div>
      ) : null}
      {session.error?.message ? (
        <div role="alert" className={joinClasses(bannerBaseClass, statusBannerToneClass('error'))}>
          <strong>{session.error.message}</strong>
        </div>
      ) : null}

      {currentStep === 'describe' ? (
        <WizardDescribeStep
          session={session}
          sessionId={session.id}
          projectLabel={ctrl.projectLabel}
          checkpoint={ctrl.checkpoint}
          llmProvider={ctrl.llmProvider}
          llmEnabled={ctrl.llmEnabled}
          busyMessage={ctrl.busyMessage}
          message={ctrl.message}
          setMessage={ctrl.setMessage}
          projectName={ctrl.projectName}
          setProjectName={ctrl.setProjectName}
          symbolsDir={ctrl.symbolsDir}
          setSymbolsDir={ctrl.setSymbolsDir}
          metaExpanded={ctrl.metaExpanded}
          setMetaExpanded={ctrl.setMetaExpanded}
          onSendMessage={(e) => void ctrl.handleSendMessage(e)}
        />
      ) : null}

      {currentStep === 'spec' && session.spec ? (
        <WizardSpecStep
          session={session}
          sessionId={session.id}
          projectLabel={ctrl.projectLabel}
          checkpoint={ctrl.checkpoint}
          llmEnabled={ctrl.llmEnabled}
          busyMessage={ctrl.busyMessage}
          message={ctrl.message}
          setMessage={ctrl.setMessage}
          canApproveSpec={ctrl.canApproveSpec}
          canGenerateIr={ctrl.canGenerateIr}
          hasUnspecifiedCustomBlocks={ctrl.hasUnspecifiedCustomBlocks}
          onSendMessage={(e) => void ctrl.handleSendMessage(e)}
          onApproveSpec={() => void ctrl.handleApproveSpec()}
        />
      ) : null}

      {currentStep === 'ir' ? (
        <WizardIrStep
          session={session}
          sessionId={session.id}
          projectLabel={ctrl.projectLabel}
          checkpoint={ctrl.checkpoint}
          llmEnabled={ctrl.llmEnabled}
          busyMessage={ctrl.busyMessage}
          canGenerateIr={ctrl.canGenerateIr}
          canGenerateProject={ctrl.canGenerateProject}
          onGenerateIr={() => void ctrl.handleGenerateIr()}
          onClearIr={() => void ctrl.handleClearIr()}
        />
      ) : null}

      {currentStep === 'generate' ? (
        <WizardGenerateStep
          session={session}
          sessionId={session.id}
          projectLabel={ctrl.projectLabel}
          checkpoint={ctrl.checkpoint}
          busyMessage={ctrl.busyMessage}
          canGenerateProject={ctrl.canGenerateProject}
          visibleLatestJob={ctrl.visibleLatestJob}
          confirmRegenerate={ctrl.confirmRegenerate}
          setConfirmRegenerate={ctrl.setConfirmRegenerate}
          onGenerateProject={() => void ctrl.handleGenerateProject()}
        />
      ) : null}
    </div>
  )
}
