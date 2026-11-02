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

## 独立核实：恢复器只读幂等检查增量（2026-11-02）

Codex 在隔离副本 `D:/Documents/ChatGPT/数流/task34-production-recovery-827fdda`（分支 `codex/task34-production-recovery`，基线 `827fdda`，9 文件，未提交未推送）完成的只读幂等检查增量，已由本会话独立核实：

- **代码审查结论**：`IdempotencyExternalStateReader` 全程只读，不释放 claim、不派发 Tool；组织范围强制取自持久化 checkpoint 而非 Tool 自带字段（`reconciler.py` 的 `trusted_action` 改写）；仅 `ABSENT` 判 NOT_APPLIED，`CLAIMED/DISPATCHED/INDETERMINATE/CONFLICT` 与一切异常均判 `UNKNOWN→WAIT`；`COMPLETED` 必须 `SUCCEEDED + VERIFIED` 且输出再次过白名单（`sanitize_idempotency_tool_output`）才判 APPLIED。SQLite 与 PostgreSQL 两个 inspect 实现语义一致（digest 冲突→CONFLICT，非 COMPLETED 行 result_json 非空→抛错）。无确定性缺陷。
- **审查员对 ABSENT 的保留意见确认有效**：`ABSENT→未执行` 的安全解释依赖两个前提——旧进程已被真实 fencing（不可能继续运行并在事后派发），以及派发状态在外部操作之前已持久记录。这两个前提目前都只在测试夹具层面成立（见下方生产接线阻塞），生产链路接线前 `ABSENT` 的语义不能视为已证明。
- **测试复跑（本会话执行）**：recovery + tools + persistence + 两个 PG 集成模块共 **137 passed、5 skipped**（skip 均为无 `SHULIU_DATABASE_URL` 的 PG 门控），超出报告声明的 61/4 范围，全部通过。
- 生产接线缺口不变：生产 Workflow Repository、Electron Main → RecoveryCoordinator、Sidecar 生产恢复 IPC、真实 Worker lease/generation fencing。补齐前 Task 34 保持 **IN_PROGRESS / BLOCKED**。

## Task 33 幂等状态与崩溃恢复桥接增量（2026-11-02）

本轮在隔离工作副本 `D:\Documents\ChatGPT\数流\task34-production-recovery-827fdda`（分支 `codex/task34-production-recovery`，起始 HEAD `827fddabc1859a58a1bbf7f8280b92dd04bd638d`）实现 Task 34 的一个受限增量；没有修改主 checkout、`main`、Task 33 工作副本或其他会话文件。

- 为 SQLite 与 PostgreSQL 幂等存储增加只读 `inspect` 能力和状态记录类型。读取不会创建、释放、锁定或推进 claim；摘要冲突不返回既有结果。
- 新增 `IdempotencyExternalStateReader`，只在记录缺失时报告 `NOT_APPLIED`；`CLAIMED`、`DISPATCHED`、`INDETERMINATE`、冲突、格式错误、存储错误及未验证的完成结果均映射为 `UNKNOWN`。只有已完成、已验证且含安全引用的结果可作为 `APPLIED`。该读取器不派发工具、不自动重试，也不释放 claim。
- RecoveryCoordinator 将组织范围取自已验证 checkpoint，覆盖 pending action 中不可信的组织字段。新增进程恢复测试确认派发后状态未知会 `WAIT / SIDE_EFFECT_STATE_UNKNOWN` 且不推进 checkpoint；已验证完成结果可推进 checkpoint，而不重放副作用。
- 独立白名单复投影测试先以 RED 复现了恢复输出将携带额外 `private_payload` 的问题；修复后读取器在写回 checkpoint 前再次调用幂等结果安全投影。
- **定向验证：61 passed、4 skipped**。命令覆盖 Python recovery、SQLite 幂等重启、durable idempotency 与 PostgreSQL 幂等集成测试。4 项 PostgreSQL 集成用例因本机未配置 `SHULIU_DATABASE_URL` 而按测试门控跳过；PG17/18 结果仍只能引用之前 run `41310851081`，不能作为本轮新代码的 CI 证据。另有 1 条旧 Browser Runtime 临时目录清理 `WinError 5` 警告；测试进程退出码为 0，未影响断言结果。
- 对所有改动 Python 文件执行 `compileall`，退出码 0；`git diff --check` 退出码 0。本仓库未发现可用的本地 `ruff` 命令，因此本轮未声称通过 Ruff。
- RED/GREEN 增量：恢复输出重投影测试先失败（多出 `private_payload`），最小修复后该测试 **1 passed**；随后的相关回归为 **61 passed / 4 skipped**。

**仍然不能验收 Task 34：** 此读取桥接尚未连接到生产 Electron Main / Sidecar / Workflow→ToolBroker 执行组合根，也没有生产 Worker lease/generation fencing。crash-before-checkpoint、dispatch-before-ACK 的真实生产路径故障注入未完成。PostgreSQL 变更测试在本机 skipped，本轮没有新的 GitHub Windows Validation、PG CI、安装包恢复验证或独立审查结果。Task 34 继续 **IN_PROGRESS / acceptance BLOCKED**；Task 35 未开始。

## 生产恢复接线与 Windows 本地验收（2026-11-02）

本节记录从基线 `b342a974dae8e6c45cfaae965a895593d39c7a0a` 继续完成的未提交增量。用户提供的基线 CI 证据为 Generic CI run `41385069236` 与 Windows Validation run `41385069133`，两者目标 SHA 均为 `b342a97` 且成功；这些结果不验证本节的新增改动。

- **Workflow 持久化组合根：** `bootstrap_runtime()` 现在实例化并返回 SQLite `WorkflowRepository`；`WorkflowEngine` 使用该 Repository 的本地测试覆盖创建、事件 outbox 原子提交、进程重开恢复、重复创建和 revision 冲突。这里验证的是组合根返回的持久化 Repository，不代表 Sidecar 已暴露 Workflow 执行 API，也不代表生产 ToolBroker 已接入 Workflow Engine。
- **Electron Main → RecoveryCoordinator：** Main 构造 `RuntimeRecoveryCoordinator` 并启用 `recoverOnStart`。初次启动和 supervisor 重启各自生成唯一 transition ID；Recovery bridge 通过 Sidecar 查询检查点并提交恢复决议。等待与 Safe Mode 结果保持 fail-closed。
- **Sidecar 生产恢复 IPC：** Sidecar 通过正式 handler 注册 `runtime.recovery.begin`、`runtime.recovery.checkpoints` 与 `runtime.recovery.recover_run`。SQLite 保存单调 runtime generation / transition；恢复决议使用 Task 33 的只读 durable idempotency 状态读取器。持久化 Safe Mode 会在冷启动时恢复，并在处理普通工作请求前拒绝请求。按用户级数据目录持有 singleton lock，避免第二个 Sidecar 并发服务同一数据库；本轮 RED 测试确认旧顺序会先 bootstrap/migrate 再拿锁，修复后启动锁在数据库 bootstrap 之前取得。
- **Worker lease / fencing：** Cloud 的 `lease_authorize` 路由在数据库事务中重新核验 Worker、task、lease、expiry、心跳和当前 fencing token；Python `WorkerWebSocketConnection.dispatch_tool()` 在 Broker 调用前要求匹配的 Cloud permit 并再次检查有效期，且将 lease 身份及 generation 传入 Broker context。Cloud 对旧 generation / 过期 lease 的拒绝与 Python 侧 Broker 未被调用均有测试。当前仓库没有 Worker job runner 调用 `dispatch_tool()`；因此这些接线尚未证明生产任务执行器实际走该入口，生产 Worker→Tool 的完整执行路径仍为阻塞项。
- **故障注入：** Windows Python 子进程测试真实启动 Sidecar、写入 dispatched checkpoint / idempotency 状态后强制终止，再以新 Sidecar 进程恢复。结果为 `WAIT / SIDE_EFFECT_STATE_UNKNOWN`，checkpoint 序号不前移、状态仍为 `DISPATCHED`，没有重复派发。该用例使用人工写入的 durable 状态，不是在实际 Workflow→ToolBroker 执行链的 dispatch/ACK 窗口杀进程。另验证 persisted Safe Mode 冷启动拒绝 Provider handler，以及同一数据目录第二个 Sidecar 被 singleton lock 拒绝、首个退出后第三个可启动。

**本机定向测试实际结果（Windows）：**

- Python recovery / IPC / workflow / worker / persistence：**118 passed**（包含启动锁先于数据库迁移的顺序回归）。
- Desktop IPC：**52 passed**；Desktop typecheck、生产构建通过。
- Cloud Worker tests：**24 passed、2 skipped**；跳过项分别要求 `SHULIU_DATABASE_URL` 的 PostgreSQL 并发与 Worker Auth 集成测试。
- Worker 协议 contract：**18 passed**；Security suite：**10 passed**。
- 根 TypeScript typecheck、Python `compileall`、`git diff --check`：通过。
- 根 `pnpm test:contract` **未通过/未完成**：`event-roundtrip` 子测试无法由 `uvx` 在受限网络下解析锁定的 `uv==0.12.20`（真实错误为 cache miss 且 network disabled）；该全量命令曾因 uvx 等待而中断。本地已安装的全局 `uv` 版本为 `0.12.20`，但本节没有把手动替代命令记作该 Contract suite 通过。
- Windows Electron IPC tests 通过；当前环境未进行真实安装版 Electron Main 启动。此前记录的 AppContainer ACL 限制仍适用，本轮没有修改 ACL。Linux/macOS、本地 PG17/18、安装包重启/升级验证均未在本轮执行。

**独立审查前状态：** 工作树位于 `codex/task34-production-recovery`，HEAD 仍为 `b342a974dae8e6c45cfaae965a895593d39c7a0a`；以上实现与证据均未提交、未推送。工作树 `origin` 指向本机 `C:\Dev\shuliu-agent`，不是可直接用于 GitHub 对账的 URL；未对该本地 remote 执行 push。Task 28–33 文件未因本轮测试而清理；本轮代码仅在隔离 Task34 worktree。最新 SHA 的 GitHub CI 尚无证据。

**结论：Task 34 仍为 IN_PROGRESS / acceptance BLOCKED。** 生产 Main→Sidecar 恢复和 Sidecar 恢复 IPC 已接线；持久 Workflow Repository 已在生产 bootstrap 构造，但 Workflow 执行/ToolBroker 组合根尚未接入；Worker 的 Cloud lease 校验及 Python Broker 前置 adapter 已实现，尚无生产 Worker runner 调用点。真实 Workflow→ToolBroker 进程故障注入、Worker 执行链路、完整 contract suite、安装版 Windows 验收以及本轮 SHA 的远端 CI 与独立审查仍待完成。Task 35 未开始。

## Safe Mode 受控恢复补齐（2026-11-02）

上一轮独立审查指出持久 Safe Mode 没有退出路径。本轮按 RED→GREEN 补上仅供 Main 启动恢复流程使用的受控出口：Sidecar 在 Safe Mode 时继续拒绝普通能力，只放行 generation begin、checkpoint 查询、recover_run 和 recovery.complete；Main 仅在当前代际确认有效、所有 checkpoint 都得到 RESUME、SQLite `quick_check` 成功且迁移版本完整时发送 complete。Sidecar 在同一事务写入 `RUNTIME_SAFE_MODE_RELEASED` outbox 事件并清除已恢复 run 的 Safe Mode 标记，然后才将健康状态切为 healthy。未恢复的 marker、未知副作用、等待审批、无效 checkpoint、旧 transition、数据库不健康或在 release 事务准入检查前被观察到的取消均保持 Safe Mode；准入检查通过后的迟到取消由原子 release 提交胜出。

- **RED：** Windows 真实 Sidecar 子进程测试先复现冷启动 Safe Mode 下 `runtime.recovery.begin` 被拒绝，恢复状态没有到达生产 coordinator。
- **GREEN：** 新路径用两个真实 Sidecar 子进程建立并切换 generation；未完成 checkpoint 恢复时 `complete` 返回 `SAFE_MODE_RELEASE_NOT_READY` 且 marker 仍存在；当前代际成功恢复后，marker 清除、outbox 审计事件存在、health 转为 healthy。随后 Provider 测试只返回 `MODEL_CONNECTION_TEST_DISABLED` 且 `request_status=not_sent`，未触发任何外部请求。定向用例 **1 passed**。
- **本轮回归（Windows 本地）：** Python recovery / IPC / workflow / worker / persistence **119 passed**；Desktop IPC **52 passed**；Cloud Worker **24 passed、2 skipped**（PostgreSQL 并发与 Worker Auth 两项需要 `SHULIU_DATABASE_URL`）；worker protocol contract + security **28 passed**；Desktop 与根 TypeScript typecheck、Desktop production build、Python compileall、`git diff --check` 均通过。
- 用户给出的 PG17/18 Generic CI `41385069236` 和 Windows Validation `41385069133` 均针对基线 SHA `b342a974dae8e6c45cfaae965a895593d39c7a0a`，不验证本轮未提交改动。当前 HEAD 仍为该基线，本轮工作未提交、未推送、没有对应 GitHub Actions 运行。

**独立审查复核后的未关闭阻塞：** Safe Mode 不再因“没有任何受控退出路径”而永久锁死，但整项 Task 34 仍不能验收。生产 bootstrap 目前持久化构造 `SQLiteWorkflowRepository`，但全仓没有生产 `WorkflowEngine` 实例或 Sidecar workflow 执行 IPC；Recovery 扫描的是独立 `CheckpointStore`，没有从 `workflow_runs` 恢复实际 WorkflowRun。Worker lease authorize / Python `dispatch_tool()` 已各自实现并有本地/测试证据，但仓库没有 Worker job runner 调用该方法，且目前 permit 未把批准的 tool/参数范围绑定到 dispatch。现有子进程故障测试仍预置 checkpoint 和 DISPATCHED 状态后杀进程，没有从 WorkflowEngine → ToolBroker → durable idempotency 的真实 dispatch/ACK 窗口注入故障。

本轮也没有启动已安装 Electron Desktop；Windows 实机证据限于真实 Sidecar 子进程和 Electron IPC 单元测试。完整 contract suite 先前因 `uvx` 在受限网络缺少锁定工具缓存而未完成，尚未复跑；本轮没有安装包恢复、跨平台 subprocess 矩阵、PG17/18 当前 SHA 或远端 Windows CI 证据。独立审查员已复核上一轮差异并指出上述阻塞；本轮 Safe Mode 修复等待再次只读复审。Task 34 维持 **IN_PROGRESS / acceptance BLOCKED**，Task 35 未开始。

## Safe Mode 释放与迟到取消竞态（2026-11-02）

独立只读复审确认：`runtime.recovery.complete` 在入口检查取消后，会在 SQLite 事务中同时追加 release audit 并删除 Safe Mode marker。若 IPC 取消在该提交期间或提交后到达，旧版 `IpcServer.respond()` 会丢弃已经提交的成功结果、返回 `IPC_REQUEST_CANCELLED`，并保持进程内 `_safe_mode=True`。这造成持久 marker 已删除、进程仍拒绝普通请求的状态不一致；复审未发现普通请求因此提前放行。

- **RED：** `test_safe_mode_release_commits_before_a_late_ipc_cancellation` 通过 handler 在成功 release 返回前置位取消事件，复现旧 server 把成功结果改成取消错误（定向测试 **1 failed**，响应缺少 `result`）。
- **GREEN（初始版本，后由复审补充）：** `IpcServer.respond()` 将 `runtime.recovery.complete` 返回白名单成功状态作为 durable commit 点。该实现最初只在 handler 入口检查取消，没有覆盖入口检查后、事务准入前的取消；下方“Safe Mode 事务准入取消边界”记录了复审发现与修复。准入检查通过后的迟到取消仍由原子 release 提交胜出，其他 IPC 取消行为未改变。
- **定向结果（Windows 本地）：** 取消竞态与 SQLite IPC release 定向测试通过；Python IPC / recovery / workflow / worker / persistence **123 passed**；Desktop IPC **52 passed**；Cloud Worker **24 passed、2 skipped**（需本地 `SHULIU_DATABASE_URL` 的 PostgreSQL 用例）。`compileall` 与 `git diff --check` 通过。
- **RED/GREEN 证据（初始版本，入口后准入前的窗口由下方复审补测）：** 第一个定向测试先以 **1 failed** 复现 server 丢弃成功结果，再在修复后通过。随后新增真实 SQLite recovery handler + `IpcServer` 测试，在 release transaction 执行 marker `DELETE`、事务尚未返回提交时注入取消；实际事务提交后，marker 已清除、release outbox 事件存在、IPC 返回 `RESUME` 且内存 health 为 healthy。既有真实 Sidecar subprocess 测试另验证进程重启后的 marker/outbox/health 状态。本轮仍未在已安装 Electron Desktop 中验证取消时序。
- **远端状态：** 基线 `b342a974dae8e6c45cfaae965a895593d39c7a0a` 的 CI 绿色结果不覆盖本轮差异。本轮未产生提交或新 SHA，未推送，也没有对应 Windows Validation。
- **独立复审：** 复审确认了初始迟到取消竞态，并未发现普通 IPC Safe Mode bypass；复审后又发现事务开始前的窗口，详见后续记录。

**Task 34 仍为 IN_PROGRESS / acceptance BLOCKED。** 生产 `WorkflowEngine` / ToolBroker 组合根和 WorkflowRun 恢复缺失；Worker job runner 没有调用 fencing dispatcher，且授权 permit 尚未绑定获批 Tool/参数/可信 actor scope；进程故障注入仍未覆盖真实 dispatch→ACK 窗口。还缺当前差异的新 SHA Windows CI、安装版 Windows 恢复验收及最终独立复审。Task 35 未开始。

## Workflow 对未知 Tool 结果的等待状态（2026-11-02）

新增 RED 测试发现：`WorkflowEngine._run_tool_step()` 原先只把 approval 未消费映射为 `WAITING`；来自 durable Tool Broker 的 `RECONCILIATION_REQUIRED`（已派发、结果未知）和 `TOOL_EXECUTION_UNKNOWN`（执行器异常、状态未知）会落入普通失败终态。修复后这两种错误进入 `WAITING / EXTERNAL`，保存原错误码和单次 resume token。恢复后继续使用同一个 Workflow step idempotency key；不会生成新的副作用标识。测试用 Fake Broker 模拟外部状态已完成后的返回，因此它验证 Workflow 状态和 key 连续性，不代表真实 Tool Broker/外部副作用已完成端到端测试。

- **RED：** 参数化测试针对两个安全错误码均复现 `FAILED` 而非 `WAITING`（**2 failed**）。
- **GREEN：** 两个用例均进入 `WAITING / EXTERNAL`；显式 resume 后使用相同幂等键并完成（**2 passed**）。
- **相关回归：** Python IPC / recovery / workflow / worker / persistence **123 passed**。

这项状态映射改善了未知副作用的恢复处理，但没有补上生产 WorkflowEngine/ToolBroker 组合根，也没有证明 WorkflowRun 可从数据库自动重建。Task 34 保持 **IN_PROGRESS / acceptance BLOCKED**。

## Safe Mode 事务准入取消边界（2026-11-02）

独立复审发现：`runtime.recovery.complete` 的入口取消检查与 SQLite `BEGIN IMMEDIATE` 之间还会读取 checkpoint、Safe Mode 状态、`quick_check` 和 migration 记录。此前取消若在入口检查后、事务开始前到达，release 仍会清除 marker 并写入 outbox。此前提交期间迟到取消测试只在 marker `DELETE` 时置位，未覆盖该事务准入窗口。

- **RED：** 新测试用 SQLite trace 在 `BEGIN IMMEDIATE` 执行前置位 `IpcServer` 当前请求的真实取消事件。旧实现仍返回 `RESUME`，导致持久 marker 被清除且 release event 被写入；测试以 **1 failed** 复现。
- **GREEN：** `SQLiteSafeModeController.release_after_verified_recovery()` 在取得 `BEGIN IMMEDIATE` 写事务后立即检查请求取消标记。取消已到达时不读取或删除 Safe Mode marker、不写 release outbox；handler 保持 Safe Mode，IPC 返回 `IPC_REQUEST_CANCELLED`。这次准入检查通过后，事务成为 release 的线性化阶段：后续迟到取消仍由 marker 删除与 audit outbox 的原子提交胜出，避免持久状态和进程内 health 分裂。
- **定向验证：** `test_recovery_release_rejects_cancel_at_transaction_admission`、`test_recovery_release_commit_wins_over_cancel_during_sqlite_transaction` 与 `tests/ipc/test_real_process_recovery.py` 一起运行，**6 passed in 1.79s**（Windows）。此前 Python IPC / recovery / workflow / worker / persistence **123 passed** 来自一次聚合命令，在前面两个小节重复引用，并非两次或可累加结果，也不包含本次新增准入测试。
- **受影响回归（Windows）：** `.venv\Scripts\python.exe -m pytest -q runtimes/python-worker/tests/ipc runtimes/python-worker/tests/recovery runtimes/python-worker/tests/workflow runtimes/python-worker/tests/worker runtimes/python-worker/tests/persistence`，**124 passed in 3.77s**。同轮 `compileall -q runtimes/python-worker/shuliu_runtime` 退出码 0，`git diff --check` 退出码 0（仅有 Markdown 文件换行格式提示）。
- **独立复审：** 复审者确认本次限定范围内的准入取消缺口已关闭，未发现由此导致的 Safe Mode 绕过或 marker / outbox / health 状态分裂。复审为只读且没有独立重跑测试；124 项回归结果仍以本机命令输出为证。当前差异仍未提交、未推送，没有新 SHA 的 Windows Validation 或 PG CI。

**Task 34 仍为 IN_PROGRESS / acceptance BLOCKED。** 生产 Workflow Engine / ToolBroker 组合根与 WorkflowRun 恢复、Worker job runner 调用 fencing dispatcher 且 permit 绑定授权范围、真实 Workflow→ToolBroker dispatch/ACK 窗口故障注入、完整 contract suite、安装版 Windows 验收及当前差异的 CI 仍未完成。Task 35 未开始。

## 未结束 Workflow 缺少 Checkpoint 时的 fail-closed 保护（2026-11-02）

复核代码发现一个可复现缺口：生产 recovery `begin` 原先只用 `CheckpointStore.latest_all()` 判断是否有上一代状态；SQLite `workflow_runs` 中若存在 `RUNNING` / `WAITING` 记录但没有 checkpoint，会被当作空白首次启动而允许建立新 generation。`complete` 也只核对 checkpoint 与既有 Safe Mode marker，因此该 run 可能被遗漏并让 Sidecar 报 healthy。IpcServer 还没有在 recovery handler 返回 `SAFE_MODE` 后更新当前进程的内存请求门禁。

- **RED：** 新增 `test_unfinished_workflow_without_checkpoint_keeps_sidecar_in_safe_mode`，通过生产 `SQLiteWorkflowRepository` 保存一个真实非终态 WorkflowRun（没有 checkpoint），再走 Sidecar Recovery IPC。旧代码返回 `RESUME`，测试以 **1 failed** 复现。
- **GREEN：** `SQLiteWorkflowRepository.list_unfinished()` 读取并验证持久化的 `RUNNING` / `WAITING` runs；recovery begin 在 generation 变更前检查每个非终态 run 均有 checkpoint，否则为该 run 持久化 `CHECKPOINT_UNAVAILABLE` Safe Mode marker 并返回 `CHECKPOINT_NOT_FOUND`。Recovery complete 再次确认所有非终态 runs 都属于当前 transition 已恢复集合。IpcServer 在 recovery 返回 `WAIT` / `SAFE_MODE` / `DEAD_LETTER` 时，将当前进程同步切到 Safe Mode，拒绝后续普通 IPC。定向测试 **1 passed**。
- **相关 Windows 本地回归：** `pytest runtimes/python-worker/tests/ipc runtimes/python-worker/tests/recovery runtimes/python-worker/tests/workflow runtimes/python-worker/tests/worker runtimes/python-worker/tests/persistence`，**125 passed in 4.15s**；Python `compileall` 退出码 0；`git diff --check` 退出码 0（仅有 evidence Markdown LF→CRLF 提示）。
- **独立复核：** 尚待本轮代码冻结后的只读复审；本节记录的是实现与本机测试输出，不构成审查结论。
- **未覆盖事项：** 这项保护防止漏掉没有 checkpoint 的未结束 WorkflowRun 并误报健康，但不会自动恢复该 run。实际生产 WorkflowEngine / ToolBroker 组合根、Sidecar workflow 执行入口及恢复已有 WorkflowRun 的能力仍不存在。当前 Worker WebSocket / lease authorize 与 Python `dispatch_tool()` 没有生产 job runner 串接，授权 permit 尚未绑定获批的 tool、参数摘要和可信 actor scope。现有进程故障注入仍是预置 checkpoint / durable idempotency 状态后重启，没有从真实 WorkflowEngine → ToolBroker dispatch 到 ACK 窗口杀进程。本轮未运行安装版 Desktop、未生成新 SHA、未提交或推送、也没有本轮差异对应的 GitHub CI。

**Task 34 仍为 IN_PROGRESS / acceptance BLOCKED。** 本地回归不替代计划要求的真实执行链路、故障注入、Windows 安装版验收、独立复审及新 SHA CI。Task 35 未开始。

## 启动恢复门禁回归收口（2026-11-02）

新增的启动安全门要求 Sidecar 在 Main 完成恢复协议前维持 `safe_mode`。第一次相关回归运行中，两个既有进程测试仍断言 Sidecar 一启动就为 `healthy`，因此失败；这反映测试没有执行生产启动所需的恢复握手。已更新测试：崩溃恢复场景先完成空 checkpoint 的初始恢复后才断言健康；实例锁场景则只验证新 Sidecar 在 Safe Mode 下持锁，并且不会因 Safe Mode 释放实例锁。产品健康门槛没有降低。

- **定向回归：** `test_forced_sidecar_restart_recovers_dispatched_action_without_replay` 与 `test_second_sidecar_for_same_user_data_fails_closed_until_first_exits`：**2 passed**。
- **受影响 Python 回归：** Windows 本机运行 IPC、Recovery、Workflow、Worker、Persistence 测试目录，**127 passed in 3.25s**。
- **Desktop 验证：** `pnpm --filter @shuliu/desktop test:ipc`：**52 passed**；`pnpm --filter @shuliu/desktop typecheck`：退出码 0。
- Python `compileall` 退出码 0；`git diff --check` 退出码 0（仅显示 evidence Markdown 的 LF/CRLF 提示）。
- 此次变更尚未提交或推送。基线 `b342a974dae8e6c45cfaae965a895593d39c7a0a` 的既有 CI 不覆盖当前差异；当前差异没有新 SHA 或 GitHub CI 结果。
- 本节的状态门和测试更新仍待独立审查者核验。

**Task 34 继续 IN_PROGRESS / acceptance BLOCKED。** 当前实现仍未接上生产 WorkflowEngine→ToolBroker 执行组合根与 WorkflowRun 自动重建；Worker job runner 尚未调用 fencing dispatcher，且授权 permit 未绑定获批工具、参数摘要与可信 actor scope；故障注入未在真实 Workflow→ToolBroker dispatch/ACK 窗口杀进程；未完成安装版 Windows 验收和当前差异的新 SHA CI。Task 35 未开始。

## Worker runner fencing 与真实 dispatch 崩溃窗口（2026-11-02）

本节对应用户给出的 `24699b5` 基线后的本地未提交增量。用户提供的 Windows Validation `41419030117`（目标 `24699b5`）和 Generic CI `41415811633`（目标 `7d62d9a`）均不覆盖本节改动；Generic CI `41427409240` 也是针对 `24699b5`，不能作为本节代码的 CI 证据。本轮没有重新触发或重跑这些工作流。

- **Worker 执行入口：** 新增 `WorkerTaskRunner`，一次处理一个 Cloud lease；只接受严格的 `{tool_id, arguments}` 任务动作，拒绝任务携带 actor、organization、context 或 permissions；执行身份和组织来自已认证连接。runner 必须通过 `WorkerWebSocketConnection.dispatch_tool()`，由 Cloud 再核验当前 Worker、lease、expiry 和 fencing token，然后才进入 ToolBroker。Cloud permit 同时绑定任务动作的 canonical SHA-256 摘要；任务动作与 Worker 实际提交的 Tool 或参数不一致时 fail-closed。成功和 ACK 均按 task / lease / idempotency key 对账；ACK 状态未知时不重发 Tool 或结果。
- **Worker Runner 测试：** Cloud worker harness 中使用真实 `WorkerTaskRunner`、真实 `WorkerToolDispatcher`、真实 Cloud `WorkerCoordinator` / lease repository 测试层、真实 `ToolBroker` 与 `PolicyEngine`；一次执行通过 fencing 并收到匹配 ACK，任务状态转为 `SUCCEEDED`。此外，扩展 `tests/integration/test_worker_wss_cloud_loop.py`：在本机启动 TLS WSS Cloud API，真实完成 Worker 注册、认证、lease request、动作摘要 fencing、ToolBroker/Policy 执行及 Cloud result ACK；单次副作用后 Cloud 任务状态为 `SUCCEEDED`。该真实网络链路测试使用临时 SQLite Cloud DB，而不是 PostgreSQL；PG 并发 fencing 仍未在本机执行。
- **Worker dispatch→ACK 故障注入：** Windows 子进程实际执行 `WorkerTaskRunner → WorkerToolDispatcher → ToolBroker → SQLiteIdempotencyStore → Executor`。Executor 写入一次外部效果标记后，测试用 `Popen.kill()` 终止子进程，发生点在 Broker/Worker 尚未收到结果 ACK 之前。持久幂等状态保持 `DISPATCHED`；重启后再次尝试由真实 ToolBroker 返回 `RECONCILIATION_REQUIRED`，没有第二次执行 Executor，效果标记仍只有一条。Cloud authorizer 与连接在这个特定崩溃夹具中是受控替身，所以它证明的是 runner 到本地 durable Broker 的进程崩溃防重放；不证明真实 Cloud 已提交 ACK 后 ACK 包丢失，也不证明真实 WSS 断线恢复。
- **Workflow dispatch→ACK 故障注入：** 另一个 Windows 子进程通过真实 `WorkflowEngine → SQLiteWorkflowRepository → ToolBroker → SQLiteIdempotencyStore → Executor` 开始 Workflow；在外部效果标记已写、Workflow/Tool ACK 尚未持久完成时强制终止。重启检查到未完成 run 缺少可恢复 checkpoint，进入 `SAFE_MODE / CHECKPOINT_NOT_FOUND`；按同一幂等键尝试恢复时被 `RECONCILIATION_REQUIRED` 拒绝，副作用未重放。此场景是真实本地 Workflow/Broker/SQLite 进程故障注入，不是生产 Sidecar 的 Workflow IPC 调用。
- **Protocol compatibility：** 新增组织 ID 若作为 Worker v1 `authenticated` 必填字段会破坏既有 Worker / Cloud 兼容。兼容测试先因旧版 `{worker_id}` 响应不符合 schema 而 RED；修复后该字段在 v1 保持可选。新版 Cloud 仍返回组织 ID；若旧 Cloud 未返回，握手仍兼容，但 `WorkerTaskRunner` 因没有可信组织身份在 dispatch 前 fail-closed。`action_digest` 在 Worker v1 的 lease authorization schema 中也保持可选以允许旧帧解析；Cloud 对缺少摘要的授权请求返回 `WORKER_LEASE_ACTION_BINDING_REQUIRED`，新 Worker 对旧 Cloud 未回显摘要的 permit 返回 `WORKER_LEASE_AUTHORIZATION_INVALID`，两条路径都不能进入 ToolBroker。缺摘要 WSS 场景先以通用错误码失败断言（RED），修改后明确错误码通过。
- **TDD 证据：** WorkerTaskRunner 测试先因 runner 模块不存在而在收集阶段失败；实现后相关 runner 与 fencing 测试通过。动作摘要接入前 Cloud lease fencing 测试因 authorizer 签名缺少摘要而失败，加入摘要契约及服务器端校验后通过。新 Worker crash 测试第一次运行暴露的是测试连接未消费 runner 注入的 clock 参数（`TypeError`，未进入 Tool 执行）；修正测试夹具后同一测试 **1 passed**。这次夹具修正不是产品缺陷修复。
- **本轮 Windows 定向回归：** `pytest runtimes/python-worker/tests/ipc runtimes/python-worker/tests/recovery runtimes/python-worker/tests/workflow runtimes/python-worker/tests/worker runtimes/python-worker/tests/persistence services/cloud-api/tests/workers -q`：在新增 v1 兼容与缺少组织 ID fail-closed 测试前 **158 passed, 2 skipped in 4.48s**；`tests/integration/test_worker_wss_cloud_loop.py` **1 passed**；兼容测试 RED→GREEN 后 **1 passed**。两个原回归 skip 是 `test_postgres_worker_registration_and_session_replay_protection` 与 `test_postgres_serializes_task_claim_and_fencing_token_increments`，原因均为未配置 `SHULIU_DATABASE_URL`。Worker protocol contract：**18 passed**。Python `compileall`、worker protocol schema JSON 解析、`git diff --check` 均通过（完整最终增量的复跑结果见下方收口记录）。
- **未接生产的部分：** 静态检查确认 `bootstrap_runtime()` 只创建持久 Workflow Repository；Sidecar `server.py` 没有生产 Workflow 执行 IPC；生产代码未构造 WorkflowEngine + ToolBroker。全仓生产路径也没有一个可注入该执行器的 Worker 进程入口；`WorkerTaskRunner` 目前由测试直接实例化。当前没有生产身份解析 / Policy / Approval / ToolRegistry 组合根可安全用于 Workflow 执行。没有这些可信依赖时开放由 Renderer 或 Sidecar 请求直接提供定义、actor、权限的执行入口会造成授权边界缺口，因此本轮没有伪造接线。
- **差异与交付：** 当前分支为 `codex/task34-production-recovery`，HEAD 仍是 `24699b5dd03707e283a7d65afe613261076cdcbd`；仅 Task34 worker protocol / runner / fencing / 测试文件及本证据文档有改动，无其他未跟踪文件混入。本轮未提交、未推送，也没有当前差异对应的 GitHub CI、安装版 Desktop 验收或独立审查结果。

**Task 34 继续 IN_PROGRESS / acceptance BLOCKED。** Worker runner 的本地 fencing 调用和两个本地 durable dispatch 崩溃场景有 Windows 证据；真实 Workflow→ToolBroker 生产组合根、生产 Worker 进程调用入口、PostgreSQL fencing、Cloud ACK 提交后应答丢失场景、当前差异的独立复审和 SHA 对应 CI 仍未完成。Task 35 未开始。

### 本地收口复跑（2026-11-02）

- 最终相关 Windows 回归将 IPC、recovery、workflow、worker、persistence、Cloud Worker 与真实本地 TLS WSS Worker 集成测试放在同一轮执行：**163 passed, 2 skipped in 5.17s**。两个 skip 均因没有 `SHULIU_DATABASE_URL`，分别是 PostgreSQL Worker 注册/会话重放测试和 PostgreSQL 并发 lease fencing 测试。
- Worker protocol Vitest contract：**18 passed**；Python `compileall`、Worker schema JSON 解析、`git diff --check` 退出码均为 0。
- 本轮实现没有提交或推送，HEAD 仍为 `24699b5dd03707e283a7d65afe613261076cdcbd`。用户提供的 CI 只验证该基线或更早 SHA，不能覆盖本地差异；本轮没有对应 CI，也没有独立只读审查。
- Task 34 保持 **IN_PROGRESS / acceptance BLOCKED**：生产 Workflow→ToolBroker 组合根与 Sidecar 执行 IPC 缺少可信身份、Policy、Approval 和注册工具配置；没有生产 Worker host 调用 runner；Cloud PostgreSQL 并发 fencing、Cloud 已提交结果但 ACK 丢失的故障注入、当前差异审查及 SHA 对应 CI 尚未完成。Task 35 未开始。

## 真实 WSS Worker 子进程故障注入补充（2026-11-02）

本轮继续验证可安全独立完成的 dispatch 崩溃场景，没有修改产品执行策略，也没有提交或推送。

- 新增 `tests/integration/test_worker_wss_dispatch_process_crash.py`：本机启动带临时 TLS 证书的真实 Cloud WSS 服务和独立 Windows Worker 子进程。子进程使用真实 `WorkerWebSocketConnection`、Cloud 注册/认证、lease、动作摘要 fencing、`WorkerTaskRunner`、`ToolBroker`、Policy 与 SQLite durable idempotency。Executor 写入一次外部效果标记后阻塞；测试对 Worker 子进程调用 `Process.terminate()` 模拟进程突然终止。Cloud lease 超时后经 `OUTCOME_UNKNOWN` reconciliation 进入 `RECONCILIATION_REQUIRED / INDETERMINATE`。Worker 经设备密钥安全续期会话后重启并尝试领取同一 task，Cloud 返回 `TASK_NOT_READY`；效果标记仍只有一条。
- **定向验收：** 两个真实 WSS 子进程故障注入（副作用开始后强制终止；Cloud 已持久化成功结果但 ACK 被网络层丢弃）与既有 WSS 成功、Cloud ACK 幂等、Worker runner / fencing 测试合并运行 **15 passed**。其后两个故障用例顺序重复 **3 轮，每轮 2 passed，共 6/6 passed**。真实 ACK 丢失场景确认 Worker 返回 `WORKER_RESULT_ACK_UNKNOWN`，Cloud task 为 `SUCCEEDED`、lease 为 `COMPLETED`、Cloud 仅有一条结果事件、副作用标记仅一条；重启 Worker 时任务不能再次领取。另一场景在副作用执行后强制终止 Worker，Cloud 将结果未知 lease 标记为 `INDETERMINATE` 并阻止任务重领。运行使用真实本地 TLS/WSS、Windows 子进程和 SQLite；不是 PostgreSQL 或生产 Cloud 环境证据。
- 试验中出现的首轮失败是测试夹具问题：未导入 `multiprocessing.dummy`、Worker 与 Cloud 测试时钟不一致，以及进程重启后一次性 session credential 已被消费。已通过使用 `threading.Thread`、注入同一测试时钟、并通过设备密钥续期短期会话修正；没有延长产品超时或放宽断言。
- **生产 Worker host 状态：** WSS 集成现在从独立进程真实执行 `WorkerTaskRunner` 并经过 Cloud lease fencing → ToolBroker → Cloud result ACK；静态入口核对仍未找到已部署/可启动的 Worker 服务命令或生产 ToolBroker 注册组合根。当前验证在测试中构造生产类，不是已安装 Worker 服务实机运行。
- **Workflow→ToolBroker 状态：** `ipc/bootstrap.py` 持久化构造 `SQLiteWorkflowRepository`；`ipc/server.py` 只注册 Provider 与 Recovery handlers；Desktop task / approval 服务仍由 `createDemoDesktopServices()` 提供，Workflow UI 明确标示执行接口尚未接入。仓库没有可复用的可信本地用户/组织主体、实时 Policy / Approval 服务和正式工具注册表。由 Renderer 或 Sidecar 请求提供 actor、permission、tool definition 或审批结果会跨越信任边界，因此本轮没有开放该入口。生产 Workflow→ToolBroker 执行组合根仍阻塞，需先提供可信主体和正式 Tool / Policy / Approval 组合。
- **状态与交付：** 当前分支 `codex/task34-production-recovery`，本轮检查时 HEAD 为 `0f8be979e1928026930354c371d371638f3daed0`；日志中其父提交 `aec68a8e879528c440620818cad4176aea2aa7f5` 已包含 Worker runner / action digest fencing 与第一版 WSS 崩溃用例，`0f8be97` 固定该测试时钟。本轮 ACK 丢失场景及本证据更新仍在工作区、尚未提交。远端 `origin` 指向本机 `C:\Dev\shuliu-agent`，本分支无 GitHub upstream；没有本轮完整 HEAD 的远端 CI、PostgreSQL 17/18 fencing、安装版 Worker/Desktop 生产端到端或独立审查证据。用户提供的 CI runs `41419030117` 和 `41427409240` 针对 `24699b5`，不验证上述提交或当前工作区差异。

**Task 34 仍为 IN_PROGRESS / acceptance BLOCKED，Task 35 未开始。** 已补真实本地 WSS Worker 进程崩溃、Cloud result ACK 丢失及防重放证据；仍缺 Workflow 生产执行组合根、可启动的生产 Worker host、PostgreSQL fencing、安装版完整验收、当前差异独立审查以及最新提交 SHA 对应 CI。

### 独立核实与 SHA 对应 CI 证据（2026-11-02 19:50，审查者）

本节由独立审查者（非 Task 34 实现者）在 Windows 实机完成，更正前文“没有本轮完整 HEAD 的远端 CI”“Cloud ACK 提交后应答丢失场景仍未完成”两处陈述。

- **提交与推送：** 实现者未提交的工作区由审查者分三次提交并推送到 GitHub `codex/task34-production-recovery`：`aec68a8`（WorkerTaskRunner、action digest fencing、dispatch/ACK 崩溃注入）、`0f8be97`（固定真实 WSS 崩溃用例时钟）、`6a5a93b`（Cloud 已提交结果但 ACK 丢失场景）。推送方式为隔离副本 → 本机 `C:/Dev/shuliu-agent` → GitHub；隔离副本的 `origin` 指向本机仓库，因此 GitHub 推送由主仓完成。
- **CI（SHA `6a5a93b`，即当前 HEAD）：** Generic CI run `41438143417` **success**，四个 job 全部通过：`Generic tests (ubuntu-latest)`、`Generic tests (macos-latest)`、`PostgreSQL migration (PG 17)`、`PostgreSQL migration (PG 18)`。Windows Validation run `41438147882` **success**。
- **CI（SHA `0f8be97`）：** Generic CI run `41436257429` **success**（同上四个 job），Windows Validation run `41436263540` **success**。
- **CI（SHA `24699b5`，前序基线）：** Generic CI run `41427409240` 第 1、2 次尝试在 `tests/contract/ipc-roundtrip.test.ts` 因 ubuntu runner 上 Electron 进程 `SIGSEGV`（`signal=SIGSEGV`、`status=null`）失败，第 3 次尝试 **success**；Windows Validation run `41419030117` **success**。该失败为已记录的 runner 偶发，同一 SHA 重跑即通过，不是断言逻辑缺陷。
- **Windows Validation 覆盖范围说明：** `.github/workflows/windows-validation.yml` 只执行 `tests/computer`、`tests/e2e/windows`、`tests/browser`、`tests/skills` 与 PyInstaller 安装包校验，**不执行** `runtimes/python-worker/tests/worker` 与通用 `tests/integration`。因此本轮 Worker runner / action digest / 崩溃注入的 Windows 覆盖仍是本机实机结果；CI 侧覆盖来自 Generic CI 的 ubuntu、macOS、PG 17、PG 18 四个 job。
- **审查者独立复跑（Windows 实机）：** `pytest runtimes/python-worker/tests/ipc tests/recovery tests/workflow tests/worker tests/persistence services/cloud-api/tests/workers tests/integration/test_worker_wss_cloud_loop.py tests/integration/test_worker_wss_dispatch_process_crash.py -q` → **165 passed, 2 skipped**，两个 skip 仍为无 `SHULIU_DATABASE_URL` 的 PostgreSQL 注册/并发 fencing 测试。Worker protocol Vitest contract → **18 passed**。与实现者报告的 163 passed 相差 1，差异为审查者额外纳入的 `test_worker_wss_dispatch_process_crash.py`（实现者的清单未包含该文件）。
- **代理环境注意：** 本机首次复跑真实 TLS WSS 用例时 `test_worker_wss_cloud_loop.py` 握手超时失败；清除 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` 后 **1 passed**。原因是本机代理把 `127.0.0.1` 的 WSS 握手也转发了。该失败是环境问题，不是产品缺陷；CI runner 无此代理。
- **审查者对代码的独立判断：** Worker 侧 `worker_action_digest` 与 Cloud 侧 `_task_action_digest` 使用同一 canonical JSON 形式（`sort_keys`、紧凑分隔符、`ensure_ascii=False`、`allow_nan=False`），摘要可对账；Cloud 的 `authorize_tool_dispatch` 先校验调用者持有该 lease（`initial["worker_id"] == principal.worker_id`）再计算并比对动作摘要，不会向非持有者泄露任务动作信息。`WorkerTaskRunner` 拒绝任务 payload 携带 `actor`、`context`、`organization_id`、`permissions`，执行身份只取自已认证 WSS 响应；ACK 状态未知时抛 `WORKER_RESULT_ACK_UNKNOWN` 且不重发。未发现可绕过授权边界的新入口。
- **仍未完成（本轮不改变验收结论）：** `ipc/bootstrap.py` 只构造持久 `SQLiteWorkflowRepository`，`ipc/server.py` 无生产 Workflow 执行 handler；仓库无可启动的生产 Worker host；无生产可信主体 / Policy / Approval / ToolRegistry 组合根。这对应本文档 “Acceptance blockers” 第 1、3 项，仍是 Task 34 的硬门槛。
- **合并注意：** `feat/foundation-v0.1` 与 `codex/task34-production-recovery` 双方都修改本文档，且前者多出 2 个仅文档的提交，合并时会在此文件冲突，需要人工取舍。

**结论不变：Task 34 仍为 IN_PROGRESS / acceptance BLOCKED。** 本轮新增的是 Worker 执行入口、动作摘要绑定、三个真实崩溃/Ack 丢失场景与 SHA 对应的平台 CI 证据；生产 Workflow→ToolBroker 组合根与可启动的生产 Worker host 仍未接通，Task 35 未开始。
