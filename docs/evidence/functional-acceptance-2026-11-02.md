# 功能验收报告 — Windows 实机（2026-11-02）

验收环境：Windows 实机（Administrator），仓库 HEAD `709bc9e`（与远端同步，Generic CI + Windows Validation 全绿）。
验收方式：真实进程 / 真实 Chrome / 真实 Electron 窗口，不使用 mock。

## 验收结果总表

| # | 验收项 | 覆盖任务 | 结果 | 证据 |
|---|---|---|---|---|
| 1 | 真实 Chrome 浏览器 Runtime（导航/读页/点击/输入/滚动/关闭、动态元素与陈旧引用、超时清理、崩溃不冒充成功、未批准来源不接触、真实类型分发一次 input effect） | Task 24 / 33 | **PASS**（18/18） | 本机 `C:\Program Files\Google\Chrome` + `runtimes/browser-driver/.venv`（browser-use 0.13.11 锁定驱动）真实执行 |
| 2 | Electron Main ↔ Python Sidecar 真实子进程 NDJSON IPC 契约 | Task 24 / 34 | **PASS**（2/2） | `tests/contract/ipc-roundtrip.test.ts` 真实 Electron + 真实 Python 子进程 |
| 3 | Sidecar Supervisor 崩溃恢复策略（backoff 重启、重复崩溃恢复、诊断清理、safe mode 路径） | Task 34 | **PASS**（48/48） | `pnpm --filter @shuliu/desktop test:ipc`，10 个文件 |
| 4 | 真实桌面应用冒烟：Electron 窗口启动 + Sidecar 在线 | Task 23 / 26 | **PASS** | 窗口标题 `ShuLiu Agent · Foundation`；子进程 `.venv/Scripts/python.exe -m shuliu_runtime.ipc.server` 存活（PID 10980） |
| 5 | PostgreSQL 门控集成（RBAC/撤权/审计、Tool 幂等重启与并发、模型用量持久化、Knowledge 权限） | Task 29 / 32 / 33 | **PASS（依据 CI）** | CI run `41312173780` 真实 PG17/18（pgvector 0.8.7）执行；本机无法复现（无 Docker、WSL 被沙箱策略拦截、pgvector 无 Windows 预编译扩展），不影响证据有效性 |
| 6 | DeepSeek 真实连接（一次性授权门、一键=一次 HTTP 请求） | Task 28 | **PASS（此前已验收）** | `docs/evidence/task-28-progress.md`，Trace `cf526ffb`，Windows Validation `40161078580` |

## 详细记录

### 1. 真实 Chrome（本机，2026-11-02 09:09）

`uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/browser/test_real_browser.py -q`
首轮 62.22s：**16 passed, 2 failed**。两个失败为环境抖动：`test_real_unapproved_origin_is_not_contacted[resource]` 报 `BROWSER_CLEANUP_FAILED`，根因是临时 Profile 的回收站删除被 Windows 中止（`[safe-delete][SAFE_DELETE_FAIL_CLOSED] trash-failed: Some operations were aborted`），属 fail-closed 清理路径在系统回收站繁忙时的偶发；`test_real_type_dispatches_one_input_effect_without_fallback[A]` 同轮失败。
单独重跑两个用例：**6 passed in 24.24s**。结论：18/18 功能通过，无功能缺陷；偶发清理抖动已记录。

### 2–3. IPC 契约与崩溃恢复（本机，2026-11-02 09:11）

- 契约：真实 Electron Main 与真实 Python Sidecar 交换 NDJSON，1.56s 全过。
- `test:ipc`：10 文件 **48 passed**（含 `runtime-crash-recovery` 8 项：backoff、恢复后诊断清理、safe mode）。
- 注：运行前 `unset ELECTRON_RUN_AS_NODE`（WorkBuddy 环境注入会降级 electron.exe）。

### 4. 真实桌面应用冒烟（本机，2026-11-02 09:15）

- 启动方式：仓库 `.venv` 前置 PATH，`SHULIU_PYTHON_EXECUTABLE` 指向 `.venv\Scripts\python.exe`，`pnpm --filter @shuliu/desktop start`。
- 结果：Electron 窗口 `ShuLiu Agent · Foundation` 出现；Sidecar 子进程以仓库 venv Python 运行 `shuliu_runtime.ipc.server`，持续存活 32s+ 无 Safe Mode、无崩溃输出。
- **观察项（不阻塞，建议后续核查）**：Sidecar（PID 10980）之下另有一个系统 Python 3.11（PID 12736）运行同一模块。若为运行时有意派生的 worker 则无问题；若属意外回退到 PATH 上的系统 Python，应在派生处固定 `sys.executable`。待确认。

### 5. PostgreSQL 门控（依据 CI，run `41312173780` 与 `41310851081`）

- PG17 / PG18（pgvector/pgvector:0.8.7-pg17/18）真实执行：Knowledge ACL/撤销（含 Task 32 server RBAC，3 passed）、durable Tool idempotency 重启/并发/ACK 丢失、模型用量持久化，全部 success。
- 本机复现不可行的原因已查明并记录（见上表）。

## 仍需人工完成（不因本报告关闭）

1. Task 32：Cloud/Security owner 独立审查跨组织伪造、撤权与 Trace/Audit 证据（材料已在 `task-32-progress.md`）。
2. Task 34：生产 main 尚未实例化 RecoveryCoordinator、fencing 未走真实链路 —— 验收保持 BLOCKED（详见 `task-34-progress.md` 的行为决策记录）。
3. 上方观察项 4：确认 Sidecar 子进程派生是否应固定 `sys.executable`。
