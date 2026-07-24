import { describe, it, expect, vi, beforeEach } from 'vitest'

import { ApiError, requestJson } from '../api/client'

function makeResponse(status: number, body: unknown, contentType = 'application/json'): Response {
  const bodyStr = typeof body === 'string' ? body : JSON.stringify(body)
  return new Response(bodyStr, {
    status,
    headers: { 'Content-Type': contentType },
  })
}

function makeGarbledResponse(status: number): Response {
  return new Response('not-json{{{', { status, headers: { 'Content-Type': 'application/json' } })
}

describe('requestJson — successful responses', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  it('returns parsed payload for a successful JSON response', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(makeResponse(200, { ok: true }))
    const result = await requestJson('/test')
    expect(result).toEqual({ ok: true })
  })

  it('throws ApiError for a successful response with invalid JSON', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(makeGarbledResponse(200))
    await expect(requestJson('/test')).rejects.toBeInstanceOf(ApiError)
  })

  it('ApiError for invalid success JSON has a clear message', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(makeGarbledResponse(200))
    const err = await requestJson('/test').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).message).toMatch(/invalid json/i)
  })

  it('does not silently return null for a successful response with invalid JSON', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(makeGarbledResponse(200))
    const result = await requestJson('/test').catch(() => 'threw')
    expect(result).toBe('threw')
  })
})

describe('requestJson — error responses', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  it('throws ApiError for a structured backend error payload', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      makeResponse(400, { error: { message: 'Validation failed', code: 'ERR' } }),
    )
    const err = await requestJson('/test').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).message).toBe('Validation failed')
    expect((err as ApiError).status).toBe(400)
  })

  it('extracts message from FastAPI detail string', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      makeResponse(422, { detail: 'Field required' }),
    )
    const err = await requestJson('/test').catch((e: unknown) => e)
    expect((err as ApiError).message).toBe('Field required')
  })

  it('extracts messages from FastAPI detail array', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      makeResponse(422, { detail: [{ msg: 'field missing' }, { msg: 'bad value' }] }),
    )
    const err = await requestJson('/test').catch((e: unknown) => e)
    expect((err as ApiError).message).toContain('field missing')
    expect((err as ApiError).message).toContain('bad value')
  })

  it('throws ApiError with status message for non-JSON error response', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(makeGarbledResponse(500))
    const err = await requestJson('/test').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(500)
    expect((err as ApiError).message).toMatch(/500/)
  })

  it('preserves the parsed error payload on ApiError', async () => {
    const body = { error: { message: 'oops', code: 'ERR' } }
    vi.mocked(fetch).mockResolvedValueOnce(makeResponse(400, body))
    const err = await requestJson('/test').catch((e: unknown) => e)
    expect((err as ApiError).details).toEqual(body)
  })
})

describe('requestJson — request headers', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    vi.mocked(fetch).mockResolvedValue(makeResponse(200, {}))
  })

  function capturedHeaders(): Headers {
    const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit
    return init.headers as Headers
  }

  it('does not set Content-Type for GET requests without a body', async () => {
    await requestJson('/test')
    expect(capturedHeaders().has('content-type')).toBe(false)
  })

  it('sets Content-Type: application/json when a body is present', async () => {
    await requestJson('/test', { method: 'POST', body: JSON.stringify({ x: 1 }) })
    expect(capturedHeaders().get('content-type')).toBe('application/json')
  })

  it('preserves caller-supplied headers', async () => {
    await requestJson('/test', { headers: { 'X-Custom': 'value' } })
    expect(capturedHeaders().get('x-custom')).toBe('value')
  })

  it('does not overwrite caller-supplied Content-Type', async () => {
    await requestJson('/test', {
      method: 'POST',
      body: 'raw',
      headers: { 'Content-Type': 'text/plain' },
    })
    expect(capturedHeaders().get('content-type')).toBe('text/plain')
  })
})

describe('ApiError — structured metadata', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  it('exposes backend code and correlation id', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      makeResponse(500, {
        error: {
          message: 'An unexpected internal error occurred.',
          code: 'INTERNAL_SERVER_ERROR',
          details: { error_id: 'err_1234' },
        },
      }),
    )
    const err = await requestJson('/test').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('INTERNAL_SERVER_ERROR')
    expect((err as ApiError).errorId).toBe('err_1234')
  })
})
