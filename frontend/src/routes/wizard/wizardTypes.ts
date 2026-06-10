import type { Dispatch, FormEvent, SetStateAction } from 'react'
import type { JobDetail, WizardSessionDetail, WizardStep } from '../../types'

export interface WizardController {
  session: WizardSessionDetail | undefined
  sessionError: Error | null
  latestJob: JobDetail | undefined
  currentStep: WizardStep
  loading: boolean

  projectName: string
  setProjectName: (v: string) => void
  symbolsDir: string
  setSymbolsDir: (v: string) => void
  message: string
  setMessage: (v: string) => void
  metaExpanded: boolean
  setMetaExpanded: Dispatch<SetStateAction<boolean>>

  irRepairWarning: boolean
  confirmRegenerate: boolean
  setConfirmRegenerate: (v: boolean) => void

  llmEnabled: boolean
  llmProvider: string

  busyMessage: string | null
  errorMessage: string | null
  hasUnspecifiedCustomBlocks: boolean
  canApproveSpec: boolean
  canGenerateIr: boolean
  canGenerateProject: boolean
  visibleLatestJob: JobDetail | null
  checkpoint: { title: string; detail: string }
  projectLabel: string | null
  generateIrMutationHasError: boolean

  resetAll: () => void
  handleCreateSession: (event: FormEvent<HTMLFormElement>) => Promise<void>
  handleSendMessage: (event: FormEvent<HTMLFormElement>) => Promise<void>
  handleApproveSpec: () => Promise<void>
  handleGenerateIr: () => Promise<void>
  handleClearIr: () => Promise<void>
  handleGenerateProject: () => Promise<void>
}
