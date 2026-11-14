# V3 Evaluation and Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reproducible V3 experience ablations and compare context-aware retrieval against the previous behavior.

**Architecture:** A separate V3 runner reuses production agents, API persistence and isolated workspaces. Shared representation/query helpers let single and multi agents use the same experience controls. An immutable reference snapshot and per-trial output databases prevent evaluation feedback.

**Tech Stack:** Existing Python, pytest, SQLite, FastAPI, Maven; no added dependency.

**Spec:** `docs/superpowers/specs/2026-11-14-v3-evaluation-retrieval-design.md`

## Global Constraints

- Preserve unrelated local presentation/output changes and existing benchmark sources.
- Never write to the reference database; explicit live mode only; no credential output.
- Three representations: none/full/compact. Two retrieval strategies: baseline/contextual.
- Strict original final tests, no skipped/zero-test successes, frozen references and isolated trial outputs.
- User requested direct execution; implement in the existing non-main development checkout, without additional approval gates or automatic push/merge.

## Review Focus

1. Existing SQLite reference file must not be initialized or migrated while taking a snapshot.
2. Evaluation-source experiences, including published provenance, must not leak into reference selection.
3. Missing usage, failed requests and incomplete trials must not become apparent savings or successes.
4. Compact rules must retain every applicability condition and issue, including case-sensitive identifiers.
5. Baseline ranking must remain reproducible after contextual ranking is introduced.

### Task 1: V3 plan, references and summary contracts

**Files:** create `scripts/v3_evaluation.py`, `backend/tests/test_experiment_v3.py`.

**Interfaces:** `build_plan(case_ids, repetitions, modes, retrievals)` returns uniquely identified balanced trial dictionaries; `summarize(trials)` includes all completed outcomes; immutable snapshot helper returns SHA-256.

- [x] Write failing tests for 54 trials, balanced condition positions, invalid inputs, missing token coverage, paired comparison and read-only snapshot preservation.
- [x] Run targeted tests; confirm expected failures.
- [x] Implement these contracts and rerun targeted tests.

### Task 2: Runtime controls and V3 runner

**Files:** create `scripts/experiment_v3.py`; modify `backend/experience/policy.py`, `backend/agent/core.py`, `backend/agent/orchestrator.py`; test `backend/tests/test_experiment_v3.py`, `backend/tests/test_experience_policy.py`.

**Interfaces:** Agent and coordinator accept `experience_format='full'`, `retrieval_strategy='contextual'`. `to_model_reference(entry, representation='full')` preserves rule semantics. Runner accepts immutable references, explicit conditions, modes and model factory for deterministic tests.

- [x] Write failing tests for compact representation, dry-run side effects, cross-repository source checks, single/multi trial wiring, failure accounting and frozen-reference isolation.
- [x] Implement runner with atomic incremental JSON and readable report; test real API/tools using scripted client fixtures.
- [x] Keep legacy experiment and retrieval tests green.

### Task 3: Target cases and contextual retrieval

**Files:** create `benchmarks/evaluation-v3/` catalog and four independent Java/Python fixtures; create `backend/experience/retrieval.py`; modify shared policy and both agents; extend relevant tests.

**Interfaces:** `build_retrieval_query(task_description, results)` returns bounded task/failure evidence; contextual ranking activates for nonempty query, baseline always sends empty query.

- [x] Write failing tests for identifier/Chinese relevance, latest test evidence, exact-rule deduplication and unchanged hard filters.
- [x] Implement bounded queries, tokenization and conservative ranking, then rerun tests.
- [x] Verify each new original benchmark fails its fixed test suite and known-correct fixtures pass without modifying originals.

### Task 4: Integration, documentation and evidence

**Files:** `docs/experiments-v3.md`, `README.md`, run reports under `.cache/`.

- [x] Run targeted and full backend suites, frontend contract checks and dry-run CLI.
- [x] Inspect existing configured model/reference availability using redacted metadata; perform an appropriately bounded live pilot if available and record actual outcomes.
- [x] Document experiment commands, interpretation limits, immutable inputs and reproduction details.
- [x] Obtain a fresh final code review, fix material findings with regression tests, and report remaining limitations.

Live pilot status: two 6-task batches failed authentication and remain recorded in `docs/reports/v3-live-pilot-2026-11-14.md`. After the user supplied a working replacement credential, the official read-only probe passed and all 6 conditions on the Python inventory target passed 12 original tests each. This authenticated run made 37 successful model requests and used 213,326 tokens. Frozen references, original sources, runtime code and the production database remained unchanged. See `docs/reports/v3-live-results-2026-11-14.md`: one case with one repetition does not establish retrieval gains; compact representation did not reduce total tokens in this run. Previously completed offline verification: 606 passed, 1 skipped; frontend contract checks passed; independent review finding fixed.
