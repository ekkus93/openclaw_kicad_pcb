const LS_LAST_SESSION = 'lastWizardSession'

export function readLastSession(): string | null {
  try {
    return localStorage.getItem(LS_LAST_SESSION)
  } catch {
    return null
  }
}

export function writeLastSession(id: string): void {
  try {
    localStorage.setItem(LS_LAST_SESSION, id)
  } catch {
    /* ignore */
  }
}
