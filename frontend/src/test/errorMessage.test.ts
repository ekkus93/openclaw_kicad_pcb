import { describe, expect, it } from 'vitest'

import { ApiError } from '../api'
import { getErrorMessage, getPersistedErrorMessage } from '../utils'

describe('getErrorMessage', () => {
  it('includes a safe backend correlation id when available', () => {
    const error = new ApiError(
      'An unexpected internal error occurred.',
      500,
      null,
      'INTERNAL_SERVER_ERROR',
      'err_1234',
    )

    expect(getErrorMessage(error)).toBe(
      'An unexpected internal error occurred. Reference: err_1234.',
    )
  })
})


describe('getPersistedErrorMessage', () => {
  it('includes the persisted correlation id after a page reload', () => {
    expect(
      getPersistedErrorMessage({
        message: 'An unexpected internal error occurred.',
        details: { error_id: 'err_persisted' },
      }),
    ).toBe('An unexpected internal error occurred. Reference: err_persisted.')
  })
})
