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

## 2026-10-25 — Exact-SHA remote CI tracking

- Task 29 implementation commit: `04922fa6a09bf3b9269d8b1d98beafe0d4e7f2bf` (`feat/models: persist model usage and budget accounting`). At the last successful post-push remote read, `origin/feat/foundation-v0.1` matched this full SHA. The current local HEAD and local remote-tracking ref also match it; a fresh live remote read could not be completed in this checkpoint.
- An exact-SHA workflow lookup previously returned three runs for this commit, each initially `in_progress`: Generic CI `40167416068` (https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/40167416068), Windows Validation `40167416038` (https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/40167416038), and Browser Runtime diagnostics `40167416053` (https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/40167416053). These are initial observed states, not final conclusions.
- Subsequent read-only job-detail requests returned GitHub HTTP 404 for all three runs. `gh auth status` now reports that the saved GitHub account is not authenticated; repository metadata returns HTTP 401. The browser session opened to the exact run URL displayed “Page not found”. HTTPS `git ls-remote` could not obtain credentials. A one-time SSH-over-443 read was also stopped by host-key verification because the configured `known_hosts` file could not be read; host-key checks were not bypassed.
- No workflow was re-run or newly triggered. Final run conclusions, job names/results, and PostgreSQL 17/18 integration results remain **NOT VERIFIED**. No skipped local PostgreSQL test is counted as passed.
- Local branch: `feat/foundation-v0.1`; local HEAD: `04922fa6a09bf3b9269d8b1d98beafe0d4e7f2bf`; local `origin/feat/foundation-v0.1` tracking ref: same SHA. Worktree at this check had only the pre-existing untracked `scripts/dev-env.ps1`, preserved and excluded from commits.
- Task 29 remains **IN_PROGRESS / not accepted**. Required next step is restore authorized GitHub read access (normal GitHub/GCM sign-in; no token sharing), then inspect the already-created runs by exact SHA, including the PG17 and PG18 integration jobs. Do not start Task 30 before the plan’s acceptance evidence is complete.

## 2026-10-25 — Exact-SHA remote CI verification (read-only, gh CLI, auth restored)

`gh auth status`: logged in as `paisleyli0116-boop` (keyring), scopes `gist, read:org, repo, workflow` — sufficient for Actions reads. No token output.

### Runs for commit `04922fa6a09bf3b9269d8b1d98beafe0d4e7f2bf` (all completed)

| Run ID | Workflow | Conclusion | Link |
|---|---|---|---|
| `40167416038` | Windows Validation | **success** | https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/40167416038 |
| `40167416068` | Generic CI | **failure** | https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/40167416068 |
| `40167416053` | Browser Runtime cross-platform diagnostics | **failure** | https://github.com/paisleyli0116-boop/shuliu-agent/actions/runs/40167416053 |

### Windows Validation `40167416038` — PASSED (34/34 steps success)

Full chain executed for real, including Electron↔Python IPC with real subprocesses, PyInstaller Sidecar build, NSIS packaging, signature verify, install smoke, upgrade/failed-runtime Safe Mode/rollback/data preservation.

### Generic CI `40167416068` — FAILED; **PG17/PG18 integration tests did NOT execute**

- **PostgreSQL migration (PG 17) and (PG 18)**: pytest aborted during **collection**, before running any test: `collected 598 items / 1 error / 580 deselected / 18 selected` → `ModuleNotFoundError: No module named 'win32gui'` while collecting `tests/e2e/windows/test_interactive_session.py` on Linux runners → `Interrupted: 1 error during collection`. **0 of the 18 selected tests ran — neither passed nor skipped.** The Task 29 PostgreSQL 17/18 evidence is therefore still NOT obtained.
- **Root cause of the collection error is pre-existing, not Task 29**: `tests/e2e/windows/test_interactive_session.py` was added on 10-22 by `9f73b5f` ("test(windows): diagnose interactive fixture startup", another session) and imports Windows-only `win32gui` without a platform guard; Generic CI runs it on `ubuntu-latest`/`macos-latest`. `04922fa..7d7debc` contains only Task 29 implementation + Task 28 evidence docs.
- **Generic tests (ubuntu-latest) and (macos-latest)**: `7 failed, 42 passed, 8 skipped` — all 7 failures are `tests/skills/platform/test_windows_sandbox.py` Windows-only tests (ctypes has no `WinDLL` on Linux/macOS), again a missing platform guard, pre-existing pattern.

### Browser Runtime `40167416053` — FAILED (environment, pre-existing pattern)

- `ubuntu-latest`: `BROWSER_START_START_BROWSER_FAILED` (stage `start_browser`, upstream error). `macos-latest`: `BROWSER_TIMEOUT` (stage `navigate_goto`, 69s) plus the known artifact-quota error on evidence upload. Both jobs failed identically on the 10-22 baseline `f9675f8` (before Task 29); the same browser suite **passed** on the real Windows Chrome runner inside Windows Validation `40167416038`.

### Verdict for Task 29 acceptance

- **Passed (verified)**: Windows Validation on exact SHA `04922fa` — all gates green.
- **Passed (local, per prior checkpoints)**: Model Gateway/Provider 108; persistent subset 9; migration 17 passed / 1 skipped.
- **Not obtained**: real PostgreSQL 17 and 18 execution of `tests/integration/test_model_usage_persistence.py` and migration `0008_model_usage_budget` — blocked by the pre-existing Linux collection error in PG jobs, **not** by Task 29 code.
- **Unverifiable here / pre-existing**: Linux/macOS browser smoke and Windows-only sandbox tests running on non-Windows runners.
- **Still outstanding before Task 29 acceptance**: make the PG17/18 CI jobs actually run the Task 29 PostgreSQL integration tests (guard `tests/e2e/windows/*` collection to Windows, or fix `pnpm test:migration` selection), then re-verify by exact SHA. No re-run was triggered in this verification pass; no files committed, pushed, or cleaned; `scripts/dev-env.ps1` untouched.

## 2026-10-25 — CI failure fixes applied (platform guards + browser launch options)

Fixes for every failure class found in the exact-SHA verification, none of which were Task 29 regressions:

1. **PG17/18 collection crash (blocker for Task 29 evidence)**: `tests/e2e/windows/test_interactive_session.py` now calls `pytest.skip(..., allow_module_level=True)` *before* importing `win32gui`/`win32process`, so non-Windows runners skip the module instead of aborting the whole pytest collection. Windows collection unchanged.
2. **Windows-only sandbox tests on Linux/macOS**: the seven `tests/skills/platform/test_windows_sandbox.py` tests that fake `ctypes.WinDLL` (Windows Job Object semantics) got the same `@pytest.mark.skipif(sys.platform != "win32", reason="Windows runner only")` guard the file already used elsewhere. Local Windows run: 14 passed.
3. **Browser startup/hang on hosted runners**: `driver.py` gained `launch_options()` — Chromium sandbox stays ON by default; setting `SHULIU_BROWSER_ALLOW_NO_SANDBOX=1` (explicit per-environment opt-in) disables it and adds `--no-sandbox`, `--disable-setuid-sandbox`, `--disable-dev-shm-usage` for runners without the SUID helper. `backend.py` forwards the flag through the whitelisted driver environment only when the host set it. The browser workflow sets it for the hosted-runner matrix and its evidence upload is now `continue-on-error` (artifact quota). TDD: 3 focused tests (RED→GREEN) in `tests/browser/test_driver_launch_options.py`; full local browser suite 64 passed on Windows (sandboxed path unchanged).

Local verification: skills 55 passed/2 skipped; repository-layout contract 35 passed; browser 64 passed.

## 2026-10-25 — Task 29 accepted (PASSED)

All plan acceptance criteria are met on exact SHAs:

- **Real PostgreSQL 17 and 18 evidence**: `PostgreSQL migration (PG 17)` and `(PG 18)` CI jobs executed the Task 29 migration and integration tests against real PostgreSQL services and passed (runs `40170136452` @ `63e397f`, `40170959775` @ `749bb35`, both success).
- **Exact-SHA CI review**: `749bb35` (contains Task 29 implementation `04922fa` plus the platform-guard fixes) — Generic CI **success** (4/4 jobs) and Windows Validation **success** (full build/install/sign/upgrade/rollback chain).
- **Local suites**: Model Gateway/Provider 108, persistent subset 9, migration 17 passed/1 skipped (local skip by design), repository-layout contract 35, browser 64, typecheck/build passed.
- **Out of Task 29 scope, tracked separately**: the browser cross-platform *diagnostics* workflow (Task 24's smoke on hosted ubuntu/macos runners) still fails on those runners; `7431dfd` adds raw driver-stderr capture into run logs for the next diagnosis pass. Its CI execution is pending a GitHub Actions infrastructure incident (jobs failed with zero steps executed; retries scheduled). This workflow is not a Task 29 acceptance gate.

**Conclusion: Task 29 PASSED. Task 30 may start.**
