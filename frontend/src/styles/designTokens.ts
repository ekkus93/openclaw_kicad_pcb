/**
 * Design tokens — centralised Tailwind class constants.
 *
 * Import from here instead of defining ad-hoc strings in components.
 * Keep this file as the single source of truth for shared styling constants.
 */

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

export const pageShellClass =
  'relative mx-auto max-w-[1320px] px-4 py-4 sm:px-6 sm:py-6 lg:px-4'
export const pageStackClass = 'grid gap-4 sm:gap-5'
export const stackColumnClass = 'grid gap-4 sm:gap-5'
export const dashboardGridClass =
  'grid gap-4 xl:[grid-template-columns:minmax(0,1.5fr)_minmax(320px,0.9fr)] sm:gap-5'

// ---------------------------------------------------------------------------
// Panels / Cards
// ---------------------------------------------------------------------------

const panelBaseClass =
  'rounded-[24px] border border-[rgba(88,63,39,0.12)] p-4 shadow-[0_16px_34px_rgba(71,43,19,0.08)] sm:rounded-[28px] sm:p-[1.35rem]'
export const panelSoftClass = `${panelBaseClass} bg-[linear-gradient(180deg,rgba(255,252,247,0.95),rgba(250,244,233,0.94))]`
export const panelAccentClass = `${panelBaseClass} bg-[linear-gradient(180deg,rgba(255,248,237,0.99),rgba(246,232,209,0.96))]`

export const heroPanelClass =
  "relative grid gap-5 overflow-hidden rounded-[26px] border border-[rgba(109,47,20,0.16)] bg-[linear-gradient(140deg,rgba(255,248,238,0.98),rgba(245,230,203,0.94)),radial-gradient(circle_at_top_right,rgba(16,78,74,0.28),transparent_36%)] p-5 shadow-[0_28px_80px_rgba(71,43,19,0.14)] sm:gap-7 sm:rounded-[32px] sm:p-[2rem] xl:[grid-template-columns:minmax(0,1.35fr)_minmax(300px,0.75fr)] before:pointer-events-none before:absolute before:inset-x-[6%] before:top-0 before:h-px before:bg-[linear-gradient(90deg,transparent,rgba(255,255,255,0.85),transparent)] before:content-[''] after:pointer-events-none after:absolute after:inset-[auto_-48px_-92px_auto] after:h-[280px] after:w-[280px] after:bg-[radial-gradient(circle,rgba(242,196,138,0.7),transparent_70%)] after:content-['']"
export const heroCardClass =
  'relative z-[1] grid gap-3 rounded-[24px] border border-[rgba(88,63,39,0.12)] bg-[linear-gradient(180deg,rgba(255,255,255,0.82),rgba(255,248,238,0.76))] p-4 shadow-[0_18px_40px_rgba(71,43,19,0.1)] backdrop-blur-sm sm:rounded-[28px] sm:p-[1.35rem]'
export const heroStatGridClass = 'mt-6 grid gap-3 sm:grid-cols-3'
export const heroStatCardClass =
  'rounded-[22px] border border-[rgba(88,63,39,0.1)] bg-[rgba(255,255,255,0.58)] px-4 py-3 backdrop-blur-sm'

export const blockGridClass =
  'grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(220px,1fr))]'
export const blockCardClass =
  'grid gap-2 rounded-[20px] border border-[rgba(88,63,39,0.14)] bg-[linear-gradient(180deg,rgba(255,255,255,0.7),rgba(255,247,235,0.62))] p-4 shadow-[0_12px_28px_rgba(71,43,19,0.06)]'

// ---------------------------------------------------------------------------
// Typography
// ---------------------------------------------------------------------------

export const headingGroupClass = 'mb-4 grid gap-1.5'
export const eyebrowClass =
  'm-0 text-[0.83rem] font-bold uppercase tracking-[0.14em] text-[var(--brand)]'
export const mutedCopyClass = 'text-[var(--muted)]'
export const heroLeadClass = 'max-w-[60ch] text-[1.02rem] leading-7 text-[var(--muted)]'
export const emptyCopyClass = 'text-[var(--muted)]'
export const helpTextClass = 'text-[0.82rem] leading-[1.55] text-[var(--muted)]'
export const compactSupportCopyClass = 'text-sm leading-6 text-[var(--muted)]'

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------

const buttonBaseClass =
  'rounded-full border border-transparent px-[1.2rem] py-[0.8rem] font-semibold no-underline transition-[transform,opacity,box-shadow,background-color] duration-150 hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-[0.55] disabled:transform-none'
export const buttonPrimaryClass = `${buttonBaseClass} bg-[linear-gradient(135deg,var(--brand)_0%,var(--brand-deep)_100%)] text-[#fff8f1] shadow-[0_18px_36px_rgba(109,47,20,0.2)] hover:shadow-[0_22px_44px_rgba(109,47,20,0.24)]`
export const buttonSecondaryClass = `${buttonBaseClass} border-[rgba(24,75,69,0.12)] bg-[rgba(255,255,255,0.72)] text-[var(--accent)] hover:bg-[rgba(255,255,255,0.9)]`
export const buttonDangerClass = `${buttonBaseClass} border-[rgba(154,45,40,0.2)] bg-[rgba(255,255,255,0.72)] text-[var(--error)] hover:bg-[rgba(154,45,40,0.06)]`
export const buttonRowClass = 'flex flex-wrap gap-3'

export const wizardPrimaryButtonClass = `${buttonPrimaryClass} w-full justify-center text-center sm:w-auto`

// ---------------------------------------------------------------------------
// Forms
// ---------------------------------------------------------------------------

export const wizardFormClass = 'grid gap-4 sm:gap-5'
export const wizardFieldGridClass = 'grid gap-3 sm:gap-4 md:grid-cols-2'
export const wizardFieldSectionClass =
  'grid gap-4 rounded-[22px] border border-[rgba(88,63,39,0.12)] bg-[rgba(255,255,255,0.46)] p-3.5 sm:p-4'
export const wizardTextareaClass =
  'min-h-[10rem] text-base leading-7 sm:min-h-[12rem] sm:text-[1.02rem] [&::-webkit-resizer]:hidden'
export const wizardActionRowClass = 'flex flex-col gap-3 sm:flex-row sm:flex-wrap'

// ---------------------------------------------------------------------------
// Banners / status
// ---------------------------------------------------------------------------

export const bannerBaseClass =
  'flex items-center gap-3 rounded-[18px] border px-[1.1rem] py-[0.9rem] shadow-[0_10px_24px_rgba(71,43,19,0.06)]'
export const spinnerClass =
  'h-4 w-4 animate-spin rounded-full border-2 border-[rgba(13,76,116,0.16)] border-t-current'
export const compactStatusRowClass = 'flex flex-wrap items-center gap-2'

// ---------------------------------------------------------------------------
// Lists / tables
// ---------------------------------------------------------------------------

export const recordListClass = 'm-0 grid list-none gap-3 p-0'
export const compactListItemClass =
  'flex items-baseline justify-between gap-3 lg:flex-col lg:items-start'
export const detailListClass =
  'm-0 grid list-none gap-x-3 gap-y-2 p-0 [grid-template-columns:max-content_minmax(0,1fr)]'
export const detailListGridClass = `${detailListClass} md:[grid-template-columns:repeat(2,max-content_minmax(0,1fr))]`

// ---------------------------------------------------------------------------
// Transcript / Composer
// ---------------------------------------------------------------------------

export const transcriptListClass = 'grid gap-4'
export const transcriptEntryClass =
  'grid gap-2 rounded-[22px] border p-4 shadow-[0_10px_24px_rgba(71,43,19,0.05)] sm:max-w-[88%]'
export const transcriptMetaClass =
  'flex items-center justify-between gap-3 text-[0.76rem] font-semibold uppercase tracking-[0.12em]'
export const transcriptBodyClass =
  'whitespace-pre-wrap break-words text-[0.98rem] leading-7 text-[var(--text)]'
export const composerCardClass =
  'grid gap-3 rounded-[22px] border border-[rgba(88,63,39,0.12)] bg-[rgba(255,255,255,0.58)] p-3.5 shadow-[0_8px_20px_rgba(71,43,19,0.04)] sm:p-4'
export const composerMetaRowClass =
  'flex items-center justify-between gap-3 text-[0.82rem] text-[var(--muted)] sm:text-[0.86rem]'

// ---------------------------------------------------------------------------
// Code / JSON
// ---------------------------------------------------------------------------

export const jsonBlockClass =
  'overflow-auto rounded-[18px] bg-[rgba(40,31,23,0.95)] p-4 font-[var(--font-mono)] text-[0.85rem] leading-[1.55] whitespace-pre-wrap break-words text-[#f7ead6]'

// ---------------------------------------------------------------------------
// Workflow steps (home page)
// ---------------------------------------------------------------------------

export const workflowStepListClass = 'm-0 grid list-none gap-2 p-0 sm:grid-cols-2'
export const workflowStepItemClass =
  'rounded-[18px] border border-[rgba(88,63,39,0.12)] bg-[rgba(255,255,255,0.68)] px-4 py-3 text-sm font-medium text-[var(--text)]'
