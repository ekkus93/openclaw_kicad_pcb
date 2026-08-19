import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { defineConfig, devices } from '@playwright/test'

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function parseWebPort(value: string, source: string): number {
  if (!/^\d+$/.test(value)) {
    throw new Error(`Invalid ${source}: ${value}`)
  }
  const port = Number(value)
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error(`Invalid ${source}: ${value}`)
  }
  return port
}

function resolveWebPort(): number {
  const configured = process.env.PLAYWRIGHT_WEB_PORT
  if (configured) {
    return parseWebPort(configured, 'PLAYWRIGHT_WEB_PORT')
  }

  const runId = process.env.GITHUB_RUN_ID
  const runAttempt = process.env.GITHUB_RUN_ATTEMPT ?? '1'
  if (process.env.CI && runId && /^\d+$/.test(runId) && /^\d+$/.test(runAttempt)) {
    const runKey = BigInt(runId) * 100n + BigInt(runAttempt)
    return 30_000 + Number(runKey % 20_000n)
  }

  return 8000
}

const WEB_PORT = resolveWebPort()
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
