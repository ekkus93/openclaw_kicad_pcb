import type { SymbolSearchResponse } from '../types'
import { requestJson } from './client'

export function searchSymbols(query: string): Promise<SymbolSearchResponse> {
  return requestJson(`/api/symbols/search?q=${encodeURIComponent(query)}`)
}
