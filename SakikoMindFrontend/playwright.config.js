import { defineConfig, devices } from '@playwright/test'
import { createRequire } from 'node:module'
import path from 'node:path'

const require = createRequire(import.meta.url)
const baseURL = process.env.BASE_URL || 'http://127.0.0.1:4173'
const port = new URL(baseURL).port || '4173'
const viteCLI = path.join(path.dirname(require.resolve('vite/package.json')), 'bin/vite.js')

export default defineConfig({
  testDir: './tests/web',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: Number(process.env.PLAYWRIGHT_WORKERS || 2),
  timeout: 30_000,
  expect: { timeout: 5_000 },
  outputDir: process.env.PLAYWRIGHT_OUTPUT_DIR || 'test-results/artifacts',
  reporter: [
    ['list'],
    ['html', { outputFolder: process.env.PLAYWRIGHT_HTML_REPORT || 'playwright-report', open: 'never' }],
    ['json', { outputFile: process.env.PLAYWRIGHT_JSON_REPORT || 'test-results/results.json' }],
    ['junit', { outputFile: process.env.PLAYWRIGHT_JUNIT_REPORT || 'test-results/junit.xml' }]
  ],
  metadata: {
    scope: 'UI uses deterministic mocked HTTP responses; adapter tests exercise the browser HTTP boundary. No real model, RAG quality, or load claims.'
  },
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off'
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], ...(process.env.PLAYWRIGHT_CHROMIUM_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHROMIUM_CHANNEL } : {}) } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } }
  ],
  webServer: process.env.SKIP_WEBSERVER === '1' ? undefined : {
    command: `"${process.execPath}" "${viteCLI}" --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 60_000
  }
})
