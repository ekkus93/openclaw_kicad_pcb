export class ApiError extends Error {
  status: number
  details: unknown
  code: string | null
  errorId: string | null

  constructor(
    message: string,
    status: number,
    details: unknown,
    code: string | null = null,
    errorId: string | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.details = details
    this.code = code
    this.errorId = errorId
  }
}

function _buildApiError(status: number, payload: unknown): ApiError {
  let message: string = `Request failed with status ${status}`
  let code: string | null = null
  let errorId: string | null = null
  if (payload && typeof payload === 'object') {
    const p = payload as Record<string, unknown>
    const structuredError = p['error'] as Record<string, unknown> | undefined
    const structuredMessage = structuredError?.['message']
    const structuredCode = structuredError?.['code']
    const structuredDetails = structuredError?.['details'] as Record<string, unknown> | undefined
    if (typeof structuredCode === 'string' && structuredCode) code = structuredCode
    if (typeof structuredDetails?.['error_id'] === 'string') errorId = structuredDetails['error_id']
    if (typeof structuredMessage === 'string' && structuredMessage) {
      message = structuredMessage
    } else {
      const detail = p['detail']
      if (typeof detail === 'string' && detail) {
        message = detail
      } else if (Array.isArray(detail) && detail.length > 0) {
        message = detail
          .map((e: unknown) => {
            if (e && typeof e === 'object' && 'msg' in e) return String((e as { msg: unknown }).msg)
            return String(e)
          })
          .join('; ')
      } else if (detail && typeof detail === 'object') {
        message = JSON.stringify(detail)
      }
    }
  }
  return new ApiError(message, status, payload, code, errorId)
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(path, { ...init, headers })

  if (response.ok) {
    let payload: unknown
    try {
      payload = await response.json()
    } catch {
      throw new ApiError(
        'Expected JSON response but received invalid JSON.',
        response.status,
        null,
      )
    }
    return payload as T
  }

  let errorPayload: unknown
  try {
    errorPayload = await response.json()
  } catch {
    errorPayload = null
  }
  throw _buildApiError(response.status, errorPayload)
}

export function jsonBody(payload: unknown): string {
  return JSON.stringify(payload)
}
