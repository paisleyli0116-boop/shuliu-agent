# Task 33 进度与验收证据

更新时间：2026-10-26
状态：**IN_PROGRESS / acceptance BLOCKED**

## 工作区与基线

- 隔离 worktree：`D:\Documents\ChatGPT\数流\task33-durable-idempotency`
- 分支：`codex/task33-durable-idempotency`
- 基线 HEAD：`e136e4e5e63fe453d91bb6846a0a5654a14122e7`
- 本轮修改尚未提交或推送；原 `C:\Dev\shuliu-agent` checkout 及其未提交文件未作写入。
- 原计划列出迁移 `0007_tool_idempotency`，但当前已接受历史已占用 0007、0008、0009。本 worktree 新增 `0010_tool_idempotency`，父版本为 `0009_knowledge_embedding_profile`，没有改写历史迁移。

## 已实现的范围

- `IdempotencyStore` 状态包括 `CLAIMED`、`DISPATCHED`、`COMPLETED`、`INDETERMINATE` 与读侧 `RECONCILIATION_REQUIRED`；同键不同 digest 冲突，已 dispatch 的未知结果不自动重放。
- Tool Broker 在调用执行器前提交 dispatch 标记；标记失败时不调用执行器。执行器异常、审计失败或结果无法持久化时进入未知/待核验路径。只允许释放 `CLAIMED` 状态。
- SQLite 本地适配器通过 `BEGIN IMMEDIATE` 和唯一索引实现本机状态持久化；PostgreSQL 适配器使用事务、唯一约束、冲突处理和行锁，供共享 Cloud/Worker 状态使用。
- 持久化结果仅保存工具执行元数据和经过格式校验的 `result_ref`，不保存任意工具输出正文。若没有可信结果引用，完成记录被拒绝，dispatch 状态保持待核验。
- 新增 PostgreSQL 17/18 CI 步骤执行重启、并发、ACK 丢失和 dispatch 释放集成测试。

## TDD 与本地验证

已观察到的 RED：

- 初始 SQLite durable store / PostgreSQL store / 迁移模块缺失时，新增测试因模块或表不存在而失败。
- Broker 在执行器异常后的重复请求未进入 `RECONCILIATION_REQUIRED`。
- 持久化字段长度一致性测试 6 项失败，SQLite schema 约束测试 6 项失败。
- 结果引用脱敏测试失败，显示 SQLite 原先会保存工具输出中的任意正文。
- 第一轮 Worker 全套测试还暴露 Sidecar 测试继承外部 `SHULIU_MODEL_PROFILES_JSON`，并使用用户数据目录的问题；通过隔离临时目录和清理子进程中的 Provider 测试环境后复测通过。当前只核对到实际用户数据库迁移记录仍为 `0001_local_foundation`。

最近验证结果：

| 检查 | 结果 | 说明 |
|---|---|---|
| Task 33 相关 Worker（tools、persistence、browser、computer） | **154 passed, 17 skipped** | 2026-10-26 最新回归；11.36 秒。包含跨主体同键冲突、重启回放一致性、安全投影与 schema 兼容性、根/嵌套 applicator 和 `maxProperties` 拒绝、Browser session ID 输入输出一致性、必需及可选投影字段值域检查、Browser/Computer/MCP/Broker 回归。跳过项不是通过证据 |
| `pytest -m migration -q` | **27 passed, 1 skipped, 649 deselected** | 2026-10-26 最新结果；真实 PostgreSQL 不由此 SQLite/本地迁移测试替代 |
| Cloud API PostgreSQL migration tests | **12 passed, 1 skipped** | SQLite migration harness 验证 schema/migration 顺序，不等同于真实 PostgreSQL 验收 |
| PostgreSQL durable idempotency 集成测试 | **4 skipped** | 3 项 store 集成 + 1 项进程崩溃测试；实际 skip 原因均为 `SHULIU_DATABASE_URL is not configured` |
| TypeScript 类型检查 | **通过** | `pnpm typecheck`，退出码 0 |
| 协议与 schema 检查 | **通过** | Protocol package 5 passed；选定的跨语言/仓库合同 67 passed；Browser JSON Schema Draft 2020-12 校验有效 |
| event-roundtrip | **1 passed** | 在工作树内设置 `UV_CACHE_DIR` / `UV_TOOL_DIR` 后通过；没有更改用户级 uv 配置 |
| Vitest `tests/contract`（排除 IPC roundtrip 文件） | **68 passed** | 2026-10-26；含 event-roundtrip，在工作树私有 `UV_CACHE_DIR` / `UV_TOOL_DIR` 下通过。完整 `pnpm test:contract` 首轮仍记录 68 passed、2 failed；其中真实 Electron + Sidecar IPC 测试因 Windows AppContainer 无权读取工作树内 Electron `dist` 而以 `0x80000003` 退出，未改 ACL。此项不能记作 IPC E2E 通过 |
| Python Worker 全套回归（历史结果） | **420 passed, 19 skipped, 2 failed** | 两个失败均在 Skill symlink 测试夹具创建时返回 Windows `WinError 1314`；没有改权限或跳过该安全覆盖。此全套回归早于本次独立审查修正，没有重跑。最新相关子集结果见第一行 |
| Python 语法编译检查 | **通过** | 本轮新增 Trace ID/schema 检查后，对变更涉及的 Worker、迁移及测试 Python 文件运行 `compileall -q`，退出码 0 |
| `git diff --check` | **通过** | 无空白错误 |
| Vitest `tests/security` | **未完整通过** | 3 个文件、7 项测试通过；另 1 个 Desktop 安全测试文件未能加载，因为隔离 worktree 没有安装 `@vitejs/plugin-react`。未在原 checkout 安装或改动依赖 |

另有一次带隔离 `SHULIU_DATA_DIR` 的 Sidecar 子进程诊断：退出码 0、返回 3 个协议帧；IPC 回归随后 **2 passed**。该结果仅验证 Sidecar IPC，不是 PostgreSQL 或外部服务验收。

## 独立审查与修正（2026-10-26）

首次独立只读审查没有报告 Critical 项，但发现两个 Important 问题，均已按 RED → GREEN 修正：

1. **主体身份曾进入幂等唯一范围。** 审查指出，同组织、同工具、同操作和同幂等键换一个主体会取得新的 claim，可能重复派发副作用。新测试在原实现上实际失败（第二次调用未抛 `IDEMPOTENCY_KEY_CONFLICT`）；修复后持久 SQLite Broker 回归通过，第二次调用被冲突拒绝，执行器总调用数为 1。现在唯一范围为组织/工具/操作/幂等键，认证主体保留在请求 digest 内；SQLite、PostgreSQL adapter、两种 schema/migration 和集成测试均已同步。
2. **完成后的重放结果与首次响应不一致。** 审查指出首次响应会返回任意脱敏输出，而重开存储后只返回引用。新回归在原实现上实际失败，观察到首次输出带页面正文而重放仅为引用；修复后首次和重放的 `ToolResult.output` 相同，只含允许列表中的状态/Trace/引用等字段，不含 API Key、任意页面正文或其他输出字段。另加注册契约测试，拒绝无法声明安全结果引用或要求不受支持的字段的幂等 Tool，避免副作用执行后才发现无法重放。

这些修复已通过 Task 33 相关 Worker 与迁移验证。缺少安全结果引用时，Broker 将 claim 标为未知并拒绝后续重放；新增定向测试确认执行器只调用一次。

3. **Trace ID 与安全重放投影不匹配。** 第二轮独立审查指出，Broker 原先只要求 Trace ID 非空，而持久化投影只保留白名单字符；如 `trace/a`，动作可能已经派发，之后才因投影缺少 schema 必需的 Trace ID 转入未知状态。新增 RED 测试确实观察到 `IDEMPOTENCY_RESULT_INVALID`，且执行器已被调用；修复后通过共享的安全 ID 校验在授权和派发前返回 `TOOL_CONTEXT_INVALID`，测试确认执行器调用次数为零。
4. **嵌套 schema 可要求无法重放的字段。** 原注册检查只查看根层 `required`；新增 RED 测试确认包含 `allOf: [{required: ["private_body"]}]` 的契约被错误接受。修复后注册器递归检查根层组合/条件约束的必需字段，拒绝安全投影外字段、非根层保证字段及未解析的局部引用。对应测试 GREEN，Browser/Computer/Broker 相关回归 **102 passed, 17 skipped**，扩大后的 Worker 子集 **140 passed, 17 skipped**。

以上第 3、4 项由独立复审触发，RED→GREEN 均有真实输出记录；其中非幂等工具 Trace 兼容测试也先失败后通过，确保格式收紧只作用于幂等工具。

第二轮独立复审另指出两项具体兼容缺陷，已分别通过 RED → GREEN 修正：

5. **不同操作身份可编码成相同幂等范围。** 原实现以冒号连接 workflow run/step；新增测试用 workflow `("r", "s")` 与显式 operation ID `"r:s"` 观察到只派发一次。现改为带类型标签的 JSON tuple 后进行 SHA-256，定向测试通过，两个操作各执行一次。
6. **安全投影可能因 `minProperties` 在派发后失效。** 新测试确认契约只强制 `evidence_ref` 和 `ok`，却要求 `minProperties: 3` 并允许可被投影移除的 `observation` 时，旧注册检查仍接受契约。现在注册器要求组合/条件中的 `minProperties` 不得高于根层强制保留字段数；定向测试通过，执行前拒绝不兼容契约。

第二轮独立只读复审确认主体范围与回放投影修复正确，未发现 store 事务/并发回归；其新增的 `minProperties` 和操作身份问题在当时审查快照中仍可复现，已修复。最新 4 项定向回归为 **4 passed**。

第三轮独立只读复审发现输出契约与脱敏持久化投影仍有两处不兼容，现已按 RED → GREEN 修正：

7. **组合 schema 可依赖被删除的可选字段。** 新增 `oneOf` 测试在旧注册器上实际失败（注册器未拒绝该契约）。注册器现在拒绝幂等结果 schema 中的 `oneOf` / `not`，并检查组合及条件分支不能约束投影会删除的字段或依赖未知属性集合。该约束在注册阶段生效，因此拒绝发生在任何 Broker 执行前。
8. **证据引用 schema 比持久化白名单更宽。** 新增测试在旧注册器上实际失败：仅要求非空字符串的 URL 型 `evidence_ref` 契约可以注册，尽管持久化投影会丢弃它。注册器现在要求每个必需 `evidence_ref` / `result_ref` 使用与 sanitizer 一致的 ASCII 标识字符集、1–256 长度约束；真实 Browser、Computer 和 MCP 契约已同步。定向验证确认合法引用可通过，URL 与末尾换行被输出 schema 拒绝。

修正后 Task 33 相关 Worker 回归为 **145 passed, 17 skipped**；迁移标记测试为 **27 passed, 1 skipped, 649 deselected**；Cloud API migration 测试为 **12 passed, 1 skipped**（SQLite migration harness）。未运行 PostgreSQL 17/18 真实集成。

TypeScript `pnpm typecheck` 通过；选定的协议合同测试 **67 passed**、Protocol package **5 passed**，Browser schema 的 Draft 2020-12 校验通过。完整 `pnpm test:contract` 首轮有 **68 passed、2 failed**：event-roundtrip 因默认 uv 目录权限失败，配置工作树私有缓存和工具目录后该单测 **1 passed**；真实 Electron IPC 用例按正确的 `build:ipc-test` 构建后仍被 Windows AppContainer 对 Electron `dist` 的目录权限拒绝，IPC E2E 未通过且未改 ACL。

## 2026-10-26 — 独立复审后续修正

只处理幂等安全结果投影与 Browser session ID 的契约一致性，没有启动 Task 34、修改 Git main、提交或推送。

9. **组合约束可令必需引用不可能满足。** 新增 `allOf` RED 测试，在注册器原实现上观察到 `ToolRegistry.register()` 接受了 `evidence_ref.maxLength=0` 的输出契约。注册器现递归检查引用字段上的组合子 schema：只接受与持久化引用约束兼容的字符串类型、长度和规范引用模式；不支持的断言 fail closed。RED 后 GREEN：对应注册器测试通过。
10. **根级 schema 断言可绕过上述证明。** 追加 `patternProperties` 将 `evidence_ref` 限为长度 0，以及 `propertyNames` 拒绝现有必需属性名两项测试；两项在原实现上均失败（注册仍被接受）。注册器现拒绝幂等输出 schema 根级及组合分支中无法证明安全的 `patternProperties` / `propertyNames` / `unevaluatedProperties`，并继续允许已有 Browser schema 使用的根级 `additionalProperties: false`。修正后 3 项组合约束测试 **3 passed**。
11. **Browser session ID 可通过输入校验但不能被安全重放。** 新增 Broker 级 RED 测试传入 `bad/id`：原实现并未在入口返回 `TOOL_INPUT_INVALID`，而是晚至 `IDEMPOTENCY_RESULT_INVALID` 才失败。现在 Browser 输入 schema 与共享结果 schema 使用同一组 1–128 字符安全 ID 限制；该请求在派发前被拒绝，测试确认没有 Browser action、Evidence 写入或 Approval 消费。`test_contract.py` **4 passed**；整个 Browser 子集 **43 passed, 17 skipped**。

修正后的本地结果：Task 33 相关 Worker 子集（tools、persistence、browser、computer）**149 passed, 17 skipped**（11.81 秒）；Protocol package **5 passed**；跨语言/仓库合同选择集 **67 passed**。这些测试包含 Fake/SQLite 单机证据，不替代真实 PostgreSQL、Windows IPC 实机或远端 CI。

本轮新增修正尚待独立只读复审。工作树 HEAD 仍为基线 `e136e4e5e63fe453d91bb6846a0a5654a14122e7`，所有修改仍未提交 / 未推送；不将该基线 SHA 误报为本轮实现 SHA。

独立复审随后发现必需引用属性自身的 `allOf`（例如 `properties.evidence_ref.allOf: [{maxLength: 0}]`）仍未递归核对。新增 RED 测试在原检查器上确实失败；现由 `_has_safe_result_reference_contract()` 对属性 schema 同样调用兼容性递归证明，`test_broker_policy_order.py` **31 passed**。扩展的 Task 33 相关 Worker 子集最终 **150 passed, 17 skipped**（12.23 秒）。

第二轮只读复审进一步发现根 schema 或组合分支的 `maxProperties: 0` 可与必需引用字段相矛盾。根和 `allOf` 两种位置的 RED 测试均在原实现失败；现注册器拒绝小于根层必需字段数量的 `maxProperties`。注册器文件测试 **33 passed**；最终相关 Worker 子集 **152 passed, 17 skipped**（11.79 秒）。

第三轮只读复审指出安全错误码字段的 schema 最小长度约束会让 Browser Tool 无法注册。保留 Browser 错误码 `BROWSER_...` 的有限字符集，并明确其 9–128 字符边界；注册器兼容性检查允许这一受限子域，同时要求错误码仍可被脱敏投影保留。修正后的 Broker/Browser/Computer 回归最终 **154 passed, 17 skipped**。

同轮复审发现只验证根层 `required` 仍不足以保证可选投影字段与回放一致：可选 `status` 若允许 `"CUSTOM"`，会在持久化投影时被丢弃。新增 RED 测试在移除检查时实际失败（注册器未拒绝该 schema）；现对 schema 中所有声明的投影字段（必需与可选）验证持久化兼容性，对引用字段也要求与 sanitizer 完全一致的约束。Browser `trace_id` 与 `error_code` 输出契约补齐了安全字符模式和长度上限。重新运行后 Worker 子集 **154 passed, 17 skipped**，排除受 Windows AppContainer 权限阻塞的真实 Electron IPC roundtrip 后合同测试 **68 passed**，`pnpm typecheck`、Python `compileall` 和 Browser schema JSON 解析均通过。

最新的只读复审在上述修正完成后再次请求；在复审返回前，不将这一轮标记为最终审查通过。

真实 PostgreSQL 前置条件复查：`SHULIU_DATABASE_URL` 未配置，`docker` / `psql` 不存在，Docker daemon 不可用；因此本地 PostgreSQL 实集成依然未执行。

## 仍阻塞验收的事项

1. PostgreSQL 17、18 的真实事务、并发、重启和进程崩溃用例尚未运行；上述集成测试必须在真实数据库环境通过。
2. Task 33 对应 GitHub Actions 尚未运行，代码尚未推送；没有远端 CI 证据。
3. Windows Worker 全回归仍有两个 WinError 1314 失败，需在具备普通用户可创建 symlink 的等效安全夹具或批准测试环境中保留并通过该覆盖；本轮未更改其权限或断言。
4. Desktop 安全 Vitest 有一个测试文件因 worktree 缺少依赖而未加载；当前环境没有对隔离 worktree 执行依赖安装。
5. 第三轮及本轮复审指出的 schema / 投影问题均已有 RED→GREEN 与相关测试；最近的 `maxProperties` 修正正在等待最后一轮独立只读复审。审查器的本地 `ruff` 不可用（隔离 worktree 没有 `ruff` 模块），因此未声称 lint 通过。复审还指出目前没有非测试代码实例化 PostgreSQL/SQLite durable adapter：现有 Browser/Computer runtime 通过构造注入 Broker，缺 store 时 Broker fail-closed，但源码没有生产 Tool runtime 组合根来证明实际副作用入口已使用持久 adapter。源码搜索确认 Sidecar bootstrap 只运行本地数据库迁移，没有构造 Browser/Computer runtime；不得以测试注入冒充生产接线。必须在启用任何真实副作用工具前配置持久 adapter。另有 dispatch 前崩溃可能留下 `CLAIMED`（安全地阻止副作用，但需要协调），以及 PG 崩溃用例需真实 PG17/18 证据。

## 结论

SQLite/Broker 方向性实现和本地迁移测试已有通过证据，但 PostgreSQL 17/18 真实集成、完整安全回归、独立审查及与该 SHA 对应的远端 CI 证据尚不齐备。Task 33 继续保持 **BLOCKED**；Task 34 未开始。

## 2026-10-26 — 错误码边界复审补充

独立复审发现，可选 `error_code` 字段即使有安全字符模式，根级 `minLength: 129` / `maxLength: 128` 或 `allOf` 中相同矛盾约束仍可能被注册器接受。新增参数化 RED 测试覆盖根级与 `allOf` 两种布局；在修复前两项均未触发拒绝。现注册器使用符合错误码安全模式的候选值通过完整 JSON Schema validator 验证有效值域，空交集会 fail closed。修复后该定向测试 **2 passed**。

最新本地验证：Task 33 相关 Worker 子集 **156 passed, 17 skipped**（tools、persistence、browser、computer，11.05 秒）；Vitest contracts（排除 Windows AppContainer 权限阻塞的 IPC roundtrip 文件）**68 passed**；`pnpm typecheck`、相关文件 `compileall`、`git diff --check` 和 Browser schema JSON 解析均通过。Contract 全套首次运行中的 event-roundtrip 因默认用户 uv cache 权限失败；改用工作树私有 `UV_CACHE_DIR` / `UV_TOOL_DIR` 后该用例通过，并在同样隔离设置下重跑整个选择集通过。此项环境错误不等同产品失败，也不抹去 IPC E2E 未执行成功的限制。

该长度边界补丁已送交只读独立复审，尚未取得复审回复；不能据此报告最终安全审查通过。工作树仍位于 `codex/task33-durable-idempotency`，HEAD 为基线 `e136e4e5e63fe453d91bb6846a0a5654a14122e7`，Task 33 变更未提交 / 未推送；没有修改原 checkout 或 `scripts/`。真实 PostgreSQL 17/18、Windows AppContainer IPC E2E、完整 Desktop security 依赖检查、生产 durable-store composition-root 接线及对应 SHA 的 GitHub CI 仍未验证，Task 33 继续 **BLOCKED**，Task 34 未开始。

## 2026-10-26 — applicator 投影一致性复审补充

只读独立复审发现两个额外的 schema 投影边界：

1. 根级 / 嵌套 `allOf` 可为可选持久化字段施加与根字段安全契约矛盾的约束。新增根级与嵌套两项 RED 测试；修复前均为 `DID NOT RAISE`。注册器现在将无条件 applicator 对持久化字段的约束与根字段契约合并，并在安全有限值域内验证是否仍存在可持久化值。定向测试及 Browser contract **8 passed**。
2. `if.maxProperties` 可根据不会进入安全投影的额外字段改变条件结果，导致已完成结果重放时切换 `then/else` 分支。新增三字段输出 / 两字段投影的 RED 测试，修复前注册器接受该 schema；现在拒绝依赖对象属性数量的 `if` 条件，同时保留 Browser 基于安全 `status` 字段的条件约束。该定向测试与错误码/allOf/Browser 回归 **9 passed**。

修正后的最新验证：相关 Python Worker 回归 **159 passed, 17 skipped**（tools、persistence、browser、computer，11.04 秒）；Contract 选择集 **68 passed**；`pnpm typecheck`、相关 Python `compileall`、Browser schema JSON 解析和 `git diff --check` 均通过。首轮全 Contract 选择集曾因默认用户 uv cache 权限失败；使用工作树私有 `UV_CACHE_DIR` / `UV_TOOL_DIR` 后 event-roundtrip 与全选择集均通过。受 Windows AppContainer 权限限制的真实 Electron IPC roundtrip 仍未通过，未修改 ACL。

上述第二项复审修正正在等待同一独立审查者只读复核。Task 33 仍未提交 / 推送，HEAD 仍为 `e136e4e5e63fe453d91bb6846a0a5654a14122e7`，未改原 checkout 或 `scripts/`。真实 PG17/18 集成、生产 durable-store composition-root 接线、AppContainer IPC E2E、完整 Desktop security 检查及对应 SHA 的远端 CI 仍为未验证 / 阻塞项；Task 33 保持 **BLOCKED**，Task 34 未开始。

## 2026-10-26 — 嵌套条件投影复审补充

独立只读复审进一步发现：`if` 谓词内嵌套的 `if/then/else` 会重置投影检查上下文，可能令安全投影删掉私有字段后，嵌套 `then.maxProperties` 的真假改变，进而切换外层条件分支。新增 RED 用例构造“完整结果合法、保留安全可选字段的持久化投影不合法”的情况；修复前注册器未拒绝（`DID NOT RAISE`）。最小修复让嵌套条件分支继承外层 `if` 谓词标记，使投影敏感的属性数量及依赖约束继续被拒绝。修复后 Broker/Browser 定向测试 **50 passed**。

同一轮还补上 `is_safe_idempotency_trace_id` 的缺失导入，并覆盖 `session_id` 安全与不安全的 `const` / `enum`。修复前四种输入路径均因 `NameError` 而非预期校验结果失败；修复后相应参数化测试通过。随后同一只读审查者复核嵌套条件传播、`dependentSchemas` / `dependentRequired` 投影边界及 session ID 常量校验，报告当前范围内**未发现新的可确认缺陷**。该结论仅限上述代码范围，不代表整个 Task 33 独立安全审查已通过。

本轮最新验证：Worker Task 33 相关回归 **165 passed, 17 skipped**（tools、persistence、browser、computer，11.68 秒）；17 项跳过来自真实 Chrome / 独立 browser-driver 环境未安装，不能视为真实浏览器通过。Vitest contract 选择集 **68 passed**（排除受 Windows AppContainer 权限限制的 IPC roundtrip 文件）；`pnpm typecheck`、相关 Python `compileall` 及 `git diff --check` 均退出码 0。PostgreSQL 17/18 集成、本轮未运行的真实数据库故障注入、真实 Electron AppContainer IPC 及远端 CI 没有新增通过证据。

当前工作树仍为 `codex/task33-durable-idempotency`，HEAD 仍是基线 `e136e4e5e63fe453d91bb6846a0a5654a14122e7`；变更未提交、未推送。隔离用的未跟踪 `.pytest-deps-task33-20261026/` 保留且不属于产品变更；未改原 checkout、`scripts/` 或其他会话文件。生产 Sidecar 尚无 Browser/Computer Tool runtime 组合根来实例化并注入 durable adapter，缺少该生产接线证据；Broker 在未注入 store 时仍按现有逻辑 fail-closed。Task 33 继续 **BLOCKED**；Task 34 未开始。

## 2026-10-26 — PostgreSQL 执行环境复核

为推进真实 PG17/18 验收，对本机执行环境作了只读复核：`SHULIU_DATABASE_URL` 未设置（未读取或输出其值）；PATH 中没有 Docker、Podman、`psql`、`pg_ctl` 或 `initdb`；未发现 PostgreSQL Windows 服务；`127.0.0.1:5432` TCP 检查失败；WSL 未安装。此前记录的 4 项 PG 集成测试均因未配置测试数据库而 skipped，不能记作通过。没有尝试安装系统级数据库或修改系统配置，也没有重跑这些已知会 skip 的测试。

因此本机当前无法提供 PG17/18 实证；迁移 SQLite harness 和 PostgreSQL 适配器代码审查不能替代真实数据库运行。Task 33 继续 **BLOCKED**，需要可访问的隔离 PG17 与 PG18 测试数据库，或原计划中的 GitHub Actions 数据库 runner 恢复后对最终 SHA 执行 CI。此检查没有修改 Task 33 产品代码、提交、推送或清理文件。Task 34 随后已按用户指示开始本地实现，当前证据单独记录在 `docs/evidence/task-34-progress.md`；Task 35 尚未开始。

## 2026-10-26 — PostgreSQL 集成测试目标保护

审查发现 Task 33 的两份 PostgreSQL 集成模块直接把任意 `SHULIU_DATABASE_URL` 交给引擎，并会升级迁移、建表、插入和删除测试数据；与同一仓库 Task 29 集成测试采用的 `_test` 数据库名保护不同，误配到非测试库可能造成数据变更。

- **RED:** 将测试 URL 指向合成的 `shuliu_production` 名称并用引擎工厂哨兵拦截，两个测试都观察到非测试 URL 已到达数据库引擎工厂；没有建立连接或修改数据库。
- **GREEN:** 两份 Task 33 测试现在在创建引擎前要求 PostgreSQL 数据库名以 `_test` 结尾。未设置 URL 时允许本地测试明确 skip；在 `CI=true/1/yes` 而 URL 缺失时直接失败，避免 CI 把缺数据库误报为绿色。无效 URL 的错误不回显连接字符串。
- **当前验证:** 两个 Task 33 PostgreSQL 集成模块 **4 passed, 4 skipped**。4 项 passed 是数据库目标/CI 配置保护测试；4 项真实 PostgreSQL 测试仍因本机没有数据库而 skipped。未连接数据库，未执行 PG17/18 事务断言。
- 已核对 `.github/workflows/ci.yml` 的矩阵确实使用 PG17、PG18 服务，并设置 `shuliu_test` 数据库名及对应 URL；工作流将执行两份 Task 33 集成模块。远端运行证据仍缺失。

该变更只改 Task 33 集成测试与本证据文档，没有更改 PostgreSQL 产品适配器。Task 33 继续 **BLOCKED**，因为本机 PG17/18 证据、生产工具装配验证和最终 SHA 的远端 CI 尚未完成；没有提交或推送。

PostgreSQL 集成环境补记：精简 pytest 依赖目录最初缺少 `alembic`；工作树隔离 uv 环境离线准备又因缺少锁定 wheel `litellm-core==1.106.0` 停止。随后使用该工作树的私有 uv cache 与虚拟环境，按仓库 lockfile 安装 90 个锁定依赖并成功收集两份 PG 集成模块；结果为 **4 skipped**，具体是 `SHULIU_DATABASE_URL is not configured`（主集成模块 3 项、进程崩溃模块 1 项）。因此没有连接 PostgreSQL、没有执行 PG 断言；状态为 **skipped / database unavailable**，不是 passed。私有环境保留在工作树，不属于产品差异。

## 2026-10-26 — 独立复审总检查点

对当前 Task 33 实现的只读审查未发现 Critical 项，并确认核心状态转换次序、digest / expected-status 条件更新、仅在 `CLAIMED` 状态释放、dispatch 后未知结果不重放以及安全结果投影在当前本地 SQLite/Broker 测试中的行为。审查提出两项 Important 限制：

1. 如果进程在 `begin()` 写入 `CLAIMED` 后、`mark_dispatched()` 前崩溃，重复调用会继续得到 `IDEMPOTENCY_IN_PROGRESS`；这不会重放副作用，但可能使调用长期等待。此状态可以安全释放的条件是确认原调用者已经停止；没有进程代次/租约协调器时，盲目回收会与仍活跃的调用竞争。将其作为 Task 34 进程恢复/协调的前置风险记录，不在 Task 33 中加入未经证明安全的回收。
2. 当前代码没有生产 Tool runtime 组合根实例化 `PostgresIdempotencyStore` / `SQLiteIdempotencyStore` 并注入 Browser / Computer Broker。Broker 对缺少 durable store 的必需幂等调用会 fail-closed，因而当前安全状态是副作用 Tool 不可用；生产持久 adapter 接线尚无证据，不能称为已启用。

审查者另行运行了聚焦 SQLite/Broker 集 **62 passed**，且未修改文件。该复审结论不替代真实 PG17/18、Windows IPC、Desktop 安全套件或远端 CI 验收。最新本地回归仍为 **165 passed, 17 skipped**；Vitest contract 选择集 **68 passed**；`pnpm typecheck`、相关 `compileall` 与 `git diff --check` 通过。PostgreSQL 集成 **4 skipped**，原因如上。Task 33 仍为 **BLOCKED**，Task 34 未开始。

## 发布状态更新（2026-11-01）

- Task 33 实现提交：`a519fc5a4e3522d6da3914231a6ce4ce8d00fe89`。
- Task 34 后续提交：`5af33e49db2072b2a67c74be710cfd609f2abc83`。
- SSH 只读核验显示 `origin/feat/foundation-v0.1` 指向 `5af33e49db2072b2a67c74be710cfd609f2abc83`；该提交链包含 Task 28 至 Task 33 的代码和证据文档。`main` 未修改。
- 本次定向复测：PG 集成测试保护用例 4 passed、真实数据库用例 4 skipped；skip 原因是本地没有 PostgreSQL 测试数据库。Task 33 相关此前本地测试记录仍见上文。
- 对应 GitHub Actions / PostgreSQL 17、18 结果尚未核实，不能视为通过。Task 33 继续 **BLOCKED**。

## GitHub CI 验证结果（2026-11-01，真实运行）

- 推送 HEAD：`30ad778`（`feat/foundation-v0.1`）。
- Generic CI run `41310851081`：**success**，4 个 job 全部通过 —— `PostgreSQL migration (PG 17)` 22/22 步、`PostgreSQL migration (PG 18)` 22/22 步、`Generic tests (macos-latest)` 36/36 步、`Generic tests (ubuntu-latest)` 36/36 步。
- PG17 / PG18 job 内的 `Run durable Tool idempotency restart and concurrency integration tests` 步骤为 **success**，即 `tests/integration/test_postgres_tool_idempotency.py`（重启、并发、仅 CLAIMED 可释放）与 `tests/integration/test_tool_idempotency_crash.py`（ACK 丢失不重放、退出码 23 路径）已在真实 PostgreSQL 17 与 18 上执行并通过。
- Windows Validation run `41310851060`：**success**，35/35 步。
- 首次真实 CI 运行暴露并已修复的两个缺陷：
  1. `ck_tool_idempotency_completed_result` 被违反 —— SQLAlchemy 默认把 Python `None` 绑定成 JSON 字面量 `null` 而不是 SQL `NULL`，导致 `CLAIMED` 行插入失败。`result_json` 列改为 `JSON(none_as_null=True)`（提交 `66e6e62`）。SQLite 等价约束复现确认：SQL `NULL` 通过、JSON `'null'` 触发同一约束错误。
  2. 通用集成 job 没有数据库却执行 PG-only 测试，按设计抛出 `CI requires a PostgreSQL test database`。两个模块标记 `pytest.mark.postgres`，通用 job 过滤为 `integration and not real_browser and not postgres`；PG 矩阵 job 仍显式执行它们（提交 `30ad778`）。
- 结论：**Task 33 的真实 PostgreSQL 17/18 验收证据已取得**，不再是「skipped / database unavailable」。
- 仍然开放（不因 CI 转绿而消失）：生产 Tool runtime 组合根尚未实例化 durable store 并注入 Browser / Computer Broker（复审 Important #2），因此生产环境副作用 Tool 仍为 fail-closed 不可用。该接线属 Task 27 范围。
