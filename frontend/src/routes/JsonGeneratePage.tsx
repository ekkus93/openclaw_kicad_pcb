import { useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '../api'
import type { ValidateNetlistResponse } from '../types'
import { joinClasses, getErrorMessage, statusBannerToneClass } from '../utils'
import {
  bannerBaseClass,
  buttonPrimaryClass,
  buttonRowClass,
  buttonSecondaryClass,
  detailListGridClass,
  emptyCopyClass,
  eyebrowClass,
  headingGroupClass,
  helpTextClass,
  heroLeadClass,
  mutedCopyClass,
  pageStackClass,
  panelAccentClass,
  panelSoftClass,
  spinnerClass,
} from '../styles/designTokens'

function WarningCard({ warning }: { warning: Record<string, unknown> }) {
  const code = typeof warning.code === 'string' ? warning.code : null
  const message = typeof warning.message === 'string' ? warning.message : null
  const severity = typeof warning.severity === 'string' ? warning.severity : 'warning'
  const family = typeof warning.family === 'string' ? warning.family : null

  const severityColors = {
    error: 'border-[rgba(154,45,40,0.2)] bg-[rgba(154,45,40,0.05)]',
    warning: 'border-[rgba(155,106,18,0.2)] bg-[rgba(155,106,18,0.05)]',
    info: 'border-[rgba(22,93,143,0.15)] bg-[rgba(22,93,143,0.05)]',
  }
  const badgeColors = {
    error: 'bg-[rgba(154,45,40,0.1)] text-[var(--error)]',
    warning: 'bg-[rgba(155,106,18,0.12)] text-[var(--warning)]',
    info: 'bg-[rgba(22,93,143,0.1)] text-[#0d4c74]',
  }
  const border = severityColors[severity as keyof typeof severityColors] ?? severityColors.warning
  const badge = badgeColors[severity as keyof typeof badgeColors] ?? badgeColors.warning

  return (
    <div className={joinClasses('rounded-[18px] border p-4', border)}>
      <div className="flex flex-wrap items-start gap-2">
        {severity !== 'warning' ? (
          <span className={joinClasses('rounded-full px-2 py-0.5 text-[0.72rem] font-bold uppercase tracking-[0.1em]', badge)}>
            {severity}
          </span>
        ) : null}
        {family ? (
          <span className="text-[0.75rem] font-semibold uppercase tracking-[0.1em] text-[var(--muted)]">
            {family.replaceAll('_', ' ')}
          </span>
        ) : null}
        {code ? (
          <code className="ml-auto rounded bg-[rgba(88,63,39,0.08)] px-1.5 py-0.5 text-[0.75rem] text-[var(--muted)]">
            {code}
          </code>
        ) : null}
      </div>
      {message ? (
        <p className="mt-2 text-[0.95rem] leading-6 text-[var(--text)]">{message}</p>
      ) : null}
    </div>
  )
}

// ─── JsonGeneratePage ─────────────────────────────────────────────────────────

export function JsonGeneratePage() {
  const navigate = useNavigate()
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const [jsonText, setJsonText] = useState('')
  const [projectName, setProjectName] = useState('')
  const [symbolsDir, setSymbolsDir] = useState('')
  const [autoFix, setAutoFix] = useState(true)

  const [validating, setValidating] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [validationResult, setValidationResult] = useState<ValidateNetlistResponse | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [parseError, setParseError] = useState<string | null>(null)

  function handleJsonChange(text: string) {
    setJsonText(text)
    setValidationResult(null)
    setErrorMessage(null)
    if (text.trim()) {
      try {
        JSON.parse(text)
        setParseError(null)
      } catch (e) {
        setParseError(e instanceof Error ? e.message : 'Invalid JSON')
      }
    } else {
      setParseError(null)
    }
  }

  function handleFileUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (e) => {
      const text = e.target?.result
      if (typeof text === 'string') {
        handleJsonChange(text)
      }
    }
    reader.readAsText(file)
    // Reset file input so same file can be re-uploaded
    event.target.value = ''
  }

  function parsedJson(): Record<string, unknown> | null {
    try {
      const parsed = JSON.parse(jsonText)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>
      }
      return null
    } catch {
      return null
    }
  }

  async function handleValidate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const netlist = parsedJson()
    if (!netlist) {
      setErrorMessage('The JSON is not a valid object. Fix the syntax above before validating.')
      return
    }
    setValidating(true)
    setErrorMessage(null)
    setValidationResult(null)
    try {
      const result = await api.validateNetlist({
        netlist_json: netlist,
        symbols_dir: symbolsDir || null,
      })
      setValidationResult(result)
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setValidating(false)
    }
  }

  async function handleGenerate() {
    const netlist = parsedJson()
    if (!netlist || !validationResult?.valid) return
    setGenerating(true)
    setErrorMessage(null)
    try {
      const job = await api.createJobFromNetlist({
        project_name: projectName || 'Circuit IR Project',
        netlist_json: netlist,
        symbols_dir: symbolsDir || null,
        auto_fix: autoFix,
      })
      navigate(`/jobs/${job.id}`)
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setGenerating(false)
    }
  }

  const canValidate = Boolean(jsonText.trim()) && !parseError && !validating && !generating
  const canGenerate = Boolean(validationResult?.valid) && !validating && !generating

  return (
    <div className={pageStackClass}>
      <section className={panelAccentClass}>
        <div className={headingGroupClass}>
          <p className={eyebrowClass}>Generate</p>
          <h1>Generate from Circuit IR JSON</h1>
          <p className={heroLeadClass}>
            Paste or upload a Circuit IR JSON document to generate a KiCad project directly.
            No LLM provider is required.
          </p>
        </div>
      </section>

      {errorMessage ? (
        <div className={joinClasses(bannerBaseClass, statusBannerToneClass('error'))}>
          <strong>{errorMessage}</strong>
        </div>
      ) : null}

      {(validating || generating) ? (
        <div className={joinClasses(bannerBaseClass, statusBannerToneClass('active'))}>
          <span className={spinnerClass} aria-hidden="true"></span>
          <strong>{validating ? 'Validating Circuit IR…' : 'Generating KiCad project…'}</strong>
        </div>
      ) : null}

      <section className={panelSoftClass}>
        <div className={headingGroupClass}>
          <h2>Circuit IR JSON</h2>
          <p className={mutedCopyClass}>
            Paste your Circuit IR JSON below, or upload a <code>.json</code> file.
          </p>
        </div>

        <div className={buttonRowClass}>
          <button
            type="button"
            className={buttonSecondaryClass}
            onClick={() => fileInputRef.current?.click()}
            disabled={validating || generating}
          >
            Upload .json file
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={handleFileUpload}
          />
        </div>

        <form onSubmit={(e) => void handleValidate(e)} className="grid gap-4">
          <label className="grid gap-1.5">
            <span className="text-[0.86rem] font-semibold text-[var(--text)]">JSON</span>
            <textarea
              className="min-h-[18rem] rounded-[18px] border border-[rgba(88,63,39,0.14)] bg-[rgba(255,255,255,0.72)] p-4 font-[var(--font-mono)] text-[0.86rem] leading-[1.55] transition-[border-color,box-shadow] duration-150 focus:border-[rgba(109,47,20,0.3)] focus:outline-none focus:ring-2 focus:ring-[rgba(109,47,20,0.12)] resize-y"
              placeholder='{"blocks": [...], "nets": [...]}'
              value={jsonText}
              onChange={(e) => handleJsonChange(e.target.value)}
              disabled={validating || generating}
              spellCheck={false}
            />
          </label>

          {parseError ? (
            <div className={joinClasses(bannerBaseClass, statusBannerToneClass('error'))}>
              <strong>JSON syntax error: {parseError}</strong>
            </div>
          ) : null}

          <div className="grid gap-3 rounded-[22px] border border-[rgba(88,63,39,0.12)] bg-[rgba(255,255,255,0.46)] p-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="grid gap-1">
                <span className="text-[0.82rem] font-semibold text-[var(--text)]">Project Name</span>
                <input
                  className="rounded-[14px] border border-[rgba(88,63,39,0.14)] bg-[rgba(255,255,255,0.72)] px-3 py-2 text-[0.9rem] focus:border-[rgba(109,47,20,0.3)] focus:outline-none focus:ring-2 focus:ring-[rgba(109,47,20,0.12)]"
                  placeholder="e.g. My Circuit"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  disabled={validating || generating}
                />
              </label>
              <label className="grid gap-1">
                <span className="text-[0.82rem] font-semibold text-[var(--text)]">
                  Symbols Directory <span className="font-normal text-[var(--muted)]">(optional)</span>
                </span>
                <input
                  className="rounded-[14px] border border-[rgba(88,63,39,0.14)] bg-[rgba(255,255,255,0.72)] px-3 py-2 text-[0.9rem] focus:border-[rgba(109,47,20,0.3)] focus:outline-none focus:ring-2 focus:ring-[rgba(109,47,20,0.12)]"
                  placeholder="Leave blank to use built-in symbols"
                  value={symbolsDir}
                  onChange={(e) => setSymbolsDir(e.target.value)}
                  disabled={validating || generating}
                />
              </label>
            </div>
            <label className="flex items-center gap-2 text-[0.88rem] text-[var(--text)]">
              <input
                type="checkbox"
                checked={autoFix}
                onChange={(e) => setAutoFix(e.target.checked)}
                disabled={validating || generating}
                className="h-4 w-4 accent-[var(--brand)]"
              />
              Auto-fix minor validation issues before generating
            </label>
            <p className={helpTextClass}>
              Symbols Directory: absolute path to a KiCad symbol library directory.
              Leave blank to use the built-in symbols.
            </p>
          </div>

          <div className={buttonRowClass}>
            <button
              type="submit"
              className={buttonPrimaryClass}
              disabled={!canValidate}
            >
              Validate
            </button>
          </div>
        </form>
      </section>

      {/* ── Validation results panel ────────────────────────────────────────── */}
      {validationResult ? (
        <section className={panelSoftClass}>
          <div className={headingGroupClass}>
            <h2>
              {validationResult.valid ? 'Validation Passed' : 'Validation Failed'}
            </h2>
            <p className={mutedCopyClass}>
              {validationResult.valid
                ? 'The Circuit IR is valid. You can now generate the KiCad project.'
                : 'The Circuit IR has errors. Fix the JSON and validate again.'}
            </p>
          </div>

          <dl className={detailListGridClass}>
            <dt className={mutedCopyClass}>Valid</dt>
            <dd>{validationResult.valid ? 'Yes' : 'No'}</dd>
            <dt className={mutedCopyClass}>Components</dt>
            <dd>{validationResult.component_count}</dd>
            <dt className={mutedCopyClass}>Nets</dt>
            <dd>{validationResult.net_count}</dd>
          </dl>

          {validationResult.warnings.length > 0 ? (
            <div>
              <p className="mb-2 text-[0.82rem] font-semibold text-[var(--muted)]">
                {validationResult.warnings.length} warning{validationResult.warnings.length === 1 ? '' : 's'}
              </p>
              <div className="grid gap-3">
                {validationResult.warnings.map((w, i) => (
                  <WarningCard
                    key={i}
                    warning={typeof w === 'object' && w !== null ? (w as Record<string, unknown>) : { message: String(w) }}
                  />
                ))}
              </div>
            </div>
          ) : (
            <p className={emptyCopyClass}>No warnings.</p>
          )}

          {validationResult.valid ? (
            <div className={buttonRowClass}>
              <button
                type="button"
                className={buttonPrimaryClass}
                disabled={!canGenerate}
                onClick={() => void handleGenerate()}
              >
                Generate KiCad Project
              </button>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  )
}
