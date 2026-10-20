# M3 Vue frontend implementation plan

Goal: implement task submission, completed-step polling, terminal result and
experience browsing against the four existing HTTP contracts, using mock data.
Spec: ../docs/interfaces.md sections 3 and 7 and the user-approved M3 workflow.
Execution: inline using TDD, frontend-design, Playwright and a final code review.

## Scope and decisions

- Changes only in frontend/, branch frontend-m3 from origin/main a5a58d4.
- Vue 3 + TypeScript + Vue Router + Vite. Vitest and Vue Test Utils for behavior.
- No backend edits, new HTTP endpoints, diff editor, cancellation, task list,
  semantic search, SSE or WebSocket. Mock adapter and HTTP adapter implement the
  same typed interface, with no demo-only properties in HTTP request bodies.
- Three routes: / (submit), /tasks/:taskId (process/result), /experiences (browse).
- Mock mode is clearly identified. Simulation scenarios: success, tests fail,
  stopped, repair succeeds but experience save fails, scheduling fails.
- Mock data persists in sessionStorage for refresh within a tab. Experiences and
  task states are explicitly demonstration data, not backend SQLite data.
- HTTP only uses POST /api/tasks, GET /api/tasks/:id, GET /api/tasks/:id/steps,
  GET /api/experiences?limit&offset. Project identifier is frontend configuration.
- Use per-request AbortSignal, a timeout, safe HTTP errors, sequential polling,
  sequence deduplication, route cleanup, stale-response protection and final-step
  catch-up. No percentage from steps_completed/max_steps (different units).
- Show task, tests and experience outcomes separately, including null facts and
  terminal scheduling errors without AgentResult. Read-only tool output preserves
  whitespace and is escaped as text. No display of private model reasoning.
- Experience response is an array without total. Use previous/next plus one-item
  lookahead, and details inline. No invented full-library search endpoint.
- No deployment or publish: local repository frontend for the team's later API.

## Design

Purpose: a repair workbench for students to inspect a Java repair run and its
reusable evidence. Left navigation, focused work area, readable timeline + code
output, separate fact summary. Responsive single-column layout on small screens.
Palette: ink #182b3a, slate #627486, paper #f4f7fa, white #ffffff,
teal #087f8c, amber #9a6700. Chinese system font stack with Segoe UI; code uses
Cascadia Code/Consolas. Medium rounded inputs, restrained dividers, no gradients,
decorative charts or inaccurate metrics. Native labels/focus, reduced motion.

## Steps and review focus

- [x] 1. Setup and red tests for exact HTTP requests, mock lifecycle/error/result
  consistency, persisted refresh and experience pagination. Implement adapters.
- [x] 2. Red tests for polling cancellation, duplicates, stale responses, terminal
  catch-up and transient errors. Implement composable and status presentation.
- [x] 3. Component tests for validation, submit gating, three-state results and
  experience paging/errors; implement views, components, responsive style.
- [x] 4. Build/typecheck and full frontend suite; browser-check success/failure,
  final step, refresh, pagination, keyboard behavior and mobile overflow.
- [x] 5. Independent review; fix reproduced findings; document commands, mock
  semantics, real API switch, boundary limitations and test results.

Public uncertainties: source_project registration is not provided yet; use a
clearly documented mock ID configurable via VITE_SOURCE_PROJECT. API CORS belongs
to the API owner. No need for changes to shared models or existing endpoints.

## Verification — 2026-10-20

- `npm.cmd test -- --reporter=dot`: 26 tests passed across 4 files.
- `npm.cmd run build`: Vue/TypeScript check and Vite production build passed.
- Edge via Playwright: submission validation, running completed steps, refresh,
  final test output, successful experience save, pagination, source link, all
  four alternative scenarios, missing task/retry and keyboard skip link passed.
- Desktop 1440px and mobile 390px checked; no horizontal page overflow in the
  submission, expanded result or expanded experience screens. Browser console:
  zero errors and zero warnings.
- Exported actual mock responses for all five scenarios and validated 66 objects
  through existing `backend.shared.models` Pydantic classes without editing them.
- Independent review found status hidden behind a failed steps request and
  incomplete Java replacement content. Both were reproduced with failing tests,
  fixed and reviewed again. Keyboard skip/hash routing was also fixed and tested.
- Browser screenshots and temporary acceptance scripts are under ignored
  `output/playwright/`; no generated artifacts or dependencies are staged.
- All repository changes are inside `frontend/`; no backend or shared tests changed.
  Real API integration remains outside this mock-first milestone.
