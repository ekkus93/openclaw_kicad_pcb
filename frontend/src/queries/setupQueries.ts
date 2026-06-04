import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { queryKeys } from '../queryKeys'

export function useDoctorQuery() {
  return useQuery({
    queryKey: queryKeys.doctor,
    queryFn: () => api.getDoctor(),
    staleTime: 30_000,
  })
}
