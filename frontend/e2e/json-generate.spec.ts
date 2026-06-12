import { test, expect } from '@playwright/test'

const VALID_IR = JSON.stringify(
  {
    version: '1',
    components: [
      { ref: 'J1', symbol: 'Connector_Generic:Conn_01x01', value: 'In' },
      { ref: 'R1', symbol: 'Device:R', value: '10k' },
      { ref: 'J2', symbol: 'Connector_Generic:Conn_01x01', value: 'Out' },
    ],
    nets: [
      { name: 'IN', pins: [{ ref: 'J1', pin: '1' }, { ref: 'R1', pin: '1' }] },
      { name: 'OUT', pins: [{ ref: 'R1', pin: '2' }, { ref: 'J2', pin: '1' }] },
    ],
  },
  null,
  2,
)

const INVALID_IR = JSON.stringify({ version: '1' }, null, 2)

test.describe('Circuit IR direct JSON flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/generate-json')
    // Wait for the page to fully load (bootstrap query completes)
    await expect(page.getByRole('heading', { name: 'Generate from Circuit IR JSON' })).toBeVisible()
  })

  test('valid IR validates successfully', async ({ page }) => {
    await page.getByLabel('JSON').fill(VALID_IR)
    await page.getByRole('button', { name: 'Validate' }).click()

    await expect(page.getByRole('heading', { name: 'Validation Passed' })).toBeVisible()
    await expect(page.getByText('3')).toBeVisible() // component count
  })

  test('Generate button is present and enabled after valid validation', async ({ page }) => {
    await page.getByLabel('JSON').fill(VALID_IR)
    await page.getByRole('button', { name: 'Validate' }).click()

    await expect(page.getByRole('heading', { name: 'Validation Passed' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Generate KiCad Project' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Generate KiCad Project' })).toBeEnabled()
  })

  test('invalid IR shows Validation Failed with error messages', async ({ page }) => {
    await page.getByLabel('JSON').fill(INVALID_IR)
    await page.getByRole('button', { name: 'Validate' }).click()

    await expect(page.getByRole('heading', { name: 'Validation Failed' })).toBeVisible()
    // At least one validation error card is shown
    await expect(page.locator('text=/validation error/i').first()).toBeVisible()
  })

  test('Generate button is absent after invalid validation', async ({ page }) => {
    await page.getByLabel('JSON').fill(INVALID_IR)
    await page.getByRole('button', { name: 'Validate' }).click()

    await expect(page.getByRole('heading', { name: 'Validation Failed' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Generate KiCad Project' })).not.toBeVisible()
  })

  test('changing Symbols Directory clears prior validation result', async ({ page }) => {
    await page.getByLabel('JSON').fill(VALID_IR)
    await page.getByRole('button', { name: 'Validate' }).click()
    await expect(page.getByRole('heading', { name: 'Validation Passed' })).toBeVisible()

    await page.getByLabel(/symbols directory/i).fill('/some/nonexistent/path')
    await expect(page.getByRole('heading', { name: 'Validation Passed' })).not.toBeVisible()
    await expect(page.getByRole('heading', { name: 'Validation Failed' })).not.toBeVisible()
  })

  test('validation result does not leak private paths in error response', async ({ page }) => {
    await page.getByLabel('JSON').fill(INVALID_IR)
    await page.getByRole('button', { name: 'Validate' }).click()
    await expect(page.getByRole('heading', { name: 'Validation Failed' })).toBeVisible()

    const content = await page.locator('body').textContent()
    expect(content).not.toContain('/tmp/')
    expect(content).not.toContain('kicad-pcb-web-prepare')
  })

  test('generate KiCad project navigates to job page', async ({ page }) => {
    await page.getByLabel('JSON').fill(VALID_IR)
    await page.getByRole('button', { name: 'Validate' }).click()
    await expect(page.getByRole('heading', { name: 'Validation Passed' })).toBeVisible()

    await page.getByRole('button', { name: 'Generate KiCad Project' }).click()

    // Server runs the job synchronously; navigation should happen within 30s
    await page.waitForURL(/\/jobs\//, { timeout: 30_000 })

    // Job is done by the time we arrive (synchronous server execution)
    await expect(page.getByText(/Succeeded|Failed/)).toBeVisible({ timeout: 30_000 })
  })
})
