import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { defineConfig, devices } from '@playwright/test'

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

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
    baseURL: 'http://127.0.0.1:8000',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000',
    url: 'http://127.0.0.1:8000',
    reuseExistingServer: !process.env.CI,
    cwd: PROJECT_ROOT,
    timeout: 30_000,
  },
})
