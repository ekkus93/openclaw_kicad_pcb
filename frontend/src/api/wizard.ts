import type { WizardGenerateProjectResponse, WizardSessionDetail } from '../types'
import { jsonBody, requestJson } from './client'

export function createWizardSession(payload: {
  message: string
  project_name?: string | null
  symbols_dir?: string | null
}): Promise<WizardSessionDetail> {
  return requestJson('/api/wizard/sessions', {
    method: 'POST',
    body: jsonBody(payload),
  })
}

export function getWizardSession(sessionId: string): Promise<WizardSessionDetail> {
  return requestJson(`/api/wizard/sessions/${encodeURIComponent(sessionId)}`)
}

export function addWizardMessage(
  sessionId: string,
  payload: {
    message: string
    project_name?: string | null
    symbols_dir?: string | null
  },
): Promise<WizardSessionDetail> {
  return requestJson(`/api/wizard/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: 'POST',
    body: jsonBody(payload),
  })
}

export function approveWizardSpec(sessionId: string): Promise<WizardSessionDetail> {
  return requestJson(`/api/wizard/sessions/${encodeURIComponent(sessionId)}/approve-spec`, {
    method: 'POST',
    body: jsonBody({}),
  })
}

export function clearWizardIr(sessionId: string): Promise<WizardSessionDetail> {
  return requestJson(`/api/wizard/sessions/${encodeURIComponent(sessionId)}/clear-ir`, {
    method: 'POST',
    body: jsonBody({}),
  })
}

export function generateWizardIr(sessionId: string): Promise<WizardSessionDetail> {
  return requestJson(`/api/wizard/sessions/${encodeURIComponent(sessionId)}/generate-ir`, {
    method: 'POST',
    body: jsonBody({}),
  })
}

export function generateWizardProject(sessionId: string): Promise<WizardGenerateProjectResponse> {
  return requestJson(`/api/wizard/sessions/${encodeURIComponent(sessionId)}/generate-project`, {
    method: 'POST',
    body: jsonBody({}),
  })
}
