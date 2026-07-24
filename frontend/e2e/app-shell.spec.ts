import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

function failOnPageErrors(page: Page) {
  const failures: string[] = []
  page.on('pageerror', (error: Error) => failures.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') failures.push(message.text())
  })
  return failures
}

test.describe('installed app shell and local-only workflows', () => {
  test('navigates primary routes and survives deep-route refresh', async ({ page }) => {
    const browserErrors = failOnPageErrors(page)
    await page.goto('/')
    await expect(page.getByRole('link', { name: 'KiCad PCB Web App' })).toBeVisible()

    for (const label of ['Circuit IR', 'Jobs', 'Symbols', 'Setup', 'Wizard']) {
      await page.getByRole('link', { name: label }).first().click()
      await expect(page.locator('main')).toBeVisible()
    }

    await page.goto('/jobs/job_smoke')
    await page.reload()
    await expect(page.getByRole('heading', { name: /Job not found/i })).toBeVisible()
    expect(browserErrors).toEqual([])
  })

  test('wizard clearly exposes disabled-provider behavior', async ({ page }) => {
    const browserErrors = failOnPageErrors(page)
    await page.goto('/wizard')

    await expect(page.getByText('Provider disabled')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Start Session' })).toBeDisabled()
    await expect(page.getByText(/No LLM provider is configured/i)).toBeVisible()
    await expect(page.getByRole('link', { name: 'Generate from Circuit IR JSON' })).toBeVisible()
    expect(browserErrors).toEqual([])
  })
})
