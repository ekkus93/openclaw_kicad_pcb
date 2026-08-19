import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { defineConfig, devices } from '@playwright/test'

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const WEB_PORT_TEXT = process.env.PLAYWRIGHT_WEB_PORT ?? '8000'
const WEB_PORT = Number(WEB_PORT_TEXT)

if (!Number.isInteger(WEB_PORT) || WEB_PORT < 1 || WEB_PORT > 65_535) {
  throw new Error(`Invalid PLAYWRIGHT_WEB_PORT: ${WEB_PORT_TEXT}`)
}

const WEB_BASE_URL = `http://127.0.0.1:${WEB_PORT}`

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: WEB_BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port ${WEB_PORT}`,
    url: WEB_BASE_URL,
    reuseExistingServer: !process.env.CI,
    cwd: PROJECT_ROOT,
    timeout: 30_000,
  },
})
