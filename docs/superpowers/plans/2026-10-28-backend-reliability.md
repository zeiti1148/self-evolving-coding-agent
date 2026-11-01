# Backend Reliability Implementation Plan

> Execute natively with superpowers:executing-plans and test-driven fixes.
> The user requested continuing broadly without further scope questions.
> Review the finished diff independently. After acceptance, the user authorized
> committing and pushing to backend-reliability only; no main merge.

**Goal:** Preserve experience conditions and make task records reliable across API restarts.
**Architecture:** Independent task tables in the existing SQLite file, transactional
snapshots, startup interruption handling, unchanged existing HTTP responses.
**Tech Stack:** Existing Python, sqlite3, Pydantic, FastAPI, pytest and Vue clients.
**Spec:** ../../backend-reliability.md.

## Global Constraints

- Start from 721e73a on local backend-reliability; keep published branches unchanged.
- No dependency additions or live model requests. Do not modify original sample projects.
- Single API instance/process; preserve terminal records and stop unfinished records on restart.
- No automatic task re-execution, destructive cleanup or new public required fields.
- Task data uses existing database configuration; never commit runtime databases or secrets.

## Review Focus

- Failed step/final writes must roll back counts, results and audit together.
- A copied/mutated result cannot replace the tool evidence already shown to users.
- Reopening the database cannot mark completed tasks interrupted or erase old experiences.
- A damaged/locked database must yield a safe error, not an empty list or accepted unsaved task.
- Shutdown or a model-client cleanup error must not lose independently verified results.

## Task 1: Preserve exact applicability conditions

Files: backend/shared/models.py, backend/tests/test_shared_models.py,
backend/tests/test_experience_v2.py, docs/interfaces.md.
Interface: existing ExperienceCreate/Experience.applicability, unchanged schema.

- [x] Add tests proving case-sensitive conditions survive construction, assignment,
  serialization, saving/reopening and merge compatibility checks; observe failures.
- [x] Add a dedicated exact-text normalizer; retain existing tag behavior and limits.
- [x] Run shared model and experience tests; expected no failures.

## Task 2: Transactional task persistence

Files: backend/api/store.py, backend/tests/test_task_store.py.
Interfaces: TaskStore(database_path: Path | None = None), initialize(), close();
create(request: TaskCreateRequest | None = None), get_request(task_id),
list_tasks(limit=20, offset=0); retain get/get_steps/start/append/get_audit/put_audit.
finish(task_id, result=None, error=None, *, audit: dict | None = None) saves atomically.

- [x] Add behavioral tests for restart, interrupted recovery, concurrent mutation,
  duplicate steps, full trace agreement, detached snapshots and rollback; run RED.
- [x] Implement lazy SQLite initialization, independent tables, locked transactions,
  safe TaskStorageError and consistent snapshots without changing public models.
- [x] Run the task store suite; expected no failures.

## Task 3: API lifecycle and compatibility

Files: backend/api/main.py, backend/tests/test_api.py,
backend/tests/test_project_api.py, README.md, docs/interfaces.md.
Interfaces: existing task endpoints unchanged; additive GET /api/tasks pagination;
503 with generic detail on task storage errors.

- [x] Add restart/detail/steps/history, storage-failure and final-evidence tests;
  verify failing behavior before wiring the store into FastAPI.
- [x] Initialize storage in lifespan, persist requests, finalize result/audit together,
  close after workers finish, and map storage errors to 503.
- [x] Reproduce then correct the project API test's assumption of no parent Git repository;
  verify both repository-local and external temporary directories.
- [x] Update operational and public interface documentation.

## Task 4: End-to-end regression and review

Files: additional targeted Agent regression only if a reproducible defect is confirmed;
docs/backend-reliability.md for results. Evidence under ignored .cache/.

- [x] Investigate model-client cleanup failures; if they override verified results,
  add a failing test and preserve the result while reporting cleanup failure safely.
- [x] Run full backend tests, frontend tests, build and contract checks once final code is ready.
- [x] Run scripted Java/Python repair acceptance with actual tools and a fresh database.
- [x] Recreate API against the same database and check status, steps, audit, experience
  links, old-record preservation, interrupted tasks and original-project integrity.
- [x] Obtain an independent review; fix material findings and document actual outcomes.

## Completion evidence

- 392 backend tests and 78 frontend tests passed; frontend build and contracts passed.
- The reviewer found bool/int equality could replace evidence; three RED cases became GREEN
  after deterministic JSON comparison, followed by the final full backend regression.
- Final scripted acceptance passed three Java/Python tasks with 12 tests each.
- Recreated API and real browser retained task details, seven steps per successful task,
  audit and experience source links; synthetic interrupted records were stopped.
- Two legacy experience rows and nine original columns survived coexistence/migration.
- Post-lifespan experiment audit reads remain supported by per-operation file connections.
- Filesystem sandbox could not run the first API test attempt; it was stopped, then the
  same tests completed with normal local process permissions. No product workaround added.
- All review scope decisions and deployment limits are recorded in ../../backend-reliability.md.
- Delivery target: backend-reliability; main and experience-v2-integration remain unchanged.
