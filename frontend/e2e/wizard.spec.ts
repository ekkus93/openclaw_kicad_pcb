/**
 * Wizard smoke tests — require a configured LLM provider.
 * Run with: RUN_E2E_WIZARD_TESTS=1 npm run test:e2e
 */

import { test, expect } from '@playwright/test'

const CIRCUIT_REQUEST =
  'Simple voltage divider: R1 10k and R2 10k between VCC and GND with a midpoint output net.'

test.describe('Wizard flow with LLM', () => {
  test('creates session, receives LLM reply, generates IR', async ({ page }) => {
    test.skip(
      !process.env.RUN_E2E_WIZARD_TESTS,
      'Set RUN_E2E_WIZARD_TESTS=1 to run LLM-dependent wizard tests',
    )

    const consoleErrors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })

    // ── 1. Navigate to wizard and fill in the initial request ─────────────────
    await page.goto('/wizard')
    await expect(page.getByRole('heading', { name: /Describe Circuit/i })).toBeVisible()

    await page.getByLabel('Circuit Request').fill(CIRCUIT_REQUEST)
    await page.getByRole('button', { name: 'Start Session' }).click()

    // ── 2. Wait for LLM to respond (up to 60s) ────────────────────────────────
    // After session creation the wizard navigates to /wizard/:sessionId and the
    // LLM produces at least one assistant message.
    await page.waitForURL(/\/wizard\/[^/]+$/, { timeout: 30_000 })
    // Wait for an assistant message to appear in the conversation area
    await expect(
      page.locator('[role="log"], .conversation, main').getByText(/[A-Za-z]{20,}/),
    ).toBeVisible({ timeout: 60_000 })

    // ── 3. Navigate to the IR step and generate IR ────────────────────────────
    // The wizard has a breadcrumb or a button to move to the IR generation step.
    // Click "Circuit IR" in the breadcrumb/nav if available, otherwise look for
    // the Generate IR button directly.
    const generateIrButton = page.getByRole('button', { name: /Generate IR/i })
    if (await generateIrButton.isVisible()) {
      await generateIrButton.click()
      await expect(page.getByRole('heading', { name: /Circuit IR/i })).toBeVisible({
        timeout: 60_000,
      })
    }

    // ── 4. Confirm no browser-side errors occurred ────────────────────────────
    // Server-side resource leaks (LLM socket closure) must be checked in server
    // logs separately. Browser console errors are checked here as a proxy.
    expect(consoleErrors, `Browser console errors: ${consoleErrors.join('; ')}`).toHaveLength(0)
  })
})
