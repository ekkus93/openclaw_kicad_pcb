import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { queryKeys } from '../queryKeys'

export function useSymbolSearchQuery(query: string) {
  return useQuery({
    queryKey: queryKeys.symbols(query),
    queryFn: () => api.searchSymbols(query),
    enabled: query.trim().length > 0,
    staleTime: 60_000,
  })
}
