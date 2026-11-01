# Experience V2 integration follow-up

> Execute in this session using the existing contracts and test-driven fixes;
> complete with an independent review. After acceptance, the user authorized
> committing and pushing to experience-v2-integration only; no main merge.

**Goal:** Verify V2 migration, scoped retrieval, merging and Vue integration on
this Windows environment, and correct demonstrated defects within the owner's scope.
**Architecture:** Use the existing V2 API and scripted models. Preserve the nine-column
SQLite source by migrating a backup. Check response provenance at the frontend HTTP
boundary; retain the backend as the authority for merge compatibility.
**Tech stack:** Existing Python, SQLite, Vue 3, TypeScript, Vitest, pytest and Playwright.
**Spec:** ../docs/experience-v2-contract.md and ../backend/experience/V2.md.

## Constraints

- Start a local branch from main@6527421, then fast-forward the existing V2 changes
  at a06d0f3 into experience-v2-integration for testing. main remains unchanged.
- New edits stay within frontend/, backend/experience/ and experience tests.
- Do not change production Agent, API, tools, shared models or dependency manifests.
- Do not call live models. Real Maven/pytest and SQLite must execute in acceptance.
- Keep local work in the existing checkout, as specified by INTEGRATION_PLAN.md.
- New checks must expose actual defects, not merely repeat implementation details.

## Review focus

- Case and internal whitespace in code/string literals must not be erased by merging.
- A merge preview/result must correspond to the selected source IDs; source order
  does not matter, but missing, duplicate or substituted identities do.
- Java evidence cannot claim a pytest runner, and Python evidence cannot claim Maven.
- Conflicts and stale previews must not enable confirmation or claim a saved result.
- Old database IDs, timestamps and original columns survive migration unchanged.

## Task 1: Experience merge fidelity

Files: backend/experience/store.py, backend/tests/test_experience_v2.py,
backend/experience/V2.md.
Interfaces: preview_merge(ids) and merge_experiences(ids), unchanged.

- [x] Add failing tests for case-sensitive root/solution literals, meaningful internal
  whitespace and differing applicability conditions across records.
- [x] Preserve exact rule and applicability text when checking compatibility and
  deduplicating issue rules; preserve existing tag/exception/path matching behavior.
- [x] Run the experience suites, including idempotence, order, rollback and concurrency.

## Task 2: Frontend merge provenance

Files: frontend/src/api/validation.ts, frontend/src/api/http.ts,
frontend/tests/experienceV2.test.ts.
Interfaces: existing AgentApi previewMerge/mergeExperiences; internal readers may
accept the expected selection to validate successful responses.

- [x] Add failing tests for mismatched preview sources, incorrect merged_from,
  source IDs reused as the result ID, conflicting previews carrying a result,
  mismatched language/runner and duplicate provenance.
- [x] Reject invalid responses through the existing ApiError(502) boundary.
  Accept reordered and case-equivalent UUID sets. Keep legacy record support.
- [x] Verify UI conflict recovery and preview invalidation with meaningful tests.
- [x] Run the full frontend suite, production build and integration check.

## Task 3: Migration, real HTTP and browser acceptance

Files: frontend/V2_INTEGRATION_ACCEPTANCE.md; ignored evidence under .cache/
and frontend/output/playwright/.

- [x] Copy the previous main acceptance database with SQLite backup; concurrently
  open the copy using V2 and verify all original columns and repeated migration.
- [x] Run existing scripts/acceptance_v2.py: Java/Python 12 tests each, partial repair
  feedback, merged knowledge delivered and source projects unchanged.
- [x] Use the real V2 FastAPI with a test-only model factory for Vue project choice,
  scoped filtering, merge preview/confirmation, idempotence and conflict handling.
- [x] Re-run full backend tests with temporary projects outside the Git checkout.
- [x] Record environment-dependent and shared-contract issues separately.
- [x] Obtain an independent review and resolve material findings before branch delivery.

## Evidence and decisions

- Baseline: frontend 67 passed. Backend: 347 passed, one path-sensitive project API
  assertion failed because the explicit basetemp was inside this Git checkout.
- Existing V2 repair acceptance passed with the project-local JDK 17.0.20 and Maven
  3.9.11. No new runtime dependency is required.

- Final: 353 backend and 78 frontend tests passed; build and contract checks passed.
- Five backend cases and eight frontend cases failed before the targeted fixes.
- Migration copy: two legacy rows preserved, 12 concurrent reads, original database unchanged.
- Browser: real Python/Java submission, 409 race recovery, successful/idempotent merge,
  effective merged retrieval, filtered pagination and legacy records passed.
- Final standard V2 acceptance: three tasks passed 12 tests each; merged record delivered
  to the scripted model; unrelated repository and original project checks passed.
- Independent review found no new material defects. Known shared condition normalization,
  memory-only task history and Git-sensitive test fixture are documented for their owners.
- Evidence and usage: V2_INTEGRATION_ACCEPTANCE.md. Delivery target:
  experience-v2-integration; main must remain unchanged.
