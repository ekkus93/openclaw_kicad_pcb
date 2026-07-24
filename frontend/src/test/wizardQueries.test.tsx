import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { api } from '../api'
import { queryKeys } from '../queryKeys'
import { useAddWizardMessageMutation } from '../queries/wizardQueries'

function wrapperFor(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('wizard mutation failure recovery', () => {
  it('invalidates the persisted session after a failed mutation', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    vi.spyOn(api, 'addWizardMessage').mockRejectedValueOnce(new Error('conflict'))

    const { result } = renderHook(() => useAddWizardMessageMutation('wiz_123'), {
      wrapper: wrapperFor(queryClient),
    })

    await act(async () => {
      await expect(
        result.current.mutateAsync({
          message: 'revise',
          project_name: null,
          symbols_dir: null,
        }),
      ).rejects.toThrow('conflict')
    })

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: queryKeys.wizardSession('wiz_123'),
      })
    })
  })
})
