export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export type WizardStatus =
  | 'drafting_spec'
  | 'awaiting_user_clarification'
  | 'spec_ready_for_review'
  | 'spec_approved'
  | 'drafting_ir'
  | 'ir_needs_repair'
  | 'ir_ready_for_generation'
  | 'generation_started'
  | 'completed'
  | 'failed'

export type WizardStep = 'describe' | 'spec' | 'ir' | 'generate'

export interface UiBootstrapResponse {
  llm_provider: string
  llm_enabled: boolean
  example_netlist_json: Record<string, unknown>
}

export interface DoctorCheck {
  name: string
  ok: boolean
  detail: string
}

export interface DoctorResponse {
  ok: boolean
  checks: DoctorCheck[]
}

export interface SymbolSearchResult {
  library: string
  name: string
  qualified_name: string
  aliases: string[]
  keywords: string[]
}

export interface SymbolSearchResponse {
  query: string
  results: SymbolSearchResult[]
}

export interface JobSummary {
  id: string
  status: JobStatus
  project_name: string
  created_at: string
  updated_at: string
}

export interface JobDetail extends JobSummary {
  request: Record<string, unknown>
  result: Record<string, unknown> | null
  error: Record<string, unknown> | null
  artifacts: string[]
}

export interface ValidationIssue {
  type: string
  code: string | null
  message: string
  details: Record<string, unknown>
}

export interface ValidateNetlistResponse {
  valid: boolean
  component_count: number | null
  net_count: number | null
  warnings: Array<Record<string, unknown>>
  symbols_dirs_used: string[]
  errors: ValidationIssue[]
}

export interface CircuitPortSpec {
  name: string
  description?: string | null
  signal_type?: string | null
}

export interface CircuitRailSpec {
  name: string
  nominal_voltage?: string | null
  description?: string | null
}

export interface CircuitBlockSpec {
  name: string
  block_type: string
  summary: string
  required_components: string[]
  constraints: string[]
}

export interface CircuitSpec {
  project_name?: string | null
  purpose: string
  supply_rails: CircuitRailSpec[]
  inputs: CircuitPortSpec[]
  outputs: CircuitPortSpec[]
  blocks: CircuitBlockSpec[]
  required_components: string[]
  constraints: string[]
  assumptions: string[]
  open_questions: string[]
  acceptance_criteria: string[]
  unsupported_reasons: string[]
}

export interface WizardMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface WizardIrValidation {
  valid: boolean
  auto_fixed: boolean
  component_count: number
  net_count: number
  warnings: Array<Record<string, unknown>>
  fixes_applied: string[]
  symbols_dirs_used: string[]
  error_message?: string | null
}

export interface WizardLlmProvenance {
  provider: string
  model?: string | null
  prompt_version: string
  endpoint_identity?: string | null
  config_revision: string
}

export interface WizardSessionDetail {
  id: string
  status: WizardStatus
  created_at: string
  updated_at: string
  project_name?: string | null
  symbols_dir?: string | null
  llm_provider?: string | null
  llm_model?: string | null
  prompt_version?: string | null
  messages: WizardMessage[]
  spec?: CircuitSpec | null
  spec_provenance?: WizardLlmProvenance | null
  spec_approved: boolean
  spec_approved_at?: string | null
  ir_json?: Record<string, unknown> | null
  ir_validation?: WizardIrValidation | null
  ir_provenance?: WizardLlmProvenance | null
  assumptions: string[]
  open_questions: string[]
  unsupported_reasons: string[]
  latest_job_id?: string | null
  error?: {
    message: string
    type?: string
    code?: string | null
    details?: Record<string, unknown>
  } | null
}

export interface WizardGenerateProjectResponse {
  session: WizardSessionDetail
  job: JobDetail
}
