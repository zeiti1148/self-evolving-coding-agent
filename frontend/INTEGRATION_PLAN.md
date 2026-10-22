# Integration readiness implementation plan

**Goal:** Prepare the experience module and Vue client for integration using the
existing four endpoints, with repeatable cross-language/SQLite acceptance.
**Spec:** User-confirmed scope on 2026-10-22 and ../docs/interfaces.md.
**Architecture:** Keep production API/Agent/tools untouched. A frontend-only
HTTP boundary checks display-critical fields; Vite forwards same-origin /api
requests in development. Acceptance uses real public models and a temporary
SQLite file, not a replacement production backend.
**Tech stack:** Existing Vue, Vite, Vitest, Python/Pydantic, SQLite; no new dependencies.

## Constraints and decisions

- Branch integration-readiness from origin/main 89522a9; user prefers no codex prefix.
- Changes limited to frontend/, backend/experience/ and corresponding experience tests.
- Work in the existing checkout/feature branch, preserving the user's workflow.
- The user has approved completing integration preparation; no extra design approval
  is needed. Do not merge, commit or push without a new request for this change.
- No production API, Agent, tools or shared-model edits. No invented Diff/search API.
- Real HTTP errors must not fall back to mock success or present malformed data as empty.
- Existing known shared Windows path test remains a separately reported dependency.

## Tasks

- [x] 1. HTTP configuration and response failures.
  Files: src/api/context.ts, src/api/http.ts, src/api/validation.ts, src/main.ts,
  vite.config.ts, .env.example, tests/http.test.ts, tests/context.test.ts.
  Red: missing/invalid task and step fields, mismatched IDs, non-array experience
  responses and experience endpoint 404 must reject safely. Empty base URL must
  use /api on the current origin. Mock flag false must never use mock data.
  Implement display-critical checks without trimming tool output. Add an optional
  development proxy, retaining explicit remote URL support.
  Verify: npm.cmd test; npm.cmd run build.
- [x] 2. Repeatable contract and SQLite acceptance.
  Files: scripts/check-integration.mjs, scripts/check_contracts.py,
  package.json. Negative controls run inside the acceptance command.
  Export five real mock scenarios, validate every response with Pydantic, persist
  and reopen successful experience data in a TemporaryDirectory, test search and
  paging, serialize real stored Experience data back to the real HTTP adapter.
  Exercise a local HTTP fixture through Vite's proxy and all four client methods.
  Test validator rejection by corrupting a status/pair/result. Python selection:
  PYTHON env path, repository .venv, then python on PATH. Never seed app.db.
  Verify: npm.cmd run check:integration (exit 0); malformed fixture rejection tests.
- [x] 3. UI recovery and handoff.
  Files: tests/views.test.ts, README.md, backend/experience/README.md.
  Exercise an experience response rejected by the real HTTP adapter through the
  actual Vue view; assert error/retry and record rendering. The acceptance command
  separately verifies real serialized SQLite records through the same HTTP adapter.
  Document exact setup, proxy vs production deployment, project ID coordination,
  commands and remaining backend gaps. Review final diff independently.
  Verify: full frontend tests/build, experience tests, full Python suite with the
  original shared path failure explicitly reported; browser-check HTTP mode via
  a test-only server if needed for proxy/UI verification.

## Review focus

- Missing optional/defaulted fields vs malformed required data: avoid rejecting valid backend output.
- Steps and result data may arrive separately; retries must preserve final status.
- A frontend fallback HTML response must be an error, never a false empty library.
- Proxy config belongs to Vite local servers; it does not configure production hosting.
- Acceptance failures must exit nonzero and temporary data/servers must be cleaned up.

## Evidence / rulings

- Baseline: frontend 26 passed and build passed; experience 67 passed.
- Default pytest temp root was inaccessible; a fresh workspace .cache basetemp
  succeeds. No application change was required for that environment error.
- Investigated invalid-Unicode save handling; existing Pydantic already rejects it.
  No experience implementation change is justified by that hypothesis.
- Task 1: 12 new tests failed before response/config implementation; 38/38 then
  passed and the production build passed after correcting TypeScript annotations.
- Task 2: Ruling: keep Python-dependent acceptance outside default Vitest so ordinary
  frontend tests need only Node. The same command runs negative controls; cost: a
  separate check:integration invocation is required for cross-language coverage.
- Task 2: Pydantic validated 66 objects in five scenarios; temporary SQLite saved,
  reopened, searched and paginated seven rows. Four real adapter methods passed
  through actual Vite proxy. Invalid fixtures reject specifically as ValidationError.
- Task 3: Ruling: test the Vue error/retry behavior with serialized fixture responses,
  and SQLite serialization through the real HTTP adapter separately. This keeps
  component tests independent of Python; cost: it is not full browser/production E2E.
- Final review: one independent reviewer identified missing final-test evidence
  checking. Fixed no-test, failed-test and write-after-test with RED→GREEN tests;
  also rejected array-valued statuses. Frontend full suite 44/44, build passed.
- Full Python suite: 124 passed, 1 failed. The unchanged failure is
  backend/tests/test_shared_models.py::test_normal_models_are_created_with_defaults
  (Windows treats /tmp/tasks/example as non-absolute). Experience subset: 67 passed.
- Real API/Agent/tools execution and the shared Windows-path fix remain with their
  owners. No production backend modules, shared models or shared tests changed.
- No deferred minor review findings. Changes remain uncommitted for user review.
