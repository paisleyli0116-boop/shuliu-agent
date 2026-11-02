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

## 发布状态更新（2026-11-01）

- Task 34 实现提交 `5af33e49db2072b2a67c74be710cfd609f2abc83` 已作为 Task 33 提交 `a519fc5a4e3522d6da3914231a6ce4ce8d00fe89` 的子提交，推送到 `origin/feat/foundation-v0.1`；推送后的远端 SHA 已只读核对为 `5af33e49db2072b2a67c74be710cfd609f2abc83`。
- 本次 Python recovery 与进程恢复定向测试为 **37 passed**。这是本地 Windows 证据，不是 GitHub CI 结果。
- GitHub Actions 结果尚未核实。生产 IPC / Workflow 接线、Worker fencing、真实 PostgreSQL 17/18 及 Task 33 前置验收仍阻塞；Task 34 继续 **IN_PROGRESS / acceptance BLOCKED**，Task 35 未开始。

## GitHub CI 验证结果与行为决策（2026-11-01，真实运行）

- 推送 HEAD：`30ad778`（`feat/foundation-v0.1`）。
- Generic CI run `41310851081`：**success**（PG17 22/22、PG18 22/22、macOS 36/36、Ubuntu 36/36）。
- Windows Validation run `41310851060`：**success**，35/35 步，其中包含 `Run Desktop UI, IPC, and security unit tests` 与 `Run Browser Runtime and real Windows Chrome integration tests`。
- 首次真实 CI 运行暴露并已修复的三个缺陷：
  1. IPC 契约测试在 macOS 上得到 `safe_mode` / `RUNTIME_SAFE_MODE` 而不是 `healthy` / `SIDECAR_EXITED`。原因：`evaluateRecovery` 在**未配置 recovery coordinator**时直接返回 `SAFE_MODE`，使任何一次崩溃都直接进入安全模式（`supervisor.ts`）。已改为无 coordinator 时返回 `RESUME`（提交 `60b0b12`）。
  2. `runtime-crash-recovery` 测试在 Windows 上得到 `startupStage = sidecar_exit` 而不是 `recovery`。原因：`completeRecovery` 的 SAFE_MODE 分支调用 `terminateChild` 后，子进程退出处理器异步把 stage 改写为 `sidecar_exit`。现在在子进程关闭后重新断言 `recovery`（提交 `54fd9a0`）。
  3. Browser 真实 Chrome 测试 `test_real_page_navigation_after_click_is_verified` 报 `KeyError: 'observation'`。根因在 Task 33 的 Broker：本意是只为持久化保存脱敏投影，却把投影直接赋给了返回值（`output = safe_output`），丢掉了 `observation`。现在首次执行返回完整输出，仅 durable 记录使用投影（提交 `39c4262`）。
- **需要 owner 确认的设计决策：** 点位 1 的改动偏离了本文件先前「missing recovery configuration keeps the runtime in Safe Mode」的写法。理由：生产 `apps/desktop/src/main/index.ts` 目前并未实例化 `RuntimeRecoveryCoordinator`，若按原设计，生产应用任何一次 Sidecar 崩溃都会永久停在 Safe Mode，且既有 IPC 契约（崩溃后重启恢复 healthy）无法成立。当前语义为：**未配置 coordinator → 普通重启（无 checkpoint 恢复）；配置 coordinator → 恢复/等待/安全模式按策略执行**。若要恢复「无配置即 Safe Mode」，必须同时把 coordinator 接入生产 main 并更新契约测试。
- 仍然阻塞 Task 34 验收的项（均未因 CI 转绿而解决）：
  - Electron Main 仍未实例化 recovery coordinator，Python 恢复 IPC 通道未接入生产；
  - Worker generation / lease fencing 仍是协议与测试夹具层面的证明，未走真实 Workflow → Tool Broker → repository 路径；
  - crash-before-checkpoint 与 Tool-dispatched-before-ACK 两个窗口未在生产链路证明；
  - 无生产安装包恢复与平台矩阵证据。

## Owner 批准（2026-11-02）

- 所有者已确认上方「需要 owner 确认的设计决策」：**同意**「未配置 coordinator → 普通重启；配置 coordinator → 按策略恢复/等待/安全模式」的语义，放弃「无配置即 Safe Mode」的原写法。
- 该决策已由 Windows 实机验收（IPC 契约 2/2、崩溃恢复 48/48、真实桌面冒烟）佐证。
- 本任务剩余阻塞不变：生产 main 接入 RecoveryCoordinator、fencing 走真实链路、两个崩溃窗口的生产链路证明与安装包矩阵证据。
