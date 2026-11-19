# Task 27-B Evidence — Foundation Lifecycle E2E (approval chain, all-Fake)

- 日期：2026-11-19
- 分支：`feat/task27-release-gate`
- 范围：仅 Task 27-B（`tests/e2e/test_foundation_lifecycle.spec.ts` + fixture sidecar + FakeTool）。Task 27 的其它子项（27-C secure-storage 原生冒烟、27-D CI 接线与 checklist）未在本轮实施。

## 交付物

| 文件 | 说明 |
|---|---|
| `tests/e2e/fixtures/lifecycle_sidecar.py` | JSON-lines fixture sidecar：SQLiteDatabase + WorkflowEngine + ToolBroker + PolicyEngine + ApprovalService（均为生产实现，SQLite 持久化），Fake 仅限 identity 来源与 Tool executor |
| `runtimes/python-worker/tests/fakes/fake_tool.py` | `FakeToolExecutor`：可控失败次数、dispatch/side-effect 计数，用于证明重试不重复副作用 |
| `tests/e2e/test_foundation_lifecycle.spec.ts` | TS 驱动 Python sidecar 的 8 个 E2E 场景 |

## 编排链（与 Task 27 规格 RED 对应）

创建 Task（workflow start）→ Policy（PolicyEngine + broker 预检 `prepare_approval_context`）→ Approval（ApprovalService 请求/决定/消费）→ Tool（broker consume → execute → verify）→ Event / Audit（outbox + approval_audit_events）。

## 注入场景与结果（8/8 通过）

| 场景 | 断言 |
|---|---|
| happy path（审批闸门） | WAITING/APPROVAL → self-approval 拒绝（`APPROVAL_SELF_APPROVAL`）→ 非指定审批人拒绝（`APPROVAL_NOT_AUTHORIZED`）→ 指定审批人 APPROVE → stale token 恢复被拒（`WORKFLOW_RESUME_REJECTED`）→ 正确 token 恢复 SUCCEEDED；事件含 WORKFLOW_RUN_CREATED/WAITING/SUCCEEDED、TOOL_EXECUTION_COMPLETED；审计含 APPROVAL_REQUESTED/APPROVED/CONSUMED；executor 恰好 1 次调用 1 次副作用 |
| 低风险直通 | ALLOW 路径无需审批即 SUCCEEDED，无 APPROVAL_REQUESTED |
| Policy 拒绝 | RESTRICTED 分类 → run FAILED `POLICY_DENIED` + TOOL_EXECUTION_DENIED 事件，0 副作用，无审批创建 |
| 审批拒绝 | REJECT → 恢复后 run FAILED `APPROVAL_REJECTED`，0 副作用 |
| 进程崩溃恢复 | 进程 A 启动后 WAITING → 杀进程 → 全新进程接同一 state dir → get_run 持久 WAITING → decide + resume → SUCCEEDED，无重复副作用 |
| ack loss / 未知派发 | executor 首派发崩溃 → WAITING(EXTERNAL, `TOOL_EXECUTION_UNKNOWN`) → 再次恢复仍被 RECONCILIATION_REQUIRED 闸门拦住，不自动重放，0 副作用 |
| ACL 撤销 | 权限清空 → `IDENTITY_PERMISSION_DENIED` fail closed，0 副作用 |
| bad update 篡改 | 恢复前篡改持久化 run_context 资源 → 恢复时 digest 失配 → run FAILED `APPROVAL_CONTEXT_CHANGED` + 审计 APPROVAL_INVALIDATED |

**Skill tamper 不在本 spec**：skill 包完整性属 `skill_security_scan`（Task 35）与 `skill_sandbox_provider`（Task 36）独立 required gates，本 lifecycle spec 不冒充其证据。

## 验证命令与结果（Windows 实机，2026-11-19）

- `pnpm test:e2e` → 8 passed (8)
- `pnpm test:unit` → 24 passed (24)
- `pnpm test:contract`（`env -u ELECTRON_RUN_AS_NODE`，先 `pnpm --filter @shuliu/desktop build:ipc-test`）→ 75 passed (75)
- `pnpm typecheck` → 通过
- `git diff --check` → 干净

已知环境注意项（非代码问题）：WorkBuddy 宿主注入 `ELECTRON_RUN_AS_NODE=1` 会使真实 Electron 测试失败；本机先跑 `build:ipc-test` 生成产物。Generic CI 仍因 GitHub Billing 扣费失败无法启动（run 44451572217），远端验证待 Billing 恢复后补跑。

## 边界

- sidecar 的 identity/policy 是本地 Fake/真实引擎组合，只连接本地 SQLite，不访问真实账号或外部服务；符合 Task 27 "通用 E2E 编排只连接 Fake / local test service" 约束。
- 审批链 await/decide/resume 的机制（WAITING → token → resume → consume）首次在真实 ApprovalService + SQLite 持久化下跨进程验证。
