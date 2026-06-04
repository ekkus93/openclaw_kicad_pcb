export const queryKeys = {
  bootstrap: ['bootstrap'] as const,
  jobs: ['jobs'] as const,
  job: (jobId: string) => ['jobs', jobId] as const,
  doctor: ['doctor'] as const,
  symbols: (query: string) => ['symbols', query] as const,
  wizardSession: (sessionId: string) => ['wizard', sessionId] as const,
}
