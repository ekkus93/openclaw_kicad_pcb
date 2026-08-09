import { startTransition, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { useBootstrapQuery } from '../../queries/bootstrapQueries'
import { useJobQuery } from '../../queries/jobQueries'
import {
  useAddWizardMessageMutation,
  useApproveWizardSpecMutation,
  useClearWizardIrMutation,
  useCreateWizardSessionMutation,
  useGenerateWizardIrMutation,
  useGenerateWizardProjectMutation,
  useWizardSessionQuery,
} from '../../queries/wizardQueries'
import { getErrorMessage } from '../../utils'
import { writeLastSession } from '../../utils/session'
import type { WizardStep } from '../../types'
import {
  canonicalWizardStep,
  wizardCanGenerateIr,
  wizardCanGenerateProject,
  wizardCurrentCheckpoint,
  wizardStepUnlocked,
} from './wizardStepLogic'
import type { WizardController } from './wizardTypes'

export function useWizardController(
  sessionId: string | undefined,
  routeStep: WizardStep | undefined,
): WizardController {
  const navigate = useNavigate()

  const [projectName, setProjectName] = useState('')
  const [symbolsDir, setSymbolsDir] = useState('')
  const [message, setMessage] = useState('')
  const [metaExpanded, setMetaExpanded] = useState(false)
  const [irRepairWarning, setIrRepairWarning] = useState(false)
  const [confirmRegenerate, setConfirmRegenerate] = useState(false)

  const { data: bootstrap } = useBootstrapQuery()
  const { data: session, isLoading: sessionLoading, error: sessionError } = useWizardSessionQuery(sessionId)
  const { data: latestJob } = useJobQuery(session?.latest_job_id ?? undefined)

  const createMutation = useCreateWizardSessionMutation()
  const addMessageMutation = useAddWizardMessageMutation(session?.id ?? '')
  const approveSpecMutation = useApproveWizardSpecMutation(session?.id ?? '')
  const generateIrMutation = useGenerateWizardIrMutation(session?.id ?? '')
  const clearIrMutation = useClearWizardIrMutation(session?.id ?? '')
  const generateProjectMutation = useGenerateWizardProjectMutation(session?.id ?? '')

  const loading = Boolean(sessionId) && sessionLoading

  const llmProvider = bootstrap?.llm_provider ?? ''
  const llmEnabled = bootstrap?.llm_enabled ?? false

  const busyMessage: string | null = createMutation.isPending
    ? `Talking to ${llmProvider} to draft the first spec…`
    : addMessageMutation.isPending
      ? `Talking to ${llmProvider} to revise the spec draft…`
      : approveSpecMutation.isPending
        ? 'Locking this spec checkpoint and moving to Circuit Plan…'
        : generateIrMutation.isPending
          ? 'Generating circuit plan… the backend will attempt automatic repair if needed.'
          : clearIrMutation.isPending
            ? 'Clearing circuit plan…'
            : generateProjectMutation.isPending
              ? 'Generating the KiCad project from the validated circuit plan…'
              : null

  const errorMessage: string | null = irRepairWarning
    ? 'The backend could not produce a valid circuit plan after its repair passes. Review the error below, then click "Repair Circuit Plan" to try again or go back to the spec and revise the circuit description.'
    : createMutation.error
      ? getErrorMessage(createMutation.error)
      : addMessageMutation.error
        ? getErrorMessage(addMessageMutation.error)
        : approveSpecMutation.error
          ? getErrorMessage(approveSpecMutation.error)
          : generateIrMutation.error
            ? getErrorMessage(generateIrMutation.error)
            : clearIrMutation.error
              ? getErrorMessage(clearIrMutation.error)
              : generateProjectMutation.error
                ? getErrorMessage(generateProjectMutation.error)
                : null

  // Track which session we've already synced form fields from to avoid
  // overwriting user edits when session data refreshes after a mutation.
  const syncedSessionIdRef = useRef<string | null>(null)

  useEffect(() => {
    if (sessionId) writeLastSession(sessionId)
  }, [sessionId])

  useEffect(() => {
    if (!session || session.id === syncedSessionIdRef.current) return
    syncedSessionIdRef.current = session.id
    setProjectName(session.project_name ?? '')
    setSymbolsDir(session.symbols_dir ?? '')
  }, [session])

  useEffect(() => {
    if (!session) return
    const canonical = canonicalWizardStep(session)
    const targetStep = routeStep && wizardStepUnlocked(session, routeStep) ? routeStep : canonical
    const targetPath = `/wizard/${session.id}/${targetStep}`
    if (`/wizard/${session.id}/${routeStep ?? ''}` !== targetPath) {
      startTransition(() => {
        navigate(targetPath, { replace: true })
      })
    }
  }, [navigate, routeStep, session])

  const currentStep: WizardStep = useMemo(() => {
    if (!session) return 'describe'
    if (routeStep && wizardStepUnlocked(session, routeStep)) return routeStep
    return canonicalWizardStep(session)
  }, [routeStep, session])

  const hasUnspecifiedCustomBlocks = session
    ? (session.spec?.blocks.some(
        (b) => b.block_type === 'custom' && b.required_components.length === 0,
      ) ?? false)
    : false

  const canApproveSpec = session
    ? session.status === 'spec_ready_for_review' &&
      Boolean(session.spec) &&
      !session.spec_approved &&
      !session.open_questions.length &&
      !session.unsupported_reasons.length &&
      !hasUnspecifiedCustomBlocks
    : false

  const canGenerateIr = session ? wizardCanGenerateIr(session) : false
  const canGenerateProject = session ? wizardCanGenerateProject(session) : false
  const visibleLatestJob = session?.latest_job_id ? (latestJob ?? null) : null
  const checkpoint = session
    ? wizardCurrentCheckpoint(session, currentStep)
    : { title: '', detail: '' }
  const projectLabel = session?.project_name ?? session?.spec?.project_name ?? null

  function resetAll(): void {
    createMutation.reset()
    addMessageMutation.reset()
    approveSpecMutation.reset()
    generateIrMutation.reset()
    clearIrMutation.reset()
    generateProjectMutation.reset()
    setIrRepairWarning(false)
    setConfirmRegenerate(false)
  }

  async function handleCreateSession(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    resetAll()
    try {
      const response = await createMutation.mutateAsync({
        message,
        project_name: projectName || null,
        symbols_dir: symbolsDir || null,
      })
      setMessage('')
      writeLastSession(response.id)
      startTransition(() => {
        navigate(`/wizard/${response.id}/${canonicalWizardStep(response)}`)
      })
    } catch {
      // Error captured in createMutation.error
    }
  }

  async function handleSendMessage(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!session) return
    resetAll()
    try {
      const response = await addMessageMutation.mutateAsync({
        message,
        project_name: projectName || null,
        symbols_dir: symbolsDir || null,
      })
      setMessage('')
      startTransition(() => {
        navigate(`/wizard/${response.id}/${canonicalWizardStep(response)}`)
      })
    } catch {
      // Error captured in addMessageMutation.error
    }
  }

  async function handleApproveSpec(): Promise<void> {
    if (!session) return
    resetAll()
    try {
      const response = await approveSpecMutation.mutateAsync()
      startTransition(() => {
        navigate(`/wizard/${response.id}/${canonicalWizardStep(response)}`)
      })
    } catch {
      // Error captured in approveSpecMutation.error
    }
  }

  async function handleGenerateIr(): Promise<void> {
    if (!session) return
    resetAll()
    try {
      const response = await generateIrMutation.mutateAsync()
      if (response.status === 'ir_needs_repair') {
        setIrRepairWarning(true)
      }
      startTransition(() => {
        navigate(`/wizard/${response.id}/${canonicalWizardStep(response)}`)
      })
    } catch {
      // Error captured in generateIrMutation.error
    }
  }

  async function handleClearIr(): Promise<void> {
    if (!session) return
    resetAll()
    try {
      const response = await clearIrMutation.mutateAsync()
      startTransition(() => {
        navigate(`/wizard/${response.id}/ir`)
      })
    } catch {
      // Error captured in clearIrMutation.error
    }
  }

  async function handleGenerateProject(): Promise<void> {
    if (!session) return
    if (visibleLatestJob?.status === 'succeeded' && !confirmRegenerate) {
      setConfirmRegenerate(true)
      return
    }
    resetAll()
    try {
      await generateProjectMutation.mutateAsync()
      // session and job are updated in the query cache by onSuccess in wizardQueries.ts
    } catch {
      // Error captured in generateProjectMutation.error
    }
  }

  return {
    session,
    sessionError: sessionError as Error | null,
    latestJob,
    currentStep,
    loading,
    projectName,
    setProjectName,
    symbolsDir,
    setSymbolsDir,
    message,
    setMessage,
    metaExpanded,
    setMetaExpanded,
    irRepairWarning,
    confirmRegenerate,
    setConfirmRegenerate,
    llmEnabled,
    llmProvider,
    busyMessage,
    errorMessage,
    hasUnspecifiedCustomBlocks,
    canApproveSpec,
    canGenerateIr,
    canGenerateProject,
    visibleLatestJob,
    checkpoint,
    projectLabel,
    generateIrMutationHasError: Boolean(generateIrMutation.error),
    resetAll,
    handleCreateSession,
    handleSendMessage,
    handleApproveSpec,
    handleGenerateIr,
    handleClearIr,
    handleGenerateProject,
  }
}