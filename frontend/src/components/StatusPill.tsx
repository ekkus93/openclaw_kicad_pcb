import { joinClasses } from '../utils'
import type { StatusTone } from '../utils'

export function StatusPill({ tone, children }: { tone: StatusTone; children: string }) {
  const tones: Record<StatusTone, string> = {
    active: 'bg-[rgba(22,93,143,0.1)] text-[#0d4c74]',
    success: 'bg-[rgba(35,102,79,0.1)] text-[var(--success)]',
    warning: 'bg-[rgba(155,106,18,0.11)] text-[var(--warning)]',
    error: 'bg-[rgba(154,45,40,0.09)] text-[var(--error)]',
    neutral: 'bg-[rgba(117,99,80,0.1)] text-[var(--muted)]',
  }
  return (
    <span
      className={joinClasses(
        'inline-flex items-center rounded-full px-[0.7rem] py-[0.35rem] text-[0.83rem] font-bold',
        tones[tone],
      )}
    >
      {children}
    </span>
  )
}
