# CI failure and recovery rehearsal

This branch publishes the current SakikoMind UI snapshot with its regressions.
The UI layout changes already existed locally before the Web testing work and
are not counted as newly discovered defects.

## What the workflow verifies

- A clean Ubuntu runner installs the committed npm lockfile and browser engines.
- The production build succeeds.
- 35 UI scenarios and 30 browser HTTP-adapter checks run on Chromium, Firefox,
  and WebKit: 65 distinct checks and 195 browser executions.
- These regression tests use controlled HTTP responses. They do not verify live
  LLM behavior, real ChromaDB retrieval quality, Redis, or load capacity.
- HTML, JSON, JUnit, failure screenshots, traces, and the separate CI drill JSON
  are retained as GitHub Actions artifacts for 14 days.

`tests/ci/ci-gate.mjs` is a separate pipeline rehearsal, excluded from the 65
product checks and 195 execution total. It creates `test-results/ci-drill.json`
and deliberately returns exit code 1 while `injectedFailure` is `true`.
It does not change application behavior or claim to reproduce a product defect.

## Rehearsal sequence

1. Push the explicit failure-injection commit to a `codex/web-regression-*`
   branch. No default-branch update or pull request is required.
2. Record its commit, Actions run URL, all product-test results, drill failure,
   and artifact download/digest. A failed product check must be investigated
   separately; it is not explained by the intentional drill.
3. Change only `const injectedFailure = true` to `false`, commit the removal of
   the injected failure, and push the same branch.
4. Record the new run and artifacts. The recovery is verified only when the
   build, browser regressions, and separate drill step actually succeed.

The upload step uses `if: ${{ !cancelled() }}`, so an ordinary test or drill
failure still preserves available reports. Cancelled runs are not guaranteed
to upload artifacts.

## Local reproduction

From `SakikoMindFrontend` with a supported Node.js and npm installation:

```sh
npm ci
npx playwright install --with-deps chromium firefox webkit
npm run build
npx playwright test ui.spec.js adapter.spec.js
node tests/ci/ci-gate.mjs
```

The final command is expected to fail in the injection commit. Keep that result
separate from product regression results. Use `CI_DRILL_REPORT_PATH` to direct
its JSON report to an isolated location when rehearsing locally.

On a machine with an installed Chrome browser, the optional
`PLAYWRIGHT_CHROMIUM_CHANNEL=chrome` selects it for the Chromium project. The CI
workflow uses Playwright's bundled browsers without that override. Any local
browser-launch failure is an environment block, not a passed test.
