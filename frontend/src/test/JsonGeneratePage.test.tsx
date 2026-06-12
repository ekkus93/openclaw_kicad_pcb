import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, fireEvent, waitFor } from '@testing-library/react'

import { JsonGeneratePage } from '../routes/JsonGeneratePage'
import { renderWithProviders } from './renderWithProviders'
import type { ValidateNetlistResponse } from '../types'

vi.mock('../api', () => ({
  api: {
    validateNetlist: vi.fn(),
    createJobFromNetlist: vi.fn(),
  },
}))

import { api } from '../api'

const VALID_IR = JSON.stringify({ blocks: [], nets: [] })

const VALID_RESPONSE: ValidateNetlistResponse = {
  valid: true,
  component_count: 4,
  net_count: 3,
  warnings: [],
  errors: [],
  symbols_dirs_used: [],
}

const INVALID_RESPONSE: ValidateNetlistResponse = {
  valid: false,
  component_count: null,
  net_count: null,
  warnings: [],
  errors: [
    {
      type: 'user_error',
      code: 'IR001',
      message: 'Missing required field: blocks',
      details: {},
    },
  ],
  symbols_dirs_used: [],
}

describe('JsonGeneratePage — validation results', () => {
  beforeEach(() => {
    vi.mocked(api.validateNetlist).mockReset()
    vi.mocked(api.createJobFromNetlist).mockReset()
  })

  it('shows "Validation Failed" when API returns valid: false', async () => {
    vi.mocked(api.validateNetlist).mockResolvedValueOnce(INVALID_RESPONSE)
    renderWithProviders(<JsonGeneratePage />)

    fireEvent.change(screen.getByRole('textbox', { name: /^json$/i }), {
      target: { value: VALID_IR },
    })
    fireEvent.click(screen.getByRole('button', { name: /^validate$/i }))

    await waitFor(() => expect(screen.getByText('Validation Failed')).toBeTruthy())
  })

  it('renders validation error messages when valid: false', async () => {
    vi.mocked(api.validateNetlist).mockResolvedValueOnce(INVALID_RESPONSE)
    renderWithProviders(<JsonGeneratePage />)

    fireEvent.change(screen.getByRole('textbox', { name: /^json$/i }), {
      target: { value: VALID_IR },
    })
    fireEvent.click(screen.getByRole('button', { name: /^validate$/i }))

    await waitFor(() =>
      expect(screen.getByText('Missing required field: blocks')).toBeTruthy(),
    )
  })

  it('does not show Generate button when valid: false', async () => {
    vi.mocked(api.validateNetlist).mockResolvedValueOnce(INVALID_RESPONSE)
    renderWithProviders(<JsonGeneratePage />)

    fireEvent.change(screen.getByRole('textbox', { name: /^json$/i }), {
      target: { value: VALID_IR },
    })
    fireEvent.click(screen.getByRole('button', { name: /^validate$/i }))

    await waitFor(() => screen.getByText('Validation Failed'))
    expect(screen.queryByRole('button', { name: /generate kicad project/i })).toBeNull()
  })

  it('shows Generate button when valid: true', async () => {
    vi.mocked(api.validateNetlist).mockResolvedValueOnce(VALID_RESPONSE)
    renderWithProviders(<JsonGeneratePage />)

    fireEvent.change(screen.getByRole('textbox', { name: /^json$/i }), {
      target: { value: VALID_IR },
    })
    fireEvent.click(screen.getByRole('button', { name: /^validate$/i }))

    await waitFor(() => screen.getByText('Validation Passed'))
    expect(screen.getByRole('button', { name: /generate kicad project/i })).toBeTruthy()
  })

  it('clears validation result when Symbols Directory changes', async () => {
    vi.mocked(api.validateNetlist).mockResolvedValueOnce(VALID_RESPONSE)
    renderWithProviders(<JsonGeneratePage />)

    fireEvent.change(screen.getByRole('textbox', { name: /^json$/i }), {
      target: { value: VALID_IR },
    })
    fireEvent.click(screen.getByRole('button', { name: /^validate$/i }))
    await waitFor(() => screen.getByText('Validation Passed'))

    fireEvent.change(
      screen.getByRole('textbox', { name: /symbols directory/i }),
      { target: { value: '/some/path' } },
    )

    expect(screen.queryByText('Validation Passed')).toBeNull()
    expect(screen.queryByText('Validation Failed')).toBeNull()
  })
})
