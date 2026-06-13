import { useState } from 'react'

export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)

  function handleCopy() {
    if (!navigator.clipboard) return
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      aria-label={`${label}: ${text}`}
      className="rounded-md border border-[rgba(88,63,39,0.14)] bg-[rgba(255,255,255,0.6)] px-2 py-0.5 text-[0.75rem] font-medium text-[var(--muted)] transition-colors duration-150 hover:bg-[rgba(255,255,255,0.9)] hover:text-[var(--text)]"
    >
      {copied ? 'Copied!' : label}
    </button>
  )
}
