import type { UiBootstrapResponse } from '../types'
import { requestJson } from './client'

export function getBootstrap(): Promise<UiBootstrapResponse> {
  return requestJson('/api/ui/bootstrap')
}
