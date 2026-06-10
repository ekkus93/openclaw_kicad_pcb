import { useEffect, useRef } from 'react'

import { joinClasses } from '../../utils'
import {
  composerCardClass,
  composerMetaRowClass,
  wizardTextareaClass,
} from '../../styles/designTokens'

interface WizardComposerProps {
  label: string
  placeholder: string
  value: string
  onChange: (value: string) => void
  disabled: boolean
  submitLabel?: string
  submitDisabled?: boolean
}

export function WizardComposer({
  label,
  placeholder,
  value,
  onChange,
  disabled,
  submitLabel,
  submitDisabled,
}: WizardComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    const node = textareaRef.current
    if (!node) return
    node.style.height = '0px'
    node.style.height = `${Math.min(node.scrollHeight, 320)}px`
  }, [value])

  return (
    <div className={composerCardClass}>
      <label>
        <span>{label}</span>
        <textarea
          ref={textareaRef}
          className={wizardTextareaClass}
          data-wizard-input="true"
          disabled={disabled}
          placeholder={placeholder}
          required
          rows={1}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }}
        />
      </label>
      <div className={composerMetaRowClass}>
        {disabled ? (
          <span>Waiting for response…</span>
        ) : (
          <span className="text-[var(--muted)]">Ctrl/Cmd+Enter to send</span>
        )}
        {submitLabel ? (
          <button
            type="submit"
            className={joinClasses(
              'rounded-full px-4 py-1.5 text-sm font-semibold transition-[transform,opacity,background-color] duration-150 hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-[0.55] disabled:transform-none',
              'bg-[linear-gradient(135deg,var(--brand)_0%,var(--brand-deep)_100%)] text-[#fff8f1] shadow-[0_4px_12px_rgba(109,47,20,0.2)]',
            )}
            disabled={submitDisabled ?? disabled}
          >
            {submitLabel}
          </button>
        ) : null}
      </div>
    </div>
  )
}
