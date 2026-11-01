# Task 34 — Full Process Crash Recovery

- **Status:** IN_PROGRESS / acceptance BLOCKED
- **Date:** 2026-11-01
- **Worktree:** `D:\Documents\ChatGPT\数流\task33-durable-idempotency`
- **Branch / HEAD before Task 34 commit:** `codex/task33-durable-idempotency` / `a519fc5a4e3522d6da3914231a6ce4ce8d00fe89`
- **Remote:** Task 33 and Task 34 commits are being prepared for the authorized feature-branch push; CI is not yet verified.

Task 34 implementation is local and remains acceptance BLOCKED. Task 33 remains BLOCKED; its implementation is now in local parent commit a519fc5a4e3522d6da3914231a6ce4ce8d00fe89. Neither task has remote CI evidence yet. Task 34 acceptance remains blocked by the Task 33 dependency and the runtime composition gaps below.

## Scope completed in this checkpoint

- Added a bounded restart policy with validated restart count, per-restart backoff and health timeout.
- The Electron Sidecar Supervisor now waits for configured backoff and does not expose a restarted Sidecar until its recovery port returns the exact `RESUME` status. `WAIT`, `SAFE_MODE`, `DEAD_LETTER`, malformed results, missing recovery configuration and recovery exceptions keep the runtime in Safe Mode. Restart count remains bounded.
- Added Python `ProcessCrashRecovery`: the previous runtime generation must be fenced before checkpoint reconciliation; a failed fence enters Safe Mode without calling the reconciler. Recovery decisions append idempotent metadata-only process crash events to the SQLite outbox, correlated with the checkpoint trace. Checkpoint schema incompatibility is delegated to the canonical reconciler so the specific safe error is preserved.
- Added real subprocess tests that kill a Python process after a durable side effect, then start a second process to read SQLite state. Confirmed-applied state advances the checkpoint without repeating the effect; unknown state waits; an unconfirmed fence enters Safe Mode. A separate subprocess test confirms an incompatible checkpoint schema enters Safe Mode after process exit.

These integration fixtures execute separate OS processes and durable SQLite state. They do not constitute a production Workflow runtime, a real external service reconciliation, or a production Worker lease adapter.

## TDD and verification evidence

- **RED — Desktop restart behavior:** the new test observed a replacement Sidecar start after only **41–43 ms** despite a configured 500 ms backoff, and the Supervisor exposed `ready` without recovery approval. Both assertions failed before the implementation.
- **RED — crash audit:** the process-crash event test failed because `CheckpointStore.record_process_crash` was missing. Its first implementation exposed a repeated-event timestamp conflict; the test caught it and the event insert was made idempotent by stable event identity and metadata comparison.
- **RED — checkpoint schema error:** the new test observed `CHECKPOINT_UNAVAILABLE` where the persisted schema mismatch required `CHECKPOINT_SCHEMA_INCOMPATIBLE`. The process recovery layer now delegates that case to the canonical reconciler.
- **GREEN — Desktop Supervisor tests:** **12 passed** across `runtime-crash-recovery.test.ts` and `sidecar-supervisor-diagnostics.test.ts`.
- **GREEN — Python recovery + actual process integration:** **29 passed** across both new integration files and the Python recovery test directory.
- **GREEN — adjacent workflow / persistence regressions:** **31 passed** across Workflow tests, SQLite idempotency restart and SQLite migration tests.
- **GREEN — complete Desktop IPC suite:** **42 passed** across 10 test files.
- **GREEN — repository security tests:** **10 passed** across 4 files.
- **GREEN — Desktop typecheck and production build:** both passed.
- **GREEN — Python compileall and `git diff --check`:** both exited 0.

Commands were run on Windows. These are local results, not GitHub Actions results.

## Acceptance blockers and unrun evidence

1. `WorkflowRepository` is currently only a protocol; repository search found no production implementation. `CheckpointStore` and `RecoveryCoordinator` are currently instantiated only by tests, not the Sidecar bootstrap or workflow runtime. The Workflow Engine's run/step state therefore is not durably checkpointed by the running product.
2. No production adapter currently implements `PreviousProcessFence` or persists runtime generations / Worker lease fencing. The new coordinator requires this proof and fails closed if it is absent.
3. `apps/desktop/src/main/index.ts` does not yet construct a `RuntimeRecoveryCoordinator` or connect it to a Python recovery IPC method. Consequently, production restart attempts without recovery wiring enter Safe Mode; this preserves the security boundary but does not restore product work.
4. Task 33 is still BLOCKED, including durable store production composition-root wiring and real PostgreSQL 17/18 evidence. Process-level recovery cannot be accepted until durable idempotency and fencing are composed with the actual Tool path.
5. No Windows GitHub Actions, Linux/macOS subprocess matrix, PostgreSQL integration run, installer health run, `pnpm test:integration`, or root `pnpm test:ipc` run was performed in this checkpoint. The local Python subprocess tests use a SQLite fixture; the PG tests and actual platform CI remain unverified.
6. The independent read-only review is pending.

No CI or remote acceptance result is implied by the local test evidence. Task 34 remains **BLOCKED** until the real runtime/repository/fencing composition, required platform process tests, independent review and corresponding CI evidence are complete. Task 35 has not started.

## Follow-up review and hardening — 2026-10-26

An independent read-only review identified these concrete issues; targeted tests reproduced them before changes:

- Mutating the caller-owned restart policy changed the supervisor budget from one restart to ten and changed the configured delay to zero. `RuntimeRecoveryCoordinator` now snapshots and freezes both the policy object and its backoff list.
- A recovery callback could remain pending indefinitely. It now has a bounded timeout using the configured health timeout and receives an `AbortSignal`; timeout returns Safe Mode. A callback that tries to return `RESUME` after timeout is not accepted.
- The supervisor previously collapsed `WAIT` into terminal Safe Mode and killed the replacement process. It now retains the Sidecar in an explicit `waiting` state, exposes degraded health with `RUNTIME_RECOVERY_WAITING`, denies normal requests while waiting, and exposes a guarded single-flight `resumePendingRecovery()` for a later approval/reconciliation signal. This entry point is not wired to a production approval event yet.
- Closing during an in-flight recovery callback could be overwritten by a late Safe Mode result. Recovery cancellation is tied to `close()`, and the recovery completion checks process identity and intentional stop before changing state.
- Process-crash audit entries previously accepted arbitrary non-empty status and organization / trace identifiers. The event writer now accepts only the four recovery statuses and bounded safe identifier syntax; the process recovery port rejects unsafe organization identifiers before fencing or audit writes.
- Added a real subprocess test that terminates a process with an approval-pending checkpoint and reconstructs it in another process; the result remains `WAIT`, with no external-state inspection or side effect.

**RED evidence:** policy budget mutation test observed 10 instead of 1; WAIT test observed `safe_mode` instead of `waiting`; a stalled recovery remained `restarting` after its 1-second test deadline; close-race test observed `safe_mode` instead of `stopped`; all three unsafe audit inputs (unknown status, path-like organization ID, newline trace ID) were accepted before validation.

**GREEN evidence:** Desktop crash-recovery and supervisor diagnostics tests **15 passed** before the additional diagnostic assertion; final complete Desktop IPC suite **46 passed**. Python recovery and process integration suite **34 passed**, including the approval-pending process exit case. Security suite **10 passed**. Desktop typecheck and production build passed. `compileall` for Python recovery passed. These are Windows local results.

## Independent review blockers still open

- Production Electron Main still constructs `SidecarSupervisor` without a recovery coordinator. No production `runtime.process_recovery` IPC handler or Python `ProcessCrashRecovery` composition exists, so a production restart safely reaches Safe Mode rather than restoring a workflow.
- `resumePendingRecovery()` is a component entry point only; no approval-change event invokes it in the running product.
- The Python process tests use controlled subprocess fixtures, SQLite, test external-state readers and test fences. They do not exercise the real `WorkflowEngine` → `ToolBroker` → production repository/lease adapter path, nor do they prove durable Worker lease fencing.
- The planned crash-before-checkpoint and Tool-dispatched-before-ACK fault windows are not yet covered by the current process fixtures.
- Task 33 remains BLOCKED on production durable-store composition and PostgreSQL 17/18 evidence. Task 34 therefore cannot meet its dependency or release gate in this worktree.
- The read-only follow-up review of the fixes is pending. GitHub Windows Validation, root IPC/integration workflows, platform subprocess matrix and installed-runtime evidence were not run.

Task 34 remains **IN_PROGRESS / acceptance BLOCKED**. Task 35 has not started. No commit or push was made; all pre-existing Task 33 changes and untracked files remain in the worktree.

## Additional review findings and verification — 2026-10-26

The next read-only review found two locally fixable gaps and two limits that remain blockers:

- `CheckpointStore.record_process_crash()` validated organization and trace identifiers but accepted a path-like `run_id` when called directly. A new parameterized case first failed with “DID NOT RAISE ValueError”; the sink now validates `run_id` with the same bounded safe identifier rule. The process-crash audit cases are **5 passed**.
- The same sink also accepted any uppercase `error_code`, which could put caller-controlled text into the audit outbox. A synthetic sensitive marker was accepted in the RED run; the sink now accepts only the recovery module's explicit error-code set. The expanded crash-audit cases are **6 passed**.
- The Desktop recovery adapter reduced timeout, invalid result, callback exception, and domain recovery decisions to a generic `RUNTIME_RECOVERY_REQUIRED`. The RED run of `runtime-crash-recovery.test.ts` had **2 failures**: the WAIT health response lost `APPROVAL_PENDING`, and timeout was reported as `RUNTIME_RECOVERY_REQUIRED` instead of `RUNTIME_RECOVERY_TIMEOUT`. Recovery now returns a structured `{status, safeErrorCode}`, accepts only allowlisted recovery error codes, and preserves the safe code through Supervisor health, denied requests, and diagnostics. Callback exceptions, malformed codes, and late `RESUME` results fail closed. The runtime crash-recovery test file is **8 passed**.
- The timeout test now uses a callback that ignores its abort signal and resolves late with `RESUME`. The Supervisor remains in Safe Mode and reports `RUNTIME_RECOVERY_TIMEOUT`. This proves a late callback result cannot restore the Sidecar. It does **not** prove that arbitrary side effects inside a non-cooperative recovery callback are cancelled; production recovery must use the abortable IPC port and remain fail-closed until that composition is implemented and tested.
- Direct crash-audit writes now reject path-like run identifiers as well as invalid organization or trace identifiers. No raw exception text is included in the Desktop recovery outcome.

**Current local verification:** complete Desktop IPC suite **48 passed**; Python recovery plus both process-recovery integration modules **36 passed**; security suite **10 passed**; Desktop typecheck, production build, recovery `compileall`, and `git diff --check` all exited **0**. These are local Windows results. One concurrent verification pass had `recoveryAborted=false` in the timeout test, so it did not establish that the callback entered the recovery stage. The fixture used the same 50 ms policy value for IPC handshake and recovery timeout. It now gives the handshake a separate explicit 2-second limit and asserts a successful handshake plus `startupStage=recovery` before checking timeout behavior; the targeted crash-recovery file then passed **8/8**, followed by the full IPC suite **48/48**. The recovery callback itself retains the 50 ms test deadline. The original failed run did not capture its stage, so that failure's exact stage remains unconfirmed. No external Provider request was made.

**Still blocking acceptance:** Task 33 remains blocked on production durable-store composition and PostgreSQL 17/18 evidence. Electron Main still does not instantiate this recovery coordinator or expose the Python recovery IPC path; Worker generation / lease fencing remains a protocol/test fixture, and the crash-before-checkpoint and Tool-dispatched-before-ACK windows have not been proven through the real Workflow → Tool Broker → repository path. Missing or schema-incompatible checkpoint cases also cannot currently produce a correlated crash event because no validated organization/trace identity is available from the checkpoint. No Windows GitHub Validation, platform process matrix, production Sidecar recovery, installer recovery, or independent review of this follow-up patch has been completed. Task 34 remains **IN_PROGRESS / acceptance BLOCKED**; Task 35 has not started. No commit or push was made.

## Same-generation approval retry audit fix — 2026-10-26

The independent review's remaining locally reproducible P2 was confirmed with an end-to-end recovery test using the SQLite checkpoint store and `ProcessCrashRecovery`: the first `WAIT / APPROVAL_PENDING` was audited, an approval-completed checkpoint was saved, and a retry for the same generation pair incorrectly returned `SAFE_MODE / RECOVERY_AUDIT_UNAVAILABLE`. The cause was the process-crash outbox idempotency key being fixed only to run and generation IDs while the event payload also included the changing decision.

The idempotency key now also includes the validated recovery status and allowlisted error code. Repeating the same decision remains idempotent; a later decision for the same run and generation pair is preserved as a separate append-only audit event. No exception text, workflow state, request content, or secret is added to the audit record.

- **RED:** the new `test_process_recovery_can_resume_after_approval_for_same_generation_pair` failed with `RECOVERY_AUDIT_UNAVAILABLE` on the second recovery attempt.
- **GREEN:** checkpoint recovery plus process restart recovery tests: **26 passed**.
- **Related regression:** all recovery tests and both process recovery integration modules: **37 passed**.
- No external Provider request, commit, push, remote CI, production approval IPC, or production fencing adapter was performed in this follow-up.

Task 34 remains **IN_PROGRESS / acceptance BLOCKED**. The production composition, actual worker fencing and tool-path crash windows, Task 33 PostgreSQL acceptance, platform matrix, and Windows CI blockers listed above remain open. Task 35 has not started.
