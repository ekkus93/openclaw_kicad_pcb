import { expect, test } from '@playwright/test'

const VALID_IR = {
  version: '1',
  components: [
    { ref: 'J1', symbol: 'Connector_Generic:Conn_01x01', value: 'In' },
    { ref: 'R1', symbol: 'Device:R', value: '10k' },
  ],
  nets: [{ name: 'IN', pins: [{ ref: 'J1', pin: '1' }, { ref: 'R1', pin: '1' }] }],
}

const VALIDATION_RESPONSE = {
  valid: true,
  component_count: 2,
  net_count: 1,
  warnings: [],
  symbols_dirs_used: [],
  errors: [],
}

function wizardSession(overrides: Record<string, unknown> = {}) {
  return {
    id: 'wiz_browser_hardening',
    status: 'ir_ready_for_generation',
    created_at: '2026-08-09T00:00:00Z',
    updated_at: '2026-08-09T00:00:01Z',
    project_name: 'BrowserHardening',
    symbols_dir: null,
    llm_provider: 'mock',
    llm_model: 'mock-model',
    prompt_version: 'v1',
    messages: [
      { role: 'user', content: 'Build a small divider.' },
      { role: 'assistant', content: 'The circuit plan is ready.' },
    ],
    spec: {
      project_name: 'BrowserHardening',
      purpose: 'Browser hardening fixture.',
      supply_rails: [],
      inputs: [],
      outputs: [],
      blocks: [],
      required_components: [],
      constraints: [],
      assumptions: [],
      open_questions: [],
      acceptance_criteria: [],
      unsupported_reasons: [],
    },
    spec_approved: true,
    spec_approved_at: '2026-08-09T00:00:00Z',
    ir_json: VALID_IR,
    ir_validation: {
      valid: true,
      auto_fixed: false,
      component_count: 2,
      net_count: 1,
      warnings: [],
      fixes_applied: [],
      symbols_dirs_used: [],
      error_message: null,
    },
    assumptions: [],
    open_questions: [],
    unsupported_reasons: [],
    latest_job_id: null,
    error: null,
    ...overrides,
  }
}

test('direct JSON generation failure remains visible and does not navigate as success', async ({
  page,
}) => {
  await page.route('**/api/netlists/validate', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(VALIDATION_RESPONSE) })
  })
  await page.route('**/api/jobs/from-netlist', async (route) => {
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        error: {
          type: 'tool_error',
          code: 'PROJECT_GENERATION_FAILED',
          message: 'KiCad project generation failed.',
          details: { job_id: 'job_failed_browser', job_status: 'failed' },
        },
      }),
    })
  })

  await page.goto('/generate-json')
  await page.getByLabel('JSON').fill(JSON.stringify(VALID_IR))
  await page.getByRole('button', { name: 'Validate' }).click()
  await expect(page.getByRole('heading', { name: 'Validation Passed' })).toBeVisible()

  await page.getByRole('button', { name: 'Generate KiCad Project' }).click()

  await expect(page.getByText('KiCad project generation failed.')).toBeVisible()
  await expect(page).toHaveURL(/\/generate-json$/)
  await expect(page.getByRole('heading', { name: 'Validation Passed' })).toBeVisible()
})

test('wizard failed IR regeneration cannot unlock Generate and retry can recover', async ({ page }) => {
  let currentSession = wizardSession()
  let generateIrAttempts = 0

  await page.route('**/api/ui/bootstrap', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ llm_provider: 'mock', llm_enabled: true, example_netlist_json: VALID_IR }),
    })
  })
  await page.route('**/api/wizard/sessions/wiz_browser_hardening', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback()
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(currentSession) })
  })
  await page.route('**/api/wizard/sessions/wiz_browser_hardening/generate-ir', async (route) => {
    generateIrAttempts += 1
    if (generateIrAttempts === 1) {
      currentSession = wizardSession({
        status: 'failed',
        error: {
          type: 'upstream_provider_error',
          code: 'UPSTREAM_PROVIDER_ERROR',
          message: 'The configured LLM provider request failed.',
          details: { operation: 'generate_ir' },
        },
      })
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ error: currentSession.error }),
      })
      return
    }

    currentSession = wizardSession({
      updated_at: '2026-08-09T00:00:02Z',
      messages: [
        { role: 'user', content: 'Build a small divider.' },
        { role: 'assistant', content: 'The repaired circuit plan is ready.' },
      ],
    })
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(currentSession) })
  })

  await page.goto('/wizard/wiz_browser_hardening/ir')
  await expect(page.getByRole('heading', { name: 'Circuit Plan' }).first()).toBeVisible()
  await expect(page.getByRole('link', { name: /Continue to Generate/ })).toBeVisible()

  await page.getByRole('button', { name: 'Regenerate Circuit Plan' }).click()

  await expect(page.getByText('The configured LLM provider request failed.').first()).toBeVisible()
  await expect(page.getByRole('link', { name: /Continue to Generate/ })).toHaveCount(0)
  await expect(page).toHaveURL(/\/wizard\/wiz_browser_hardening\/ir$/)
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()

  await page.getByRole('button', { name: 'Try again' }).click()

  await expect(page).toHaveURL(/\/wizard\/wiz_browser_hardening\/generate$/)
  await expect(page.getByRole('heading', { name: 'Generate Project' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Generate Project' })).toBeEnabled()
  await expect(page.getByText('The configured LLM provider request failed.')).toHaveCount(0)
  expect(generateIrAttempts).toBe(2)
})
