# Task 24 / Task 25 — Browser 与 Computer 的生产组合根

日期：2026-11-05
基线：`codex/task34-production-recovery` @ `da53ecd`（含 Task 33 幂等实现与 Task 34 生产组合根）
范围：仅补齐 Browser / Computer 的生产装配。不修改 Desktop、聊天、Computer Runtime 执行逻辑、Skill、Sandbox 或 `main`。

## 问题

`BrowserRuntime` 与 `ComputerRuntime` 在 Task 24 / Task 25 中已完成实现并有测试，但**没有任何生产代码构造它们**：只有 `tests/browser/support.py` 与 `tests/computer/test_computer_contract.py` 手工装配。后果是：

- 生产环境不存在 `browser.*` / `computer.*` Tool，Workflow 无法调用它们；
- 即便将来接线，durable idempotency、Approval 绑定与 evidence 落盘也没有确定的实现来源；
- Task 33 记录的“Browser / Computer 副作用仍 fail-closed”实际是“根本没有路径”，而不是“路径存在但被门禁挡住”。

## 改动

| 文件 | 作用 |
| --- | --- |
| `shuliu_runtime/production/evidence.py`（新增） | `FileEvidenceStore`：内容寻址、原子写入、大小上限、凭据型键拒绝 |
| `shuliu_runtime/production/desktop.py`（新增） | `compose_desktop_runtimes`、`DesktopRuntimeOutcome`、origin 解析与不可用码 |
| `shuliu_runtime/production/composition.py` | 拆出 `compose_trusted_dependencies`；新增 `compose_production_runtime`、`ProductionRuntimeOutcome`、`attach_desktop_runtimes`、`attach_production_runtime` |
| `shuliu_runtime/ipc/bootstrap.py` | `RuntimeBootstrapResult` 增加 browser / computer 运行时与不可用码字段 |
| `shuliu_runtime/ipc/server.py` | 装配改为 `compose_production_runtime`；health 增加 `browser` / `computer` 组件 |
| `tests/production/test_evidence_store.py`（新增） | 8 项：持久化、去重、上限、敏感键、不可序列化、存储不可用 |
| `tests/production/test_desktop_composition.py`（新增） | 14 项：身份缺失、origin 缺失/非法、完整装配、工具合流、写操作无审批不派发、gate 不可配置、持久幂等、非 Windows 不可用、证据落盘、注册表冲突、局部失败不拖垮 Workflow |
| `tests/production/test_sidecar_wiring.py` | 新增 4 项：桌面运行时在无配置时全关、有 origin 时装配、health 组件语义 |

## 设计约束（写成代码而不是文档约定）

1. **Browser 执行门禁固定为 `None`**，不接受任何环境变量。生产上 `launch` / `observe` / `close` 可用，`navigate` / `click` / `type` / `scroll` 在派发前被拒。Task 24 的“production action gate remains closed”由此成为装配层事实，而不是口头约定。
2. **写操作先过审批**。Policy 对 CRITICAL 动作必须给出 `REQUIRE_APPROVAL`；没有 `approval_id` 时 Broker 直接抛 `APPROVAL_REQUIRED`，执行器根本不会被调用（测试用 FakeBrowser 断言 `navigate` 未出现在调用记录里）。
3. **origin 白名单无默认值**。`SHULIU_BROWSER_ALLOWED_ORIGINS` 缺失 → `BROWSER_ALLOWED_ORIGINS_UNAVAILABLE`；含通配、路径、查询串、用户信息、非 http(s) → `BROWSER_ALLOWED_ORIGINS_INVALID`。两种都不装配 Browser。
4. **证据必须落盘才能报成功**。`evidence_ref` 指向 `evidence:<sha256前32>`，文件在运行数据目录下。写失败、超过 256 KiB、或记录中出现 `password` / `token` / `api_key` / `credential` / `cookie` 等键时抛错，Browser 与 Computer 的执行器据此 fail closed，不会返回一个指向不存在记录的引用。
5. **三个运行时共用一套依赖**。Workflow、Browser、Computer 使用同一个 identity resolver、同一个 Policy engine 实例和同一个 `ApprovalService`。装配两次会让“谁在操作”与“按哪版 Policy 判定”出现分叉。
6. **不可用不等于放开**。任一运行时装配失败只产出错误码并留空，Workflow 与另一运行时继续；health 里 `not_configured` + `error_code` 明确表达“关着”，并且 `healthy` 的 Browser 组件额外带 `execution_gate: closed`，防止把观察能力误读成执行能力。
7. **注册表冲突必须炸**。Browser 的工具注册表与其它 provider 同名时抛 `TOOL_REGISTRY_CONFLICT`，不做“后者覆盖前者”。

## TDD 证据

- RED：`test_evidence_store.py` 与 `test_desktop_composition.py` 在实现前收集失败（`ImportError: cannot import name 'BROWSER_ORIGINS_ENVIRONMENT_KEY'`）。
- 首轮 GREEN 后的 4 项失败及其修正，全部是测试自身或语义澄清，不是放宽带断言：
  - `ToolExecutionError: A verified identity is required` —— `TrustedIdentityResolver` 要求 actor 同时带 `subject_id` 与 `organization_id`，测试补上后者。
  - 注册表冲突用例的 executor 桩不满足 `execute`/`verify` 协议，改为真实形状的桩。
  - 非法 origin 的期望码从笼统的 `BROWSER_RUNTIME_UNAVAILABLE` 改为精确的 `BROWSER_ALLOWED_ORIGINS_INVALID`。
  - CRITICAL 动作在 Policy `ALLOW` 下会被 Broker 以 `POLICY_APPROVAL_REQUIRED` 拒绝，测试改为真实的生产配置（`REQUIRE_APPROVAL`）并断言 `APPROVAL_REQUIRED` 且未派发。
- GREEN：`pytest runtimes/python-worker/tests/production -q` → **116 passed**（原 89 + 新增 27）。
- 全量回归：`pytest runtimes/python-worker/tests -q` → 见文末“最终验证”。

## 未覆盖与风险

- **打包路径**：`BrowserRuntime` 由 `Path(__file__).resolve().parents[4]` 定位 `packages/protocols/schemas/browser-tool-v1.schema.json`。PyInstaller 产物中该路径可能不存在，届时 Browser 会以 `BROWSER_RUNTIME_UNAVAILABLE` 关闭。属 Task 26 打包范围，本任务不改。
- **驱动环境**：默认 `BrowserUseBackend` 依赖 `runtimes/browser-driver/.venv`。缺失时 `launch` 报 `BROWSER_DRIVER_UNAVAILABLE`，而不是组合失败；这是按需失败，不影响 Sidecar 启动。
- **Computer 的系统依赖**：Windows 上默认后端需要 `pywinauto` / `pywin32`；缺失时 `COMPUTER_RUNTIME_UNAVAILABLE`。非 Windows 一律 `COMPUTER_RUNTIME_UNSUPPORTED`。Linux / macOS 上没有任何 Computer Use 结论。
- **证据保留策略**：`FileEvidenceStore` 只写不删，没有轮转或保留期。Task 27 的 evidence retention 配置接管前，目录会单调增长。
- **未改语义**：Browser 的执行门禁仍然关闭，所以本次改动**没有**让任何 Browser 写操作可在生产运行；本任务交付的是“路径存在且被正确挡住”，不是“Browser 可写”。

## 最终验证

本地（Windows 11，Python 3.11 `.venv`）：

```text
pytest runtimes/python-worker/tests/production -q            → 116 passed
pytest runtimes/python-worker/tests -q --junitxml=...       → 691 tests, 0 failures, 0 errors, 19 skipped
pnpm test:unit                                              → 8 files / 24 tests passed
pnpm test:security                                          → 4 files / 10 tests passed
git diff --check                                            → clean
```

说明：

- 全量 Python 回归的进程退出码为 1，原因是 WorkBuddy 宿主对 pytest 清理自身临时目录（625 个文件）触发了批量删除拦截；同一批 JUnit 结果记录 `failures=0, errors=0`，且 CI 环境没有该拦截，以 CI 结果为准。
- 19 项 skip 与基线一致：Linux / macOS 专属沙箱检查、未配置 PostgreSQL 的数据库用例。
- 已删除本地 JUnit 与日志临时目录，未提交。

