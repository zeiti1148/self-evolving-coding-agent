# Workbench usability improvements — 2026-10-22

Scope: continue the user's experience/frontend work in integration-readiness;
preserve the previous uncommitted integration changes. No backend/API contract
changes, new dependencies, commits or publication.

Design: retain the existing teal #087f8c, ink #182b3a, paper #f4f7fa, white,
muted #627486 and error #b33d43 palette and Chinese system font/Cascadia code.
Add a compact wrapping toolbar inside the execution panel: filter at left,
expand/collapse and download at right. Keep existing timeline sequence numbers,
never renumber filtered steps. The filter does not change the final result.
Existing single-column mobile layout and keyboard focus treatment stay in use.

- [x] Log controls: all/unsuccessful steps, matching/total counts, bulk toggle
  and useful empty-filter state. Keep manual expansion when polling or filtering.
  New task resets local controls. Extract ExecutionSteps.vue from TaskView.vue.
- [x] Download JSON: task snapshot + all fetched steps, timestamp, mock flag and
  complete_steps flag. A filtered view must never silently export only failures.
  Incomplete/running/failed-step-fetch snapshots must not claim a complete run.
  User clicks to download locally; preserve exact output whitespace and plain text.
- [x] Mock cache recovery: validate persisted tasks and experiences before use;
  skip malformed/duplicate records while preserving valid records. Invalid root
  JSON resets only demo state. Show a concrete notice when recovery occurred.
- [x] Verification: tests before behavior, full frontend suite/build, existing
  cross-language/SQLite acceptance, desktop/mobile browser interactions including
  actual downloaded JSON, final independent review and README usage updates.

Implementation files: components/ExecutionSteps.vue, features/taskExport.ts,
views/TaskView.vue, api/mockStorage.ts, api/mock.ts, api/context.ts, App.vue,
styles.css; corresponding tests under frontend/tests. Experience storage itself
already passed 67 tests and is not changed without a reproduced defect.

Acceptance: 53 frontend tests, TypeScript/Vue check and production build;
5 scenarios / 66 public-model objects / 7 temporary SQLite rows and all four
HTTP methods through the Vite proxy. Edge at desktop and 390px: filtering,
bulk and keyboard expansion, actual JSON download with all five steps and
preserved whitespace, refresh, partial cache recovery, experience navigation;
no console errors/warnings. Independent review found no actionable issues.
Browser artifacts are ignored under frontend/output/playwright/.
