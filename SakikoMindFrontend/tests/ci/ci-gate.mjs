import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

// CI DRILL ONLY: this deliberately fails the first rehearsal commit.
// The recovery commit changes this literal to false after the failed run and
// its artifacts have been observed. This is not a SakikoMind product defect.
const injectedFailure = false

const report = {
  schemaVersion: 1,
  kind: 'intentional-ci-failure-drill',
  scope: 'Pipeline failure signal and artifact preservation; excluded from product regression totals.',
  injectedFailure,
  timestamp: new Date().toISOString(),
  commit: process.env.GITHUB_SHA || null,
  runId: process.env.GITHUB_RUN_ID || null,
  status: 'passed'
}

try {
  assert.equal(injectedFailure, false, 'INTENTIONAL CI DRILL: failure injection is active; this is not a product regression.')
} catch (error) {
  report.status = 'failed_intentionally'
  report.error = error.message
  process.exitCode = 1
}

const output = path.resolve(process.env.CI_DRILL_REPORT_PATH || 'test-results/ci-drill.json')
await mkdir(path.dirname(output), { recursive: true })
await writeFile(output, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
console.log(JSON.stringify(report, null, 2))
