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

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(path, { ...init, headers })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    let message: string = `Request failed with status ${response.status}`
    const structuredMessage = payload?.error?.message
    if (typeof structuredMessage === 'string' && structuredMessage) {
      message = structuredMessage
    } else {
      const detail = payload?.detail
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
    throw new ApiError(message, response.status, payload)
  }
  return payload as T
}

export function jsonBody(payload: unknown): string {
  return JSON.stringify(payload)
}
