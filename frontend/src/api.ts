import type {
  DoctorResponse,
  JobDetail,
  JobSummary,
  SymbolSearchResponse,
  UiBootstrapResponse,
  ValidateNetlistResponse,
  WizardGenerateProjectResponse,
  WizardSessionDetail,
} from './types'

export class ApiError extends Error {
  status: number
  details: unknown

  constructor(message: string, status: number, details: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.details = details
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const message = payload?.error?.message ?? `Request failed with status ${response.status}`
    throw new ApiError(message, response.status, payload)
  }
  return payload as T
}

function body(payload: unknown): string {
  return JSON.stringify(payload)
}

export const api = {
  getBootstrap(): Promise<UiBootstrapResponse> {
    return requestJson('/api/ui/bootstrap')
  },
  getDoctor(): Promise<DoctorResponse> {
    return requestJson('/api/doctor')
  },
  getJobs(): Promise<JobSummary[]> {
    return requestJson('/api/jobs')
  },
  getJob(jobId: string): Promise<JobDetail> {
    return requestJson(`/api/jobs/${encodeURIComponent(jobId)}`)
  },
  searchSymbols(query: string): Promise<SymbolSearchResponse> {
    return requestJson(`/api/symbols/search?q=${encodeURIComponent(query)}`)
  },
  validateNetlist(payload: {
    netlist_json: Record<string, unknown>
    symbols_dir?: string | null
  }): Promise<ValidateNetlistResponse> {
    return requestJson('/api/netlists/validate', {
      method: 'POST',
      body: body(payload),
    })
  },
  createJobFromNetlist(payload: {
    project_name: string
    netlist_json: Record<string, unknown>
    symbols_dir?: string | null
    validation?: string
    auto_fix?: boolean
  }): Promise<JobDetail> {
    return requestJson('/api/jobs/from-netlist', {
      method: 'POST',
      body: body(payload),
    })
  },
  createWizardSession(payload: {
    message: string
    project_name?: string | null
    symbols_dir?: string | null
  }): Promise<WizardSessionDetail> {
    return requestJson('/api/wizard/sessions', {
      method: 'POST',
      body: body(payload),
    })
  },
  getWizardSession(sessionId: string): Promise<WizardSessionDetail> {
    return requestJson(`/api/wizard/sessions/${encodeURIComponent(sessionId)}`)
  },
  addWizardMessage(
    sessionId: string,
    payload: {
      message: string
      project_name?: string | null
      symbols_dir?: string | null
    },
  ): Promise<WizardSessionDetail> {
    return requestJson(`/api/wizard/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: 'POST',
      body: body(payload),
    })
  },
  approveWizardSpec(sessionId: string): Promise<WizardSessionDetail> {
    return requestJson(`/api/wizard/sessions/${encodeURIComponent(sessionId)}/approve-spec`, {
      method: 'POST',
      body: body({}),
    })
  },
  clearWizardIr(sessionId: string): Promise<WizardSessionDetail> {
    return requestJson(`/api/wizard/sessions/${encodeURIComponent(sessionId)}/clear-ir`, {
      method: 'POST',
      body: body({}),
    })
  },
  generateWizardIr(sessionId: string): Promise<WizardSessionDetail> {
    return requestJson(`/api/wizard/sessions/${encodeURIComponent(sessionId)}/generate-ir`, {
      method: 'POST',
      body: body({}),
    })
  },
  generateWizardProject(sessionId: string): Promise<WizardGenerateProjectResponse> {
    return requestJson(`/api/wizard/sessions/${encodeURIComponent(sessionId)}/generate-project`, {
      method: 'POST',
      body: body({}),
    })
  },
}