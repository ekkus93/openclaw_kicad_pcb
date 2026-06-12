import type { DoctorResponse } from '../types'
import { requestJson } from './client'

export function getDoctor(): Promise<DoctorResponse> {
  return requestJson('/api/doctor')
}
