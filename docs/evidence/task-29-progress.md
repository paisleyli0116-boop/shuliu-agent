# Task 29 progress — Persistent ModelCall / Cost / Budget

**Status: IN_PROGRESS. Task 29 is not accepted. Task 30 has not started.**

## Scope and dependency check

The approved Implementation Plan defines Task 29 as durable Cloud PostgreSQL storage for ModelCall metadata, actual cost, and atomic budget reservations / settlement. The feature depends on Task 05's migration and PostgreSQL persistence foundation (`69b1ca6`) and Task 15's Model Gateway ports (`72f4915`); both commits are in the current branch history.

The plan names migration `0005_model_usage_budget.py`, but the current published chain already uses revisions `0005_worker_coordinator`, `0006_worker_authentication`, and `0007_task_scoped_fencing_tokens`. To preserve applied migration history, this implementation adds `0008_model_usage_budget.py` after `0007`; earlier revisions are unchanged.

## Work recorded so far

- Added organization-scoped `model_calls`, `model_budget_accounts`, and `model_budget_reservations` schema. Model call rows hold route, token counts, cost, latency, safe error code, status, Trace ID, and timestamp; they contain no prompt, completion, input, output, or Secret columns.
- Added durable SQLAlchemy adapters in `runtimes/python-worker/shuliu_runtime/models/postgres_usage.py`. Call records and settlements are idempotent by organization and call identity. Budget accounts require an explicit per-call reservation amount; reservation checks lock the account row. Unknown provider outcomes move reservations to `RECONCILIATION_REQUIRED` and retain the reserved amount. Known not-sent failures may release the reservation.
- Added `call_id` to the Gateway request/call path and budget operations. Unknown request outcomes stop fallback so they cannot silently cause a second Provider call. Model usage fields are restricted to LiteLLM's normalized token counters. The current cost unit is USD; other currencies are rejected until explicit conversion support exists.
- Added the Task 29 PostgreSQL persistence/concurrency integration tests and wired them into the PostgreSQL 17/18 CI matrix. The local environment has no `SHULIU_DATABASE_URL`, so these real-PostgreSQL tests have not run locally.

## Test evidence

- RED: new migration contract failed because the three model usage tables did not exist; GREEN: the focused migration test passed after revision `0008` was added.
- RED: adapter tests initially failed because `postgres_usage` and stable `ModelCall.call_id` did not exist. RED also reproduced unknown-outcome fallback invoking both providers. GREEN: focused adapter/Gateway tests passed; the fallback regression now proves only the first Provider is invoked and its reservation remains for reconciliation.
- RED: usage-field tests showed arbitrary Provider-supplied metadata keys were accepted. GREEN: the two focused usage tests passed after an allowlist was applied in both Gateway validation and persistence.
- RED: budget-currency and ModelCall-status schema checks failed. GREEN: USD-only account configuration and a database status check passed both focused tests.
- RED: the CI contract test showed PG17/18 did not invoke the new integration file. GREEN: the focused repository-layout contract passed after adding the step.
- Python Model Gateway / Provider tests: **106 passed**.
- `pnpm test:migration`: **16 passed, 1 skipped**. The skipped test requires a configured PostgreSQL test database.
- `uv run --locked --package shuliu-cloud-api pytest tests/integration/test_model_usage_persistence.py -q`: **2 skipped** because `SHULIU_DATABASE_URL` is not configured.
- `uv lock --check --offline`: passed.
- A broad `pnpm test:integration` invocation was interrupted after it began selecting already-validated browser tests; it exited 1 on interruption and is not a passing integration result. No browser test was skipped or reported as passed by this Task 29 run.

## Current repository checkpoint

- Branch: `feat/foundation-v0.1`.
- HEAD at checkpoint: `2a905aeda52eba4f3246c3949a480d56d7076ae4` (Task 28 evidence-document-only commit, local and not pushed).
- Task 29 implementation changes are uncommitted. The pre-existing untracked `scripts/` directory was left untouched and excluded from Task 29 changes.
- No commit or push has been made for Task 29.

## Remaining acceptance work

1. Run the Task 29-specific security and type/syntax checks, inspect the complete diff, and complete an independent review.
2. Obtain real PostgreSQL 17 and 18 evidence for migration, restart persistence, concurrent reservation, duplicate settlement, organization isolation, and unknown-outcome retention. The PG17/18 CI step is configured but has not run for these changes.
3. Resolve any failures from those checks, then commit only the Task 29 files, push the feature branch without force, and verify the matching full-SHA CI results before declaring Task 29 complete.

No Provider request, DeepSeek call, database-backed external service, or production action was performed in this work.

## 2026-10-25 — Review and local verification checkpoint

The worktree was snapshotted before this review to `%TEMP%\shuliu-task29-workspace-snapshot-0163a0be2bb14d10b33221f951eff04c`. The saved tracked patch SHA-256 is `25A27E41E373771F795B1FE718030124B30186F61D6787CB52D42C82B7C12925`; the snapshot contains copies of all untracked files. `scripts/dev-env.ps1` remains unmodified (snapshot SHA-256 `497318CA2E230CABD7BC2A1CB5D2CC2F9D9508FD101A72050F8D95AA7CAEE52C`).

### Review findings and fixes

- A new RED regression injected a failure while writing `model_calls` after Gateway success. It confirmed a split-transaction defect: the database charged USD `0.01234567` and released the reservation while no ModelCall row existed. GREEN: the PostgreSQL budget adapter now finalizes the reservation and writes the ModelCall in one transaction; failure rolls both changes back. PostgreSQL ModelCall inserts use conflict-safe idempotency and validate the existing row. The Gateway uses the atomic finalizer when the persistent budget adapter provides it.
- A new migration rollback/re-upgrade test was RED with `MigrationStateError: Alembic revision history and checksum ledger do not match`: after downgrade, the checksum ledger retained revision 0008. The migration recorder now removes checksums for reverted revisions and safely returns when downgrading past the ledger table. GREEN: downgrade to 0007 and re-upgrade to head passed.
- Review found a duplicate `sqlalchemy==2.1.5` declaration in the runtime package manifest. The duplicate was removed; the uniqueness check and `uv lock --check --offline` pass.
- The PG17/18 integration test now concurrently records the same ModelCall identity to exercise PostgreSQL conflict handling. This PostgreSQL-only concurrency assertion has not run locally.

### Latest local evidence

- `uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/models -q`: **107 passed**.
- `uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/models/test_persistent_model_calls.py runtimes/python-worker/tests/models/test_persistent_budget.py -q`: **8 passed** after the atomicity fix.
- `pnpm test:migration`: **17 passed, 1 skipped**. The skipped configured-PostgreSQL migration case could not run without `SHULIU_DATABASE_URL`.
- The focused migration downgrade/re-upgrade test: **1 passed**.
- `uv run --locked --all-packages pytest tests/integration/test_model_usage_persistence.py -q`: **2 skipped** because no PostgreSQL service, Docker/Podman, client tools, or `SHULIU_DATABASE_URL` is available locally. These are skipped, not passed.
- `pnpm test`: **24 package tests and 70 contract tests passed**.
- `pnpm typecheck`: passed.
- `pnpm exec vitest run tests/contract/repository-layout.test.ts`: **35 passed**.
- `uv lock --check --offline`: passed. Python syntax parsing passed for nine changed Python files. CI YAML parsing confirms a PostgreSQL `[17, 18]` matrix and the direct Task 29 integration-test command.
- An earlier `pnpm test:integration` was interrupted when it selected already-validated real-browser tests; that invocation exited due to interruption and is not a pass. The non-integration `pnpm test` was subsequently completed successfully; the targeted Task 29 PostgreSQL file was run separately and reported its two local skips.

### Git and remaining acceptance status

- Read-only remote verification succeeded: local branch and `origin/feat/foundation-v0.1` both point to `2a905aeda52eba4f3246c3949a480d56d7076ae4`; it is a child of `ec3a02b26c6c9850441ad83003a93ddf59c4b391`. No Task 29 commit has been made yet.
- The pre-existing untracked `scripts/dev-env.ps1` is excluded from all Task 29 changes and must remain untouched.
- Task 29 remains **IN_PROGRESS / not accepted**. Required next evidence is actual PostgreSQL 17 and 18 CI execution for this implementation, followed by exact-SHA CI review and final commit/push verification. No skipped local PG result is represented as a pass. Task 30 has not started.

## 2026-10-25 — Final local regression after second-pass review

- A second RED regression showed that when an unknown Provider outcome was followed by an atomic ModelCall write failure, the transaction rolled back safely but left the reservation at `RESERVED` instead of attempting reconciliation. GREEN: Gateway now makes a bounded best-effort reconciliation transition and records a safe UNKNOWN attempt if storage permits; it never retries the Provider call. If the database remains unavailable, the reserved amount remains held.
- Final Model Gateway / Provider suite: **108 passed**.
- Persistent ModelCall and budget subset: **9 passed**.
- Direct Task 29 PostgreSQL integration test rerun: **2 skipped** locally, still awaiting PG17/18 CI.
- No repository commit or push has been made for Task 29 at this checkpoint; Task 30 remains unstarted.
- The separate Task 28 evidence correction was committed locally as `7d7debc` after verifying that remote `feat/foundation-v0.1` still pointed to `2a905aeda52eba4f3246c3949a480d56d7076ae4`; Task 29 changes are staged separately.
