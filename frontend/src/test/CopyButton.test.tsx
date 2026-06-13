import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { CopyButton } from '../components/CopyButton'

const mockWriteText = vi.fn().mockResolvedValue(undefined)

beforeEach(() => {
  mockWriteText.mockClear()
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: mockWriteText },
    configurable: true,
  })
})

describe('CopyButton', () => {
  it('renders with default label', () => {
    render(<CopyButton text="abc123" />)
    expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument()
    expect(screen.getByText('Copy')).toBeInTheDocument()
  })

  it('renders with custom label', () => {
    render(<CopyButton text="abc123" label="Copy ID" />)
    expect(screen.getByText('Copy ID')).toBeInTheDocument()
  })

  it('calls clipboard.writeText with the provided text on click', () => {
    render(<CopyButton text="job-uuid-999" />)
    fireEvent.click(screen.getByRole('button'))
    expect(mockWriteText).toHaveBeenCalledWith('job-uuid-999')
  })

  it('shows Copied! immediately after click', async () => {
    render(<CopyButton text="job-uuid-999" />)
    fireEvent.click(screen.getByRole('button'))
    await vi.waitFor(() => {
      expect(screen.getByText('Copied!')).toBeInTheDocument()
    })
  })
})
