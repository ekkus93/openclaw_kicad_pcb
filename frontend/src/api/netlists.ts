import type { ValidateNetlistResponse } from '../types'
import { jsonBody, requestJson } from './client'

export function validateNetlist(payload: {
  netlist_json: Record<string, unknown>
  symbols_dir?: string | null
}): Promise<ValidateNetlistResponse> {
  return requestJson('/api/netlists/validate', {
    method: 'POST',
    body: jsonBody(payload),
  })
}
