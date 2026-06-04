import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import { queryKeys } from '../queryKeys'
import type { WizardSessionDetail } from '../types'

export function useWizardSessionQuery(sessionId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.wizardSession(sessionId ?? ''),
    queryFn: () => api.getWizardSession(sessionId!),
    enabled: Boolean(sessionId),
  })
}

export function useCreateWizardSessionMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.createWizardSession.bind(api),
    onSuccess: (session: WizardSessionDetail) => {
      queryClient.setQueryData(queryKeys.wizardSession(session.id), session)
    },
  })
}

export function useAddWizardMessageMutation(sessionId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: Parameters<typeof api.addWizardMessage>[1]) =>
      api.addWizardMessage(sessionId, payload),
    onSuccess: (session: WizardSessionDetail) => {
      queryClient.setQueryData(queryKeys.wizardSession(session.id), session)
    },
  })
}

export function useApproveWizardSpecMutation(sessionId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.approveWizardSpec(sessionId),
    onSuccess: (session: WizardSessionDetail) => {
      queryClient.setQueryData(queryKeys.wizardSession(session.id), session)
    },
  })
}

export function useGenerateWizardIrMutation(sessionId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    // Backend owns all repair rounds — frontend calls once per user action.
    mutationFn: () => api.generateWizardIr(sessionId),
    onSuccess: (session: WizardSessionDetail) => {
      queryClient.setQueryData(queryKeys.wizardSession(session.id), session)
    },
  })
}

export function useClearWizardIrMutation(sessionId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.clearWizardIr(sessionId),
    onSuccess: (session: WizardSessionDetail) => {
      queryClient.setQueryData(queryKeys.wizardSession(session.id), session)
    },
  })
}

export function useGenerateWizardProjectMutation(sessionId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.generateWizardProject(sessionId),
    onSuccess: ({ session, job }) => {
      queryClient.setQueryData(queryKeys.wizardSession(session.id), session)
      queryClient.setQueryData(queryKeys.job(job.id), job)
    },
  })
}
