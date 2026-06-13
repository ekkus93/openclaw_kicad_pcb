import { ApiError } from './api'

export type StatusTone = 'neutral' | 'active' | 'success' | 'warning' | 'error'

export function joinClasses(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}

export function formatDate(isoString: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZoneName: 'short',
    }).format(new Date(isoString))
  } catch {
    return isoString
  }
}

export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) {
    return error.message
  }
  return 'Unexpected error.'
}

export function statusBannerToneClass(tone: StatusTone): string {
  const tones: Record<StatusTone, string> = {
    active: 'border-[rgba(22,93,143,0.2)] bg-[rgba(22,93,143,0.1)] text-[#0d4c74]',
    success: 'border-[rgba(35,102,79,0.2)] bg-[rgba(35,102,79,0.1)] text-[var(--success)]',
    warning: 'border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.11)] text-[var(--warning)]',
    error: 'border-[rgba(154,45,40,0.18)] bg-[rgba(154,45,40,0.09)] text-[var(--error)]',
    neutral: 'border-[rgba(117,99,80,0.16)] bg-[rgba(117,99,80,0.1)] text-[var(--muted)]',
  }
  return tones[tone]
}
