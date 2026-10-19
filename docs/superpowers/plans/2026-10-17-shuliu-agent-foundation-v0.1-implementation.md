# ShuLiu Agent Foundation V0.1 Implementation Plan

> **状态：Implementation Plan 已获批准；Task 21、Task 22、Task 23 实现、本地验证及 GitHub CI 均通过。** Architecture Spec 是架构事实源，本文件是施工事实源。计划获批不代表代码已实现、测试已通过或 Release 已批准。

## 目标与边界

把已确认的 ShuLiu Agent Foundation V0.1 架构拆成可逐项审查、测试和提交的工程任务。当前只做第一层通用 Foundation；不实现采购、仓库、直播、新媒体、HR 等第二层岗位机器人，也不在 Agent Core 写入岗位规则。

岗位能力只通过以下组合接入：

```text
Skill + Workflow + Tool + Connector + Knowledge + Permission
```

所有有副作用的动作都必须经过 Tool Broker → Policy → Approval（若要求）→ Executor，并使用 idempotency key；不确定结果不得盲目重放。

## 已锁定架构决策

- ADR-013–018、ADR-022–027 均已同步到 Architecture Spec 与 ADR 台账。ADR-019–021 已被 ADR-014–016 覆盖。
- ADR-017：pnpm workspaces；Node.js 22 LTS >=22.12；Python 3.11；TypeScript 使用 Vitest；Python 使用 pytest；CI 使用 GitHub Actions；指定 Monorepo 初始目录；通用测试在 Linux / macOS，Windows 专属能力在 Windows runner / 实机；实现使用 `feat/foundation-v0.1` 独立 worktree，不能在 `main` 写产品实现。
- ADR-018：单公司内部版，保留 `organization_id`，不实现完整多租户 SaaS；身份模型为 `User + Organization Membership + Role + Permission`；Session / Access 短期有效，Refresh / Device Secret 安全存储；Identity Provider 通过可替换抽象接口接入。
- Policy：LOW 读取 / 搜索 / 分析；MEDIUM 非破坏性写入 / 普通表单；HIGH 对外发送 / 发布 / 正式记录创建；CRITICAL 付款 / 批量删除 / 权限或安全配置。HIGH 默认 Approval，CRITICAL 强制 Approval；DENY 优先、默认 Fail Closed；Agent 不得自批。
- Model：Skill 只声明 capability；Gateway 按 capability、privacy、budget、provider health、fallback policy 选模型；Provider 优先级配置化；`LOCAL_ONLY` 不得转远端。
- Skill：V0.1 只允许 ShuLiu Official / Company Internal；可执行 Skill 独立进程隔离；禁止 Skill 直接访问 Secret、OS、任意网络或 Foundation DB；副作用只经 Tool Broker → Policy → Approval → Executor；无第三方 Skill Store。Task 18 默认 fail closed；经验证的 OS provider 尚未就绪前，所有平台均不激活 executable Skill。
- Evidence retention 为可配置策略，不是协议固定天数。Release 必须通过 unit、contract、integration、migration、security、IPC handshake、Windows build、installer smoke、runtime health 全部门禁。
- Cloud PostgreSQL 是业务 Source of Truth；SQLite 只负责 cache / outbox / inbox / checkpoint / local task state，不做通用双向同步。
- Electron ↔ Python 使用 stdio + NDJSON；stdout 仅协议、stderr 仅日志。Cloud ↔ Worker 使用 WSS + heartbeat + lease + ACK + idempotency；Worker 使用一次性注册 Token、设备身份、短期会话凭据和 fencing token。
- Tool 优先级：API → Native API / COM → Browser DOM → UIA → Vision GUI → raw coordinates。Browser adapter 为 `browser-use`；Windows adapter 为 UFO² / UIA / Win32；GUI Operator 为 UI-TARS Operator；MCP 是外部 Tool / Connector 协议。
- Model Gateway 为 ShuLiu policy / capability 层 + LiteLLM。Scheduler 使用 APScheduler，Trigger 与 Workflow Execution 分离。Knowledge 使用 PostgreSQL + pgvector，ACL 先过滤再向量检索。
- 本地 Secret 使用 OS secure storage；Cloud Secret 使用 cloud secret manager。Windows 打包使用 PyInstaller + NSIS，且只以 Windows 验证为准。

## 为保证任务可执行而提出、审批时一并审阅的工具选择

这些是 Implementation Plan 的具体工程建议，不新增架构边界；未批准之前不得视作已授权实现选择：

- pnpm 精确版本固定为 `11.28.6`，该版本要求 Node >=22.13.0；根 `.node-version` 固定为 Node 22.23.3，根 `package.json` 的 `engines.node` 下限为 `>=22.13.0`。这满足 Architecture Spec 的 Node 22 LTS >=22.12 基线。参考 [pnpm 官方安装与 Node 兼容表](https://pnpm.io/installation) 和 [pnpm 11.28.6 发布记录](https://pnpm.io/blog/releases/11.28.6)。
- Python packaging / lock 建议使用 uv `0.12.20`、`pyproject.toml` 和提交的 `uv.lock`；CI 使用 `uv run --locked`。参考 [uv 官方项目与锁文件文档](https://docs.astral.sh/uv/guides/projects/) 和 [uv 官方 Releases](https://github.com/astral-sh/uv/releases)。
- Cloud API 建议使用 FastAPI，数据库访问建议 SQLAlchemy + Alembic；身份 Provider 仍是抽象接口。FastAPI 的 pytest `TestClient` 用于隔离 HTTP contract tests。[FastAPI Testing](https://fastapi.tiangolo.com/tutorial/testing/)
- 跨语言消息 Schema 建议使用 JSON Schema Draft 2020-12，TypeScript 使用 Ajv、Python 使用 `jsonschema` 做同一 fixture 集的正反例验证。
- 集成测试建议使用独立 pgvector PostgreSQL 服务容器；具体 PostgreSQL 主版本作为数据库任务的 Plan 参数，在 migration tests 通过后固定，生产 Cloud 部署目标仍未锁定。

## 任务执行规约

1. 所有任务按下方“任务顺序总览”的拓扑顺序执行；只允许在依赖已完成后开始。新增 Task 28–36 是原任务缺失能力的补充施工项，必须在 Task 27 Release Gate Closure 前完成。任务依赖图必须保持有向无环。
2. 一个任务对应一个可审查 commit；先添加本任务失败测试并确认 RED，再做最小实现得到 GREEN，再 Refactor 并重跑验收。
3. 写任务测试时依赖 FakeModel / FakeTool / FakeBrowser / FakeComputer / FakeWorker / FakeApproval；不调用真实模型、真实企业账号或不稳定第三方站点。
4. 一项需要 Windows runner / 实机的任务，如果当前没有可用 Windows 环境，只能标记未完成，不能用 Mac 结果替代。
5. 每个 GREEN / 验收命令均为计划中的目标命令；本文不运行它们，也不包含任何产品代码。
6. 所有版本化契约由 `packages/protocols` 管理。Foundation / Runtime / Worker / Skill / Event / DB Schema 版本相互独立。
7. Cloud Postgres 是业务事实源；本地 SQLite 不承担完整双向同步。Worker 交付是 at-least-once，依靠幂等键、ACK、Lease 与 fencing token 抵御重复 / 僵尸任务。
8. 任何未匹配 Policy、无法验证的 sandbox、未知协议版本、未满足兼容条件的更新都 Fail Closed。

## 任务顺序总览

| 顺序 | 任务 | 依赖 |
|---:|---|---|
| 01 | Repository / Monorepo Skeleton | 无 |
| 02 | Shared Protocol Schemas | 01 |
| 03 | Shared Types / Error Model | 02 |
| 04 | Event Model / Observability / Audit | 02–03 |
| 05 | PostgreSQL / SQLite / Migration / Outbox | 03–04 |
| 06 | Security / Secret Store | 01、03 |
| 07 | Identity / Membership / RBAC | 03、05–06 |
| 08 | Policy Engine | 03、07 |
| 09 | Approval Engine | 04、07–08 |
| 10 | Tool Registry / Broker / MCP Adapter | 02–03、06–09 |
| 11 | Agent Core State Machine | 03–04、08–10 |
| 12 | Workflow Engine | 04、10–11 |
| 13 | Checkpoint / Recovery | 05、12 |
| 14 | Scheduler | 05、12–13 |
| 15 | Model Gateway | 03、06、08 |
| 16 | Knowledge / RAG | 05–06、15 |
| 17 | Skill Runtime / Lifecycle | 02–03、06、08、10 |
| 18 | Skill Process Sandbox | 06、10、17 |
| 19 | Worker Protocol / Registration | 02–03、06–07 |
| 20 | Worker Lease / Coordinator | 05、08、10、19 |
| 21 | Cloud API | 05、07–10、20 |
| 22 | Electron ↔ Python IPC | 02–03、06 |
| 23 | Desktop UI | 03、08–09、11、22 |
| 24 | Browser Runtime | 10、18 |
| 25 | Computer Runtime — Windows only | 10、18、24 |
| 26 | Installer / Update / Recovery | 01、05–06、17–18、20、22–25 |
| 28 | Real Model Provider Configuration / Connection Test | 06、15 |
| 29 | Persistent ModelCall / Cost / Budget | 05、15 |
| 30 | Real Embedding / Knowledge Retrieval Closure | 16、28–29 |
| 31 | PDF / Word / Excel Knowledge Ingestion | 16、30 |
| 32 | Trusted Knowledge RBAC / Trace / Audit | 04、07、16、21 |
| 33 | Durable Tool Idempotency | 05、10、13、30（仅 migration 顺序） |
| 34 | Full Process Crash Recovery | 13、22、26、33 |
| 35 | Skill Security Scanner / Security Gate | 17–18 |
| 36 | Platform Skill Sandbox Providers | 18 |
| 27 | End-to-End / Release Gates | 01–26、28–36 |

以上是执行拓扑顺序；Task 27 是最终 Release Gate，Task 28–36 虽保留新编号以避免重编号已执行任务，仍必须先于 Task 27 完成。依赖边无环。Foundation 与岗位机器人分离：`skills/examples` 只放无业务语义的协议演示 Skill。

### 原计划能力缺口映射

| 尚未完成能力 | 原计划覆盖边界 | 明确实施任务 | Release Gate |
|---|---|---|---|
| 真实 Model Provider 配置、Secret/API Key 接入、连接测试 | Task 15 只有 Provider port / Fake 和 LiteLLM Adapter | Task 28 | `real_model_provider_connection`、`model_provider_license_security` |
| 真实 Embedding 调用与 Knowledge 检索闭环 | Task 16 使用 FakeEmbedding 验证 ACL-first query | Task 30 | `real_embedding_knowledge_retrieval` |
| PDF / Word / Excel 文档解析与入库 | Task 16 只有 parser 注入点 | Task 31（PDF / DOCX / XLSX） | `knowledge_document_ingestion_pdf_docx_xlsx` |
| ModelCall、成本和预算持久化 | Task 15 有内存 / telemetry ports，没有 durable ledger | Task 29 | `model_usage_budget_persistence` |
| 服务端可信 Knowledge RBAC 上下文与 Trace / Audit | Task 16 有 authorization context 类型与 ACL query，但尚无可信 HTTP 入口构造和 Audit 集成 | Task 32 | `knowledge_server_rbac_trace_audit` |
| 真实副作用 Tool 的持久化幂等 | Task 27 曾只有门禁，没有实现工程项 | Task 33 | `durable_idempotency` |
| 完整进程崩溃恢复 | Task 13 有 checkpoint / reconcile 组件测试，没有 Sidecar 进程重启端到端验收 | Task 34 | `process_crash_recovery` |
| Skill 安全扫描 | Task 17 校验签名 / checksum，不执行恶意代码扫描；Architecture Spec §16、§19 要求检查扫描结果 | Task 35 | `skill_security_scan` |

原计划审计出的 7 项均为未完成实现，不因映射到任务而自动视为完成；对应任务和 Release Gate 未通过前禁止宣称生产能力已就绪。

Skill 安全扫描同样是独立未完成能力；签名、Publisher 和 checksum 不能替代扫描，Task 27 的整体 `security` Gate 通过也不能替代专属 `skill_security_scan` evidence。

## 逐文件 TDD 工程任务

### Task 01 — Repository / Monorepo Skeleton

- **目标：** 建立可复现的跨语言工作区、基础包、GitHub Actions 通用 / Windows CI 框架和代码任务入口；不实现产品功能。
- **前置依赖：** 无；实现前必须由用户批准本 Plan，并按 ADR-017 创建 `feat/foundation-v0.1` 独立 worktree。
- **具体文件路径：** `pnpm-workspace.yaml`、`package.json`、`pnpm-lock.yaml`、`tsconfig.base.json`、`.node-version`、`pyproject.toml`、`uv.lock`、`.python-version`、`.gitignore`、`.github/workflows/ci.yml`、`.github/workflows/windows-validation.yml`、`apps/desktop/package.json`、`services/cloud-api/pyproject.toml`、`runtimes/python-worker/pyproject.toml`、`packages/protocols/package.json`、`packages/shared-types/package.json`、`packages/event-model/package.json`、`packages/testing/package.json`、`skills/examples/README.md`、`tests/contract/repository-layout.test.ts`。
- **创建 / 修改：** 创建 ADR-017 指定的全部初始目录和最小 workspace manifests；添加包名 `@shuliu/desktop`、`@shuliu/protocols`、`@shuliu/shared-types`、`@shuliu/event-model`、`@shuliu/testing` 及 Python distribution `shuliu-cloud-api`、`shuliu-python-runtime`；CI matrix 包括 Linux / macOS 通用 job 和 Windows 专属 job；在 CI 固定 pnpm / uv、Node 22.23.3（满足 >=22.13.0）、Python 3.11。
- **接口 / Schema：** Workspace 包名、Node `engines.node >=22.13.0 <23`、Python `requires-python >=3.11,<3.12`、包脚本 `test` / `typecheck` / `test:unit` / `test:contract` / `test:integration` / `test:migration` / `test:security` / `test:ipc` / `test:e2e` / `test:windows`；Python 测试由 `uv run --locked` 调用。
- **测试文件：** `tests/contract/repository-layout.test.ts`。
- **RED：** 先建立 Vitest harness，再断言指定目录、包 manifests、版本下限、lockfile 与 CI job 存在；执行 `pnpm exec vitest run tests/contract/repository-layout.test.ts`，预期因必需文件 / 目录 / workflow 缺失而失败。
- **最小实现：** 创建 ADR-017 目录与 manifests；只加运行检查需要的 workspace 脚本、lockfile 和 CI 配置，不创建业务模块。
- **GREEN 命令：** `pnpm exec vitest run tests/contract/repository-layout.test.ts`。
- **Refactor：** 消除重复版本常量；CI 从根 manifest 读取 Node / pnpm / Python 版本；Windows job 不得被 Linux / macOS job 的结果覆盖。
- **验收命令：** `pnpm install --frozen-lockfile`；`uv sync --locked --all-packages`；`pnpm exec vitest run tests/contract/repository-layout.test.ts`；检查 GitHub Actions workflow 语法并确认 Windows job 单列。
- **完成标准：** 所有 ADR-017 初始目录被追踪；两种 lockfile 可冻结安装；版本下限在本地与 CI 一致；产品实现仍为空；通用与 Windows job 的职责边界可见。
- **Commit：** `chore: bootstrap foundation monorepo and CI`

### Task 02 — Shared Protocol Schemas

- **目标：** 为 Agent、Skill、Tool、Workflow、Event、Worker、Model 七大协议及 Runtime IPC 建立唯一机器可校验 Schema。
- **前置依赖：** Task 01。
- **具体文件路径：** `packages/protocols/schemas/agent-v1.schema.json`、`packages/protocols/schemas/skill-v1.schema.json`、`packages/protocols/schemas/tool-v1.schema.json`、`packages/protocols/schemas/workflow-v1.schema.json`、`packages/protocols/schemas/event-v1.schema.json`、`packages/protocols/schemas/worker-v1.schema.json`、`packages/protocols/schemas/model-v1.schema.json`、`packages/protocols/schemas/runtime-ipc-v1.schema.json`、`packages/protocols/src/validate.ts`、`packages/protocols/src/index.ts`。
- **创建 / 修改：** 建立 Draft 2020-12 schemas；公共 envelope 字段包含 protocol name / version、message id、request / trace correlation；定义拒绝未知不兼容 major 的行为。
- **接口 / Schema：** `validateProtocol(protocolName, payload): ValidationResult`；消息必须显式 `protocol_version: 1`；错误结果携带 path、schema id 和稳定错误 code。
- **测试文件：** `packages/protocols/tests/protocols.test.ts`、`tests/contract/protocol-fixtures.test.ts`。
- **RED：** 为八种协议分别准备合法与非法 fixture；先运行 `pnpm --filter @shuliu/protocols test`，预期 Schema 缺失或非法消息被错误接受而失败。
- **最小实现：** 加入 schema 文件和单一 Validator 导出；不在此任务实现各业务模块。
- **GREEN 命令：** `pnpm --filter @shuliu/protocols test`。
- **Refactor：** 公共 envelope 用 `$ref` 复用；保持 Schema 互不循环引用；错误路径稳定、无依赖自然语言解析。
- **验收命令：** `pnpm --filter @shuliu/protocols test`；`pnpm test:contract`。
- **完成标准：** 七大协议和 IPC 都有正反例；非法 major、缺字段、类型不符能被拒绝；JSON fixture 可供 Python contract tests 重用。
- **Commit：** `feat: define versioned foundation protocol schemas`

### Task 03 — Shared Types / Error Model

- **目标：** 建立跨模块共享 TypeScript 类型和一致的 ShuLiuError envelope。
- **前置依赖：** Task 02。
- **具体文件路径：** `packages/shared-types/src/identity.ts`、`packages/shared-types/src/risk.ts`、`packages/shared-types/src/policy.ts`、`packages/shared-types/src/task.ts`、`packages/shared-types/src/run.ts`、`packages/shared-types/src/error.ts`、`packages/shared-types/src/index.ts`、`packages/shared-types/tests/error.test.ts`、`packages/shared-types/tests/risk-types.test.ts`。
- **创建 / 修改：** 定义 IDs、Org scope、Task / AgentRun refs、risk levels、policy decision、approval state、error code / category。
- **接口 / Schema：** `RiskLevel = LOW | MEDIUM | HIGH | CRITICAL`；`PolicyDecision = ALLOW | DENY | REQUIRE_APPROVAL`；`ShuLiuError {code, category, message, retryable, recoverable, severity, details, cause?, trace_id}`。
- **测试文件：** `packages/shared-types/tests/error.test.ts`、`packages/shared-types/tests/risk-types.test.ts`。
- **RED：** 写未知 risk level、错误类别、缺 `trace_id`、错误可重试标记的类型 / runtime parse 测试；`pnpm --filter @shuliu/shared-types test` 预期失败。
- **最小实现：** 加入静态类型、构造器和符合 Task 02 schema 的序列化 / parse 函数。
- **GREEN 命令：** `pnpm --filter @shuliu/shared-types test`。
- **Refactor：** 统一错误 code 常量，禁止业务包重复定义公共风险枚举。
- **验收命令：** `pnpm --filter @shuliu/shared-types test`；`pnpm --filter @shuliu/shared-types typecheck`。
- **完成标准：** 共享类型可被桌面、协议、事件包导入；未知风险值拒绝；所有错误可关联 Trace。
- **Commit：** `feat: add shared foundation types and error model`

### Task 04 — Event Model / Observability / Audit

- **目标：** 定义不可混用的 Event、Trace / Span、Log、Metric、Evidence、Audit 模型及敏感信息过滤。
- **前置依赖：** Tasks 02–03。
- **具体文件路径：** `packages/event-model/src/event.ts`、`packages/event-model/src/trace.ts`、`packages/event-model/src/log.ts`、`packages/event-model/src/metric.ts`、`packages/event-model/src/audit.ts`、`packages/event-model/src/evidence.ts`、`packages/event-model/src/redaction.ts`、`packages/event-model/src/index.ts`、`packages/event-model/tests/event.test.ts`、`packages/event-model/tests/log.test.ts`、`packages/event-model/tests/metric.test.ts`、`packages/event-model/tests/redaction.test.ts`、`runtimes/python-worker/shuliu_runtime/events/models.py`、`runtimes/python-worker/shuliu_runtime/events/observability.py`、`runtimes/python-worker/tests/events/test_event_contract.py`、`runtimes/python-worker/tests/events/test_observability.py`。
- **创建 / 修改：** Event model 与 Python serializer 对齐 Schema；建立 Trace parent / child 关系；Audit append-only interface；Evidence policy reference，不写死 retention 天数；redaction 移除 Secret / Cookie / Authorization。
- **接口 / Schema：** `append_event(event) -> event_id`、`append_audit(record) -> audit_id`、`record_metric(name, value, labels)`、`redact(payload) -> safe_payload`；所有记录使用 schema version、timestamp、org scope、trace id；Metric / Log 不承载用户正文或 Secret。
- **测试文件：** 上述 TS / Python 测试文件，以及 `tests/contract/event-roundtrip.test.ts`。
- **RED：** 测试 TS→Python→TS roundtrip、Trace 断链、缺版本、敏感字段泄漏、Metric 标签携带正文；`pnpm --filter @shuliu/event-model test` 与 `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/events -q` 预期失败。
- **最小实现：** 建立 types、serializer、append-only repository interface 和字段级 redaction。
- **GREEN 命令：** `pnpm --filter @shuliu/event-model test`；`uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/events -q`。
- **Refactor：** Event payload 与 Evidence metadata 分离；日志 / audit 写入适配器不跨模块依赖具体数据库。
- **验收命令：** `pnpm test:contract`；`pnpm test:security`。
- **完成标准：** Event / Trace / Log / Metric / Evidence / Audit 职责分离，事件版本可读、Trace 关联完整、Python 与 TS roundtrip 稳定、敏感值不进入任何记录。
- **Commit：** `feat: add event audit and observability contracts`

### Task 05 — PostgreSQL / SQLite / Migration / Outbox

- **目标：** 建立 Cloud PostgreSQL 与本地 SQLite schema、迁移机制、outbox / inbox 去重及 server ACK 边界。
- **前置依赖：** Tasks 03–04。
- **具体文件路径：** `services/cloud-api/alembic.ini`、`services/cloud-api/migrations/env.py`、`services/cloud-api/migrations/versions/0001_foundation_core.py`、`services/cloud-api/app/persistence/postgres.py`、`runtimes/python-worker/shuliu_runtime/persistence/sqlite.py`、`runtimes/python-worker/shuliu_runtime/persistence/outbox.py`、`runtimes/python-worker/shuliu_runtime/persistence/inbox.py`、`runtimes/python-worker/tests/persistence/test_sqlite_migrations.py`、`runtimes/python-worker/tests/persistence/test_outbox_ack.py`、`services/cloud-api/tests/persistence/test_postgres_migrations.py`。
- **创建 / 修改：** PostgreSQL 建立 org-scoped Task / Run / Event / Approval / Worker / Lease / Knowledge 逻辑表；SQLite 只建立 cache、outbox、inbox、checkpoint、local task state；migration ID 与 checksum 可核验。
- **接口 / Schema：** `enqueue(event_id, idempotency_key, payload)`、`ack(event_id, server_ack)`；服务端事务以 event idempotency 唯一约束接受事件后 ACK；本地仅在 ACK 确认后置 synced。
- **测试文件：** 列出的 SQLite 与 PostgreSQL 测试文件。
- **RED：** 测试重复 enqueue / 重复服务端提交、ACK 丢失续传、迁移 checksum 错误和 SQLite 中不存在业务 SOT 表；预期迁移 / repository 行为失败。
- **最小实现：** 先建 schema 与 repo interface，再实现事务去重和离线 outbox 状态；不实现通用双向同步。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/persistence -q`；`uv run --package shuliu-cloud-api pytest services/cloud-api/tests/persistence -q`。
- **Refactor：** Postgres / SQLite adapter 不共享不兼容 SQL；事务边界集中；migration 可重复查询但不能重复应用。
- **验收命令：** `pnpm test:migration`；`pnpm test:integration`。
- **完成标准：** Postgres 为业务事实源；SQLite 仅限已批准用途；重复事件不重复应用；ACK 丢失恢复可续传；迁移未知状态阻止任务继续。
- **Commit：** `feat: add postgres sqlite migrations and outbox`

### Task 06 — Security / Secret Store

- **目标：** 提供 Secret 引用与安全存取抽象，禁止长期 Secret 进入普通配置、日志或协议 payload。
- **前置依赖：** Tasks 01、03。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/security/secret_store.py`、`runtimes/python-worker/shuliu_runtime/security/os_secret_store.py`、`runtimes/python-worker/shuliu_runtime/security/cloud_secret_store.py`、`runtimes/python-worker/shuliu_runtime/security/secret_ref.py`、`runtimes/python-worker/tests/security/test_secret_store.py`、`runtimes/python-worker/tests/security/test_secret_redaction.py`、`apps/desktop/src/main/security/secure-storage.ts`、`apps/desktop/tests/security/secure-storage.test.ts`。
- **创建 / 修改：** 定义 OS secure storage provider、Cloud secret manager adapter、`secret_ref` 解析服务和测试 Fake；具体云厂商后端留作可替换实现。
- **接口 / Schema：** `SecretStore.put(ref, value)`、`get(ref)`、`delete(ref)`；业务模块只拿引用，Renderer 不拿 Secret value。
- **测试文件：** 所列 Python / TypeScript security tests。
- **RED：** 测试普通配置文件写入、日志中出现 secret、未知 secret_ref 被默许；预期拒绝或脱敏断言失败。
- **最小实现：** 加接口、Fake、OS adapter、Cloud adapter stub；当前平台无可用安全存储时 fail closed。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/security -q`；`pnpm --filter @shuliu/desktop test -- secure-storage`。
- **Refactor：** 统一 secret_ref 格式；错误只包含引用 / code，不包含值；平台 adapter 单独替换。
- **验收命令：** `pnpm test:security`。
- **完成标准：** 测试证明长期 Secret 不落普通配置 / 日志；OS / Cloud store 均可替换；不支持 secure storage 时拒绝启用相关功能。
- **Commit：** `feat: add secure secret storage interfaces`

### Task 07 — Identity / Membership / RBAC

- **目标：** 实现 V0.1 单公司身份数据模型与 Provider 抽象，所有授权基于 Organization Membership、Role、Permission。
- **前置依赖：** Tasks 03、05–06。
- **具体文件路径：** `packages/shared-types/src/identity.ts`、`services/cloud-api/app/identity/provider.py`、`services/cloud-api/app/identity/membership.py`、`services/cloud-api/app/identity/rbac.py`、`services/cloud-api/tests/identity/test_provider_contract.py`、`services/cloud-api/tests/identity/test_membership_authorization.py`、`services/cloud-api/migrations/versions/0002_identity_rbac.py`。
- **创建 / 修改：** User、Organization Membership、Role、Permission 表及 FakeIdentityProvider；持久化模型含 `organization_id`，但不实现 SaaS tenant provisioning。
- **接口 / Schema：** `IdentityProvider.authenticate(credentials) -> Session`；`authorize(user_id, organization_id, permission, resource) -> bool`；Session / Access 凭据短期有效。
- **测试文件：** 所列 identity tests。
- **RED：** 测试未知用户、无 membership、跨 organization 访问、撤销 role 后继续访问和 provider interface 不兼容；预期授权测试失败。
- **最小实现：** 先接入 Fake Provider 与最小 RBAC repository，再实现 fail-closed 权限判断和 membership 撤销。
- **GREEN 命令：** `uv run --package shuliu-cloud-api pytest services/cloud-api/tests/identity -q`。
- **Refactor：** Provider 与业务授权分离；不把权限校验放在 Renderer 或模型提示词。
- **验收命令：** `uv run --package shuliu-cloud-api pytest services/cloud-api/tests/identity -q`；`pnpm test:integration`。
- **完成标准：** 单公司边界明确、organization scope 始终传递、跨组织和缺省权限均被拒绝、生产 Provider 可通过契约替换。
- **Commit：** `feat: add single-company identity and RBAC`

### Task 08 — Policy Engine

- **目标：** 按 ADR-022 评估 Tool / Model 动作风险、权限、隐私、组织范围和明确规则，并 Fail Closed。
- **前置依赖：** Tasks 03、07。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/policy/engine.py`、`runtimes/python-worker/shuliu_runtime/policy/risk.py`、`runtimes/python-worker/shuliu_runtime/policy/rules.py`、`runtimes/python-worker/tests/policy/test_risk_matrix.py`、`runtimes/python-worker/tests/policy/test_fail_closed.py`、`runtimes/python-worker/tests/policy/test_deny_precedence.py`。
- **创建 / 修改：** 定义 Risk 分类与显式策略规则加载、版本化决策记录和规则校验。
- **接口 / Schema：** `evaluate(subject, action, resource, context) -> PolicyDecision`; 返回 ALLOW / DENY / REQUIRE_APPROVAL，含 risk、rule_id、policy_version、reason、trace_id。
- **测试文件：** 所列 `tests/policy` 文件。
- **RED：** 覆盖 LOW / MEDIUM / HIGH / CRITICAL、无规则、冲突规则、DENY 与 ALLOW 并存、未知风险；`uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/policy -q` 预期失败。
- **最小实现：** 显式规则匹配器，DENY 优先；HIGH 默认 REQUIRE_APPROVAL，CRITICAL 无法被降级；缺省 / 未知情况 DENY。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/policy -q`。
- **Refactor：** 评估纯函数化；规则加载与数据库、HTTP 解耦；decision reason 可审计但不泄露敏感内容。
- **验收命令：** `pnpm test:unit`；`pnpm test:security`。
- **完成标准：** 所有风险级别有正反例；DENY precedence、Fail Closed、HIGH / CRITICAL 规则与 ADR-022 一致。
- **Commit：** `feat: add fail-closed policy engine`

### Task 09 — Approval Engine

- **目标：** 持久化审批请求并将批准绑定不可变动作上下文，确保拒绝 / 过期 / 参数变化都不执行。
- **前置依赖：** Tasks 04、07–08。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/approval/service.py`、`runtimes/python-worker/shuliu_runtime/approval/context_hash.py`、`runtimes/python-worker/tests/approval/test_approval_lifecycle.py`、`runtimes/python-worker/tests/approval/test_approval_binding.py`、`services/cloud-api/app/approval/routes.py`、`services/cloud-api/tests/approval/test_approval_routes.py`。
- **创建 / 修改：** Approval repository、状态机、审批人授权、过期处理、审计 Event；禁止 Agent 身份自批。
- **接口 / Schema：** `request_approval(action_context, policy_decision)`、`decide(approval_id, approver, decision)`、`consume(approval_id, action_context)`；上下文摘要匹配才返回有效批准。
- **测试文件：** 所列 approval tests。
- **RED：** 测试拒绝、过期、自批、参数变更后复用旧审批；预期旧批准仍会通过的错误行为在实现前失败。
- **最小实现：** 创建审批实体和 hash binding；API 只暴露用户允许的审批操作。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/approval -q`；`uv run --package shuliu-cloud-api pytest services/cloud-api/tests/approval -q`。
- **Refactor：** Approval 状态机唯一；时间源可注入；审计 Event 不含 Secret / 完整敏感参数。
- **验收命令：** `pnpm test:integration`；`pnpm test:security`。
- **完成标准：** 高风险动作只有获授权、未过期且上下文相同的审批才可继续；拒绝 / 过期 / 自批不触达 Executor。
- **Commit：** `feat: add context-bound approval engine`

### Task 10 — Tool Registry / Broker / MCP Adapter

- **目标：** 管理 Tool schema、permission、risk、idempotency、Executor 与验证状态；将 MCP Server 能力作为不可信外部 Connector 适配。
- **前置依赖：** Tasks 02–03、06–09。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/tools/registry.py`、`runtimes/python-worker/shuliu_runtime/tools/broker.py`、`runtimes/python-worker/shuliu_runtime/tools/executor.py`、`runtimes/python-worker/shuliu_runtime/tools/idempotency.py`、`runtimes/python-worker/shuliu_runtime/tools/mcp/adapter.py`、`runtimes/python-worker/tests/tools/test_broker_policy_order.py`、`runtimes/python-worker/tests/tools/test_idempotency.py`、`runtimes/python-worker/tests/tools/test_mcp_boundary.py`、`runtimes/python-worker/tests/fakes/fake_executor.py`。
- **创建 / 修改：** 注册、发现、schema validate、Tool Broker、FakeExecutor、MCP adapter；Broker 执行顺序为 validate → identity → policy → approval if required → executor → verify → event。
- **接口 / Schema：** `ToolDefinition {name, version, input_schema, output_schema, permissions, risk_level, idempotency_required}`；`execute(tool_id, args, actor, context, idempotency_key) -> ToolResult`。
- **测试文件：** 所列 `tests/tools` 文件。
- **RED：** 测试缺 idempotency key、Policy DENY、缺 Approval、非法参数、重复 key、MCP 越权 Server；确认 Executor 调用计数为 0。
- **最小实现：** 增加 Registry + Broker pipeline 与 FakeExecutor；MCP tool 映射仍经过相同 Broker。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/tools -q`。
- **Refactor：** Executor 不持有授权逻辑；MCP transport 仅负责翻译输入输出；结果状态区分执行成功与已验证。
- **验收命令：** `pnpm test:integration`；`pnpm test:security`。
- **完成标准：** 没有绕过 Broker 的副作用入口；重复调用不重复应用；所有拒绝在调用外部 Executor 之前发生。
- **Commit：** `feat: add tool broker and guarded MCP adapter`

### Task 11 — Agent Core State Machine

- **目标：** 实现业务无关的 Task / AgentRun 状态机和版本快照。
- **前置依赖：** Tasks 03–04、08–10。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/agent/state.py`、`runtimes/python-worker/shuliu_runtime/agent/run.py`、`runtimes/python-worker/shuliu_runtime/agent/snapshot.py`、`runtimes/python-worker/tests/agent/test_state_transitions.py`、`runtimes/python-worker/tests/agent/test_run_snapshot.py`。
- **创建 / 修改：** Task 创建、AgentRun 生命周期、合法转换校验、Agent / Skill / Workflow / Policy / Runtime 版本 pinning。
- **接口 / Schema：** `create_run(task, agent_version, resolved_versions) -> AgentRun`; `transition(run_id, expected_state, next_state, event) -> AgentRun`。
- **测试文件：** 所列 Agent tests。
- **RED：** 覆盖非法状态转换、并发重复完成、升级时运行中快照漂移、Trace 丢失；预期状态机缺失而失败。
- **最小实现：** 纯状态 reducer + repository port；不加入采购、内容运营等业务语义。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/agent -q`。
- **Refactor：** 转换表与持久化 adapter 分离；将状态变化写 Event；不让 Agent 自行批准或授予 Permission。
- **验收命令：** `pnpm test:unit`；`pnpm test:integration`。
- **完成标准：** 每个状态可重放审查、版本不可变、业务词汇没有进入 Core package。
- **Commit：** `feat: add generic agent run state machine`

### Task 12 — Workflow Engine

- **目标：** 执行版本固定的 Workflow steps、分支、等待和 Tool invocation，不承担 Scheduler Trigger 职责。
- **前置依赖：** Tasks 04、10–11。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/workflow/definition.py`、`runtimes/python-worker/shuliu_runtime/workflow/engine.py`、`runtimes/python-worker/shuliu_runtime/workflow/conditions.py`、`runtimes/python-worker/tests/workflow/test_workflow_engine.py`、`runtimes/python-worker/tests/workflow/test_wait_and_resume.py`。
- **创建 / 修改：** Workflow schema validation、WorkflowRun / StepRun 状态、显式 retry / fallback / pause / fail 与 Approval 等待。
- **接口 / Schema：** `start_workflow(definition_version, input, run_context) -> WorkflowRun`; `resume_workflow(run_id, resume_token) -> WorkflowRun`。
- **测试文件：** 所列 Workflow tests。
- **RED：** 测试未知 Step、超限重试、approval wait/resume、运行中定义更新；预期当前未实现功能失败。
- **最小实现：** 顺序 / 条件 step executor 与注入 Tool Broker port；Workflow 不直接触发副作用。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/workflow -q`。
- **Refactor：** step runner 插件化；定义版本只读；retry 上限显式配置。
- **验收命令：** `pnpm test:unit`；`pnpm test:integration`。
- **完成标准：** 等待态可持久化、Approval 决策可恢复、更新定义不影响已开始的 WorkflowRun。
- **Commit：** `feat: add versioned workflow engine`

### Task 13 — Checkpoint / Recovery

- **目标：** 持久化 Workflow checkpoint 并在恢复时核对外部副作用，禁止直接重放最后一次 ToolCall。
- **前置依赖：** Tasks 05、12。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/recovery/checkpoint.py`、`runtimes/python-worker/shuliu_runtime/recovery/reconciler.py`、`runtimes/python-worker/shuliu_runtime/recovery/safe_mode.py`、`runtimes/python-worker/tests/recovery/test_checkpoint_recovery.py`、`runtimes/python-worker/tests/recovery/test_unknown_side_effect.py`。
- **创建 / 修改：** Checkpoint store、version validator、external-state reconciler、uncertain-action pause / dead-letter behavior、Safe Mode transition。
- **接口 / Schema：** `Checkpoint {run_id, sequence, state, last_completed_step, pending_action, pending_approval, context_version, created_at}`；`recover(run_id) -> RESUME | WAIT | SAFE_MODE | DEAD_LETTER`。
- **测试文件：** 所列 Recovery tests。
- **RED：** 注入“外部动作成功但结果未写入”、checkpoint schema 不兼容、Approval 等待重启、idempotency 重复；预期盲目重放防护缺失。
- **最小实现：** 先加载并校验 checkpoint，再 reconcile pending action，再恢复或暂停。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/recovery -q`。
- **Refactor：** 恢复判断纯函数化；状态迁移写 Event / Audit；不确定分支不自行猜测成功。
- **验收命令：** `pnpm test:integration`；`pnpm test:security`。
- **完成标准：** 外部状态确认后才恢复；未知副作用安全暂停；进程重启不造成重复副作用。
- **Commit：** `feat: add checkpoint reconciliation and recovery`

### Task 14 — Scheduler

- **目标：** 以 APScheduler persistent datastore 产生 Schedule occurrence，交由 Workflow Engine 执行。
- **前置依赖：** Tasks 05、12–13。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/scheduler/service.py`、`runtimes/python-worker/shuliu_runtime/scheduler/store.py`、`runtimes/python-worker/shuliu_runtime/scheduler/occurrence.py`、`runtimes/python-worker/tests/scheduler/test_schedule_semantics.py`、`runtimes/python-worker/tests/scheduler/test_misfire_dst.py`、`runtimes/python-worker/pyproject.toml`、`uv.lock`、`services/cloud-api/app/persistence/postgres.py`、`services/cloud-api/migrations/versions/0003_scheduler.py`、`services/cloud-api/tests/persistence/test_postgres_migrations.py`。
- **创建 / 修改：** Stable `schedule_id`、启动 conflict replace、default `coalesce=latest`、显式 `all`、同 Schedule no overlap、configurable `misfire_grace_time`；在 PostgreSQL migration 中创建 Schedule、occurrence 和 APScheduler job-store schema；Occurrence 用稳定 idempotency key 入 Workflow command port。Scheduler 不调用 Tool，也不声称实现完整 Workflow 崩溃恢复或持久化副作用幂等。
- **接口 / Schema：** `Schedule {schedule_id, organization_id, trigger, timezone, coalesce, misfire_grace_time, workflow_version_id}`；`ScheduleOccurrence {occurrence_id, schedule_id, scheduled_at, idempotency_key, status, workflow_run_id}`；`WorkflowCommandPort.enqueue_workflow(schedule, occurrence, idempotency_key) -> workflow_run_id`。
- **测试文件：** 所列 Scheduler tests 与 `services/cloud-api/tests/persistence/test_postgres_migrations.py`。
- **RED：** 覆盖 restart 后持久化、同 ID replace、missed latest/all、同 Schedule overlap、过期 misfire、DST / 显式 IANA timezone、无效持久化 Trigger fail-closed、跨 organization 的 schedule_id 重绑拒绝、DB partial unique active occurrence；测试证明 Scheduler 不直接调用 Tool。
- **最小实现：** APScheduler adapter + occurrence repository + Workflow command port。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/scheduler -q`。
- **Refactor：** Trigger 与 Execution adapter 分离；timezone 必须显式；APScheduler callbacks 不承载业务逻辑。
- **验收命令：** `pnpm test:integration`；`pnpm test:migration`。
- **完成标准：** PostgreSQL Schedule / occurrence / APScheduler schema 均经 migration；persistent scheduler store 可恢复；latest / all / overlap / grace 行为可测；副作用幂等键稳定并传给 Workflow command port；DST 使用显式 IANA timezone。Task 13 的完整 crash recovery 与持久化副作用幂等仍是未关闭的强制 Release Gate；DST 边界选择仍待部署策略确认。
- **Commit：** `feat: add persistent trigger scheduler`

### Task 15 — Model Gateway

- **目标：** 接受能力请求，经 ShuLiu Policy 层按 privacy / budget / health / fallback 选择 LiteLLM Provider。
- **前置依赖：** Tasks 03、06、08。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/models/capability.py`、`runtimes/python-worker/shuliu_runtime/models/gateway.py`、`runtimes/python-worker/shuliu_runtime/models/routing.py`、`runtimes/python-worker/shuliu_runtime/models/providers/litellm_adapter.py`、`runtimes/python-worker/tests/models/test_capability_routing.py`、`runtimes/python-worker/tests/models/test_local_only_fallback.py`、`runtimes/python-worker/tests/models/test_budget_health.py`、`runtimes/python-worker/tests/fakes/fake_model.py`。
- **创建 / 修改：** capability catalog、provider configuration、FakeModel、LiteLLM adapter、ModelCall telemetry。
- **接口 / Schema：** `ModelRequest {capability, privacy, budget_ref, input, trace_id}`；`complete(request) -> ModelResult`，结果含 requested capability、resolved provider/model、usage、latency、cost、error code。
- **测试文件：** 所列 Model tests。
- **RED：** 覆盖 capability mismatch、预算超限、provider unhealthy、fallback Policy recheck、LOCAL_ONLY 禁止远端；预期无安全路由而失败。
- **最小实现：** 路由器先过滤不合规 provider，再由 LiteLLM adapter 执行；FakeModel 用于核心测试。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/models -q`。
- **Refactor：** Provider 顺序只来自配置；Skill 不包含 provider/model 字段；每次 fallback 使用新的 PolicyDecision。
- **验收命令：** `pnpm test:integration`；`pnpm test:security`。
- **完成标准：** capability / privacy / budget / health 全部参与选择；LOCAL_ONLY 永不远端 fallback；每次 ModelCall 记录实际 route。
- **Commit：** `feat: add policy-aware model gateway`

### Task 16 — Knowledge / RAG

- **目标：** 实现 Cloud PostgreSQL + pgvector 知识写入 / 检索，并在数据库检索前完成 organization / scope / classification / Permission ACL 过滤。
- **前置依赖：** Tasks 05–06、15。
- **具体文件路径：** `services/cloud-api/app/knowledge/ingest.py`、`services/cloud-api/app/knowledge/retrieval.py`、`services/cloud-api/app/knowledge/acl.py`、`services/cloud-api/tests/knowledge/test_acl_before_vector_search.py`、`services/cloud-api/tests/knowledge/test_revocation_invalidation.py`、`services/cloud-api/migrations/versions/0004_knowledge_pgvector.py`、`runtimes/python-worker/shuliu_runtime/knowledge/client.py`。
- **创建 / 修改：** Document / Chunk / ACL schema、embedding gateway port、retrieval query、cache invalidation hook；parser 和具体 Embedding Provider 经配置注入。
- **接口 / Schema：** `retrieve(query, authorization_context, top_k) -> list[KnowledgeHit]`；`KnowledgeHit` 只含已授权 chunk 与来源引用。
- **测试文件：** 所列 Cloud tests 与 `tests/integration/test_knowledge_permissions.py`。
- **RED：** 建造一个相似度更高但无权的 Chunk，断言 SQL 授权候选集先收窄再排名；测试撤权 / 删除后缓存命中不得返回旧内容。
- **最小实现：** 用 ACL JOIN / predicate 限定候选行后调用 pgvector；FakeEmbedding 完成稳定查询测试。
- **GREEN 命令：** `uv run --package shuliu-cloud-api pytest services/cloud-api/tests/knowledge -q`；`uv run --package shuliu-cloud-api pytest tests/integration/test_knowledge_permissions.py -q`。
- **Refactor：** ACL query 独立且不可被 caller 替换为空；Provider / parser 不与索引表耦合；无本地第二向量索引。
- **验收命令：** `pnpm test:migration`；`pnpm test:integration`；`pnpm test:security`。
- **完成标准：** 数据库路径先 ACL 后向量排序；撤权 / 删除失效；未选真实 Provider 时可通过 FakeEmbedding 验收。
- **Commit：** `feat: add acl-first pgvector knowledge retrieval`

### Task 17 — Skill Runtime / Lifecycle

- **目标：** 实现 Manifest 验证、签名 / Publisher / checksum / compatibility / dependencies / permissions 校验、staging、版本切换和回滚。
- **前置依赖：** Tasks 02–03、06、08、10。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/skills/manifest.py`、`runtimes/python-worker/shuliu_runtime/skills/registry.py`、`runtimes/python-worker/shuliu_runtime/skills/lifecycle.py`、`runtimes/python-worker/shuliu_runtime/skills/signature.py`、`runtimes/python-worker/tests/skills/test_lifecycle.py`、`runtimes/python-worker/tests/skills/test_package_verification.py`、`runtimes/python-worker/tests/skills/test_permission_change.py`、`skills/examples/echo/manifest.yaml`、`skills/examples/echo/README.md`、`runtimes/python-worker/pyproject.toml`、`uv.lock`、`.github/workflows/ci.yml`。
- **创建 / 修改：** V0.1 trusted publisher allowlist interface；以 safe YAML loader 解析 Manifest、以可替换 verifier 接口和 Ed25519 验签实现验证 publisher signature；package content 使用确定性 SHA-256 checksum；状态机 DISCOVERED → DOWNLOADED → VERIFIED → STAGED → INSTALLED → ENABLED / DISABLED → DEPRECATED → REMOVED；staging 和 active version 分离；声明直接依赖 `cryptography==50.0.2`、`PyYAML==6.0.3` 并更新 `uv.lock`；Generic CI 显式运行此目录的 Skill tests。
- **接口 / Schema：** Manifest 含 id / version / publisher / signature / checksum / foundation range / skill protocol / capabilities / permissions / dependencies；`install(package) -> SkillInstallResult`。
- **测试文件：** 所列 Skill tests。
- **RED：** 测试未知 Publisher、篡改签名、checksum 错、协议不兼容、依赖缺失、权限增加未审批、升级失败后旧版本仍 enabled、活跃 Run 阻止卸载。
- **最小实现：** 校验完整后写 Staging，Contract Test 通过才切换 active pointer；示例 Skill 只做 echo，不包含岗位业务。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/skills -q`。
- **Refactor：** 生命周期转移表纯函数化；signature provider 可替换；安装失败不污染旧版本和业务数据。
- **验收命令：** `uv sync --locked --all-packages`；`uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/skills -q`；`pnpm test:contract`；`pnpm test:security`；`pnpm test:integration`；GitHub Generic CI 实际执行 Skill test step。
- **完成标准：** 只有可信、签名有效、兼容且权限已批准的 Skill 可启用；更新失败旧版仍可用；无第三方 Store。
- **Commit：** `feat: add verified skill lifecycle runtime`

### Task 18 — Skill Process Sandbox

- **目标：** 为可执行 Skill 提供独立子进程和受控能力通道，并验证超时、异常退出、进程崩溃与资源清理；独立进程只提供进程边界，不得被描述为完整 OS sandbox。
- **前置依赖：** Tasks 06、10、17。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/skills/sandbox.py`、`runtimes/python-worker/shuliu_runtime/skills/process_host.py`、`runtimes/python-worker/shuliu_runtime/skills/capability_proxy.py`、`runtimes/python-worker/tests/skills/test_sandbox_denials.py`、`runtimes/python-worker/tests/skills/test_tool_proxy_only.py`、`runtimes/python-worker/tests/skills/test_process_host_lifecycle.py`、`runtimes/python-worker/tests/skills/test_process_host_crash_cleanup.py`、`runtimes/python-worker/tests/skills/platform/test_linux_sandbox.py`、`runtimes/python-worker/tests/skills/platform/test_macos_sandbox.py`、`runtimes/python-worker/tests/skills/platform/test_windows_sandbox.py`、`runtimes/python-worker/tests/fakes/fake_skill_sandbox.py`、`.github/workflows/ci.yml`、`.github/workflows/windows-validation.yml`、`docs/risk-register.md`。
- **创建 / 修改：** 启动参数 allowlist、最小化环境变量、临时工作目录、有限文件系统 / IPC / network policy、capability proxy、OS sandbox provider abstract interface；子进程不继承 Secret、Cloud DB URL、Foundation DB path 或任意父进程环境。进程监督器只能由已通过 SandboxProvider 评估的宿主入口调用；stdin / Broker RPC 不得绕过 deadline / cancel 监督。不能由已验证 OS provider 强制所请求隔离规则时 Fail Closed，不启动可执行 Skill。
- **接口 / Schema：** `SandboxPolicy {skill_id, permissions, filesystem_roots, network_allowlist, secret_refs=[]}`；`SandboxProvider.assess(policy) -> {supported, enforced_controls}`；`SkillProcessHost.run(package_path, policy, input, timeout) -> result`；Skill 仅可通过宿主 NDJSON RPC 请求 Tool capability，不接收 Secret value，不直接接触 executor。
- **测试文件：** 所列 denial、Tool proxy、进程生命周期 / 崩溃清理测试及 Linux / macOS / Windows 平台测试。
- **RED：** 先写测试并实际启动子进程；尝试读取 Secret / DB 凭据、访问未授权文件、连接未许可网络、执行任意 OS 命令或绕过 Broker / Policy / Approval 时必须拒绝；策略无法 enforced 时进程不得启动。故意不读大输入的 Skill 不得卡住 deadline；挂起的 Broker 调用不得伪装成成功或自动重试，需返回 `SKILL_TOOL_EXECUTION_UNKNOWN`；未 VERIFIED 的 ToolResult 不得转发为成功。另测试超时杀进程树、非零退出与崩溃回收句柄 / 临时目录、父进程收到取消后清理子进程；Windows 测试需验证 Skill 顶层代码在 Job Object 绑定前不能启动后代。
- **最小实现：** 进程 host + 单一受控 NDJSON capability RPC；默认移除敏感环境变量、限制可执行文件 / 参数、cwd 和资源；Provider 明确返回实际可强制的控制项；stdin 写入和 Tool Broker 调用由后台通道监督，超时后副作用状态不明必须停止并要求对账。Windows 进程以 suspended 状态创建、加入 Job Object 后再恢复。测试中的 permissive provider 仅用于生命周期测试 fixture，不可作为生产安全 evidence。
- **GREEN 命令：** `uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/skills/test_sandbox_denials.py runtimes/python-worker/tests/skills/test_tool_proxy_only.py runtimes/python-worker/tests/skills/test_process_host_lifecycle.py runtimes/python-worker/tests/skills/test_process_host_crash_cleanup.py -q`。
- **Refactor：** sandbox policy 不依赖 Skill 自报；对每个平台使用独立 adapter；失效时 fail closed。
- **验收命令：** 上述 Skill tests；`pnpm test:security`；Generic CI 的 Linux / macOS sandbox runner；Windows Validation 的 Windows sandbox runner；`git diff --check`。
- **完成标准：** Secret / DB 凭据不进入子进程；无 Broker 通道的副作用请求失败；有副作用 Tool 只能经 Tool Broker → Policy → Approval → Executor；ToolResult 只有 `SUCCEEDED + VERIFIED` 才返回成功。timeout、异常退出、崩溃与取消均终止 Skill 子进程并清理宿主资源；Broker 超时后的后台操作可能继续，必须返回 `SKILL_TOOL_EXECUTION_UNKNOWN`，禁止自动重放并要求上层对账。每个平台的 OS 限制以实际 `enforced_controls` 和攻击性 runner 证据为准；不能被 Provider 强制的权限不允许 executable Skill 启动。单独的进程 / PID 隔离不代表完整 OS sandbox。Task 18 交付的 `UnavailableSandboxProvider` 默认拒绝启动；当前没有经过验证的生产 OS provider，因此不能启用可执行 Skill。私有生命周期测试中的普通子进程只证明进程监督和清理，不可作为 OS sandbox evidence。
- **Commit：** `feat: isolate executable skills behind capability broker`

### Task 19 — Worker Protocol / Registration

- **目标：** 实现 Cloud ↔ Worker WSS 消息契约、一次性注册、设备公钥绑定及短期会话认证。
- **前置依赖：** Tasks 02–03、06–07。
- **具体文件路径：** `packages/protocols/schemas/worker-v1.schema.json`、`runtimes/python-worker/shuliu_runtime/worker/protocol.py`、`runtimes/python-worker/shuliu_runtime/worker/registration.py`、`runtimes/python-worker/shuliu_runtime/worker/credentials.py`、`runtimes/python-worker/tests/worker/test_registration.py`、`runtimes/python-worker/tests/worker/test_session_auth.py`、`runtimes/python-worker/tests/fakes/fake_cloud.py`、`tests/contract/worker-protocol.test.ts`。
- **创建 / 修改：** Worker 注册、设备公钥、`worker_id`、session token、heartbeat、lease、ACK、progress、cancel、result、error message。
- **接口 / Schema：** `register(registration_token, device_public_key) -> {worker_id, session_credential}`；`authenticate(session_credential)`；长期 secret 仅经 SecretStore 取用。
- **测试文件：** 所列 Worker tests。
- **RED：** 测试重复使用 registration token、过期 token、无效签名、session 过期、明文 Secret 配置、未知 protocol version；预期认证测试失败。
- **最小实现：** 先通过 FakeCloud registration service 跑通单次注册与短期 session 校验；mTLS 不作为 V0.1 强制项。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/worker -q`；`pnpm test:contract`。
- **Refactor：** enrollment 与 runtime auth 分开；一次性 token 原子消费；协议不记录凭据值。
- **验收命令：** `pnpm test:contract`；`pnpm test:security`。
- **完成标准：** Token 只能绑定一次、Worker 密钥本地生成、Cloud 分配身份、session 短期有效、长密钥不以明文持久化。
- **Commit：** `feat: add authenticated worker registration protocol`

### Task 20 — Worker Lease / Coordinator

- **目标：** 实现任务派发、Lease 续租、heartbeat、ACK、fencing token、至少一次投递和结果对账。
- **前置依赖：** Tasks 05、08、10、19。
- **具体文件路径：** `services/cloud-api/app/workers/coordinator.py`、`services/cloud-api/app/workers/leases.py`、`services/cloud-api/app/workers/dispatch.py`、`services/cloud-api/app/workers/reconcile.py`、`services/cloud-api/tests/workers/test_lease_fencing.py`、`services/cloud-api/tests/workers/test_disconnect_recovery.py`、`services/cloud-api/tests/workers/test_idempotent_ack.py`。
- **创建 / 修改：** Worker capability matching、Lease persistence、monotonic fencing token、grace period configuration、ACK dedupe、dead-letter transition。
- **接口 / Schema：** `lease(task_id, worker_id) -> {lease_id, expires_at, fencing_token}`；`renew(lease_id, token)`；`commit_result(lease_id, token, result, idempotency_key)`。
- **测试文件：** 所列 Cloud worker tests。
- **RED：** 同时模拟旧 Worker 断线、Lease 到期、新 Worker 获得新 fencing token、旧 Worker 恢复提交；旧结果必须被拒绝；ACK 重试不得二次处理。
- **最小实现：** Transactional lease repository 与 fake WebSocket transport；参数由配置注入，不硬编码为协议常量。
- **GREEN 命令：** `uv run --package shuliu-cloud-api pytest services/cloud-api/tests/workers -q`。
- **Refactor：** Lease transition 原子化；external side effect reconciliation 留给 Tool / Workflow；错误统一 Worker error model。
- **验收命令：** `pnpm test:integration`；`pnpm test:security`。
- **完成标准：** 过期 / stale fencing token 不可提交；server ACK / idempotency 去重；失联后按 Lease 和配置恢复而非假定停止。
- **Commit：** `feat: add worker lease and fencing coordinator`

### Task 21 — Cloud API

- **目标：** 提供经过服务端 Identity / RBAC 检查的 Foundation HTTP API，并将 Worker Protocol 接入真实 TLS WebSocket 路由和 Task 20 Coordinator。
- **前置依赖：** Tasks 05、07–10、20。
- **具体文件路径：** `services/cloud-api/app/main.py`、`services/cloud-api/app/identity/api_auth.py`、`services/cloud-api/app/routes/tasks.py`、`services/cloud-api/app/routes/approvals.py`、`services/cloud-api/app/routes/health.py`、`services/cloud-api/app/approval/routes.py`、`services/cloud-api/app/workers/auth.py`、`services/cloud-api/app/workers/routes.py`、`services/cloud-api/app/workers/coordinator.py`、`services/cloud-api/app/workers/leases.py`、`services/cloud-api/app/persistence/postgres.py`、`services/cloud-api/migrations/versions/0006_worker_authentication.py`、`services/cloud-api/migrations/versions/0007_task_scoped_fencing_tokens.py`、`runtimes/python-worker/shuliu_runtime/worker/credentials.py`、`runtimes/python-worker/shuliu_runtime/worker/registration.py`、`runtimes/python-worker/shuliu_runtime/worker/transport.py`、`packages/protocols/schemas/worker-v1.schema.json`、`services/cloud-api/tests/api/test_task_routes.py`、`services/cloud-api/tests/api/test_health.py`、`services/cloud-api/tests/api/test_authz.py`、`services/cloud-api/tests/workers/test_worker_api_auth.py`、`services/cloud-api/tests/workers/test_worker_auth_postgres.py`、`services/cloud-api/tests/workers/test_lease_fencing.py`、`services/cloud-api/tests/persistence/test_postgres_migrations.py`、`runtimes/python-worker/tests/worker/test_registration.py`、`runtimes/python-worker/tests/worker/test_session_auth.py`、`tests/integration/test_worker_wss_cloud_loop.py`、`.github/workflows/ci.yml`、`services/cloud-api/pyproject.toml`、`runtimes/python-worker/pyproject.toml`、`uv.lock`。
- **创建 / 修改：** FastAPI app factory 和依赖注入；通用 Task create/read、Approval list/decision、health；Worker 注册、设备签名验证、会话挑战与换发、一次性短期 Session 验证、WebSocket 连接管理和 Coordinator 消息适配；无岗位业务 endpoint。Session signing key 只能经注入的 Cloud Secret Manager resolver 读取，缺失时 Fail Closed；Registration Token 只以 digest 持久化。
- **接口 / Schema：** `POST /v1/tasks`、`GET /v1/tasks/{task_id}`、`GET /v1/approvals`、`POST /v1/approvals/{approval_id}/decision`、`GET /health`、`POST /v1/workers/registrations`、`POST /v1/workers/session-challenges`、`POST /v1/workers/sessions`、`WSS /v1/workers/ws`。设备注册签名覆盖规范化注册字段；Worker 客户端身份由已验证的短期凭据映射到服务端注册记录，消息中的 `worker_id` 不作为认证事实。WSS 消息包括 authenticate / authenticated、heartbeat、lease_request / lease、result / ack；result 必须带 fencing token 和 idempotency key。
- **测试文件：** 上述 API、认证、Worker、迁移和 TLS 网络集成测试；Generic CI 运行 API / Worker 单测和真实本机 WSS 集成；PG17/PG18 矩阵运行认证持久化、迁移及 Worker 集成测试。
- **RED：** 先运行新增 API / Worker 测试并确认失败：无 Cloud API app、设备签名无验证、注册 Token 可重放 / 过期、短期 Session 可过期后继续使用 / 重放、伪造 worker_id 可影响身份、无 TLS Worker 路由、Coordinator 消息未接线、跨 organization 或缺 Permission 可读写 Task、跨 Task fencing scope 与唯一约束不一致。断言失败请求不产生越权记录。
- **最小实现：** app factory 显式注入 Engine、IdentityProvider、RBACRepository、Clock 和 Cloud Secret Manager resolver；注册 Token 使用 hash 且以数据库条件更新原子消费；Worker 使用 Ed25519 证明设备私钥持有，Cloud 签发有过期时间和唯一 jti 的短期凭据并在 WSS 首次使用时原子消费；重连使用短期 nonce challenge + 设备签名换新 Session。WSS 认证成功后以服务端派生的 WorkerPrincipal 接入 Coordinator；heartbeat、lease、result、ACK 走现有 Coordinator。未知执行结果保留 Lease，不自动重试；到期后的恢复必须由显式 `OUTCOME_UNKNOWN` / `RETRY_SAFE` 判定。
- **GREEN 命令：** `uv run --locked --package shuliu-cloud-api pytest services/cloud-api/tests/api services/cloud-api/tests/workers -q -m "not integration"`；`uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/worker -q`；`uv run --locked --all-packages pytest tests/integration/test_worker_wss_cloud_loop.py -q`；配置 `SHULIU_DATABASE_URL` 时运行 `uv run --locked --package shuliu-cloud-api pytest services/cloud-api/tests/workers/test_worker_auth_postgres.py -q`。
- **Refactor：** Router 只做 schema / HTTP 映射，不拥有租约或重试决策；统一安全错误码；禁止 Session / Registration Token / device private key 进入日志、配置、Event 或普通数据库字段。应用需由 HTTPS/WSS TLS ingress 暴露；本地证书集成测试不代替生产 TLS 部署证据。
- **验收命令：** `pnpm test:contract`；`pnpm test:integration`；`pnpm test:migration`；`pnpm test:security`；PG17 / PG18 `services/cloud-api/tests/workers -m integration`；Generic CI Linux / macOS 与 Windows Validation 全绿。
- **完成标准：** 真实 Worker Runtime 使用 HTTPS 注册并通过 WSS 建连；注册签名、Token 一次性使用、session expiry / replay、心跳、Lease、fencing token、结果 ACK、断线和设备签名重连均有测试证据；expired / outcome-unknown 不自动重派，只能显式 `RETRY_SAFE`；PG migration 与持久化认证在 PG17/18 通过；FakeWebSocket 单测不能作为真实连接验收证据；无岗位业务路由。真实部署域名、生产证书和 Cloud Secret Manager 的运行环境证据仍由 Release Gate 单独验证。
- **Commit：** `feat: add authenticated foundation cloud API`

### Task 22 — Electron ↔ Python IPC

- **目标：** 实现 Electron Main 管理 Python Sidecar 的 stdio + NDJSON IPC、握手、超时和恢复信号。
- **前置依赖：** Tasks 02–03、06。
- **具体文件路径：** `apps/desktop/src/main/runtime/supervisor.ts`、`apps/desktop/src/main/runtime/ndjson-channel.ts`、`apps/desktop/src/main/runtime/handshake.ts`、`apps/desktop/src/main/runtime/ipc.ts`、`apps/desktop/src/preload/api.ts`、`apps/desktop/tests/ipc/handshake.test.ts`、`apps/desktop/tests/fixtures/electron-app/`、`apps/desktop/tsconfig.ipc-test.json`、`apps/desktop/scripts/build-ipc-test.mjs`、`apps/desktop/package.json`、`runtimes/python-worker/shuliu_runtime/ipc/server.py`、`runtimes/python-worker/tests/ipc/test_ndjson_server.py`、`runtimes/python-worker/tests/fixtures/ipc_sidecar.py`、`packages/protocols/schemas/runtime-ipc-v1.schema.json`、`tests/contract/fixtures/protocols/runtime-ipc.valid.json`、`tests/contract/ipc-roundtrip.test.ts`、根 `package.json`、`pnpm-lock.yaml`、`.github/workflows/ci.yml`、`.github/workflows/windows-validation.yml`、`.gitignore`、`docs/risk-register.md`。
- **创建 / 修改：** Main spawn / supervise；stdout parser 只收 NDJSON、stderr logger；preload allowlist；startup protocol handshake + runtime health；request / trace id 和 timeout。
- **接口 / Schema：** `request_id`, `trace_id`, `protocol_version`, `method`, `params`; response 为 `result | ShuLiuError`；Renderer 仅用 typed allowlist API。
- **测试文件：** 所列 IPC tests。
- **RED：** 测试 stdout 插入 log、partial line、非法 JSON、版本不匹配、超时、sidecar crash、Renderer 任意命令请求；预期握手 / 解析 / allowlist 错误未被阻断。
- **最小实现：** Schema 校验的 newline-framed JSON parser、并发 request correlation、timeout / cancellation、受控 renderer health allowlist、受限 Sidecar 环境变量、一次有限重启并 health check；连续崩溃或协议不兼容进入 Safe Mode。
- **GREEN 命令：** `pnpm test:ipc`（包含真实 Electron Main + Python Sidecar 子进程 roundtrip）；`pnpm test:contract`；`pnpm typecheck`。
- **Refactor：** Main IPC 与 UI renderer 分离；禁止在 stdout 打印调试日志；Sidecar 启动参数无长期 Secret。
- **验收命令：** `pnpm test:ipc`；`pnpm test:contract`。
- **完成标准：** stdout 纯协议、stderr 经安全字段 allowlist 后进入普通日志；真实 Electron Main 启动真实 Python Sidecar 并验证握手、并发关联、超时、取消、崩溃、有限重启、协议不兼容与 Safe Mode；Renderer 只能通过 Preload 请求 Main 的健康 allowlist，不能发任意 IPC、命令或文件路径；通用 Linux / macOS 与 Windows Validation workflow 的 IPC gate 全绿。完整 checkpoint / durable idempotency / 副作用崩溃恢复仍由 Task 34 验收，不属于本 Task 的已完成能力。
- **Commit：** `feat: add supervised NDJSON runtime IPC`

### Task 23 — Desktop UI

- **目标：** 提供创建 / 查看任务、审批、健康状态和通用模型 Provider 设置的桌面界面，通过窄 preload API 工作。
- **前置依赖：** Tasks 03、08–09、11、22。
- **具体文件路径：** `apps/desktop/src/renderer/App.tsx`、`apps/desktop/src/renderer/routes/tasks.tsx`、`apps/desktop/src/renderer/routes/approvals.tsx`、`apps/desktop/src/renderer/routes/settings/providers.tsx`、`apps/desktop/src/renderer/components/runtime-health.tsx`、`apps/desktop/src/renderer/components/provider-profile-form.tsx`、`apps/desktop/src/renderer/api/client.ts`、`apps/desktop/src/renderer/api/providers.ts`、`apps/desktop/src/preload/api.ts`、`apps/desktop/tests/ui/task-flow.test.tsx`、`apps/desktop/tests/ui/approval-flow.test.tsx`、`apps/desktop/tests/ui/health-state.test.tsx`、`apps/desktop/tests/ui/provider-settings.test.tsx`、`apps/desktop/tests/ipc/provider-settings-allowlist.test.ts`。
- **创建 / 修改：** 通用 Foundation UI：task create/status、approval detail/decision、Runtime health / Safe Mode，以及模型 Provider 设置表单。Provider 表单负责 Provider / Model 选择、capability 显示、API Key 输入、手动连接测试、保存配置和更换 / 替换凭据；Renderer 只通过窄 typed preload API 提交动作，不持有 Provider 实现、Secret Store 或 DB 权限。不添加采购等岗位页面。
- **接口 / Schema：** Renderer 调用 `window.shuliu.tasks.create/get`、`window.shuliu.approvals.list/decide`、`window.shuliu.health.get` 和 `window.shuliu.providers.list/testConnection/saveProfile/replaceCredential`。API Key 只在用户触发的表单请求中短暂驻留内存，测试 / 保存结束后清空输入；不得写入 localStorage、普通配置、渲染日志、Event、错误文本或测试快照。Task 23 使用 Fake IPC 验证界面行为；实际 SecretRef / Gateway 调用由 Task 28 提供。
- **测试文件：** 所列 UI tests 与 provider settings allowlist / secret redaction tests。
- **RED：** 以 Fake IPC 测试创建 Task、拒绝审批后状态、Safe Mode 提示、缺权限隐藏 / 禁用；Provider 测试覆盖 Provider / Model 选择、输入 Key、测试连接成功 / 失败状态、保存、替换旧凭据、完成后清空 Key、错误 / snapshot / log 不包含原始 Key；无 UI / typed API 时失败。
- **最小实现：** 页面 + typed API client + loading/error/empty states；Provider 按钮只调用窄 preload contract，不在 Task 23 内实现真实 Provider、模型调用或 Secret 持久化。
- **GREEN 命令：** `pnpm --filter @shuliu/desktop test -- ui`。
- **Refactor：** 页面只读写业务 API，不持有 Secret / DB access / arbitrary filesystem access。
- **验收命令：** `pnpm --filter @shuliu/desktop test`；`pnpm --filter @shuliu/desktop typecheck`。
- **完成标准：** 用户能查看任务、Approval、runtime health，并能在统一设置页输入 API Key、选模型、发起连接测试、保存或更换凭据；UI 状态可用 Fake IPC 验收，明文 Key 不持久化 / 日志化；UI 不能绕过 Policy。真实 Provider 连接与安全存储由 Task 28 验收。
- **Commit：** `feat: add foundation desktop task and approval UI`

**Task 23 执行记录（2026-10-19）：** 已实现统一工作台 Shell、任务 / 审批 / Runtime Health 与 Safe Mode 页面、Provider 设置交互、受控 Preload API 和 Main IPC。Provider 连接测试明确标记为模拟，未发起真实 Provider 请求；测试用 API Key 经 Main 进入既有 OS secure storage，UI 只返回脱敏状态，测试用户数据目录已在验证后删除。桌面 UI 9 个测试文件 / 23 项通过；Desktop typecheck、build、Root typecheck、Security 8 项、`pnpm test:ipc`（真实 Electron Main + Python Sidecar）及 Python 全套（255 passed / 8 skipped）通过。Worker-only 测试范围为 206 passed / 3 skipped；该命令只运行 `runtimes/python-worker/tests`，Task 21 的 240 项报告属于全 Python 包范围。Task 22 在 Worker 范围新增 IPC Server / Sidecar 测试，不存在从全套清单中删去 Worker 测试的改动。实际 Electron 版本 44.9.0 的内嵌 Node 为 24.22.0；本机项目 Node 工具链为 22.22.2（满足 >=22.12），GitHub CI 按 `.node-version` 锁定 22.23.3。已通过源码构建的真实 GUI 启动、导航、任务 / Provider 输入与操作、Sidecar 不可用时 Safe Mode、Renderer API 白名单检查及正常退出 smoke；安装包启动和升级 / 回滚验证仍属于 Task 26。截图留档：`docs/evidence/task-23-desktop-task-workspace.png`、`docs/evidence/task-23-desktop-provider-settings.png`。Task 23 commit `23b4f561ac4d87e4d10466da993206ad3672e5b9` 的 Generic CI run `39196822845`（Linux、macOS、PostgreSQL 17 / 18）与 Windows Validation run `39196822808` 均通过。

### Task 24 — Browser Runtime

- **目标：** 将 `browser-use` 封装为受控 Browser Tool adapter，提供观察、动作和结果验证。
- **前置依赖：** Tasks 10、18。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/browser/adapter.py`、`runtimes/python-worker/shuliu_runtime/browser/session.py`、`runtimes/python-worker/shuliu_runtime/browser/verification.py`、`runtimes/python-worker/tests/browser/test_observe_act_verify.py`、`runtimes/python-worker/tests/browser/test_session_isolation.py`、`runtimes/python-worker/tests/fakes/fake_browser.py`、`packages/testing/src/fake-browser.ts`、`tests/integration/test_browser_tool_policy.py`。
- **创建 / 修改：** Adapter contract、browser session / profile boundary、DOM observation、action API、verification result、FakeBrowser。
- **接口 / Schema：** `observe(session_id) -> BrowserObservation`；`act(action, policy_context) -> BrowserActionResult`；结果含 VERIFIED / UNVERIFIED / FAILED 与 evidence ref。
- **测试文件：** 所列 Browser tests。
- **RED：** 测试过期 session、账号目标变化、重复点击、Policy deny、动作后状态未变；不得把点击事件视作成功。
- **最小实现：** 以 browser-use adapter 提供受限 API；所有动作经过 Tool Broker；观察结果最小化并脱敏。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/browser -q`；`uv run --package shuliu-python-runtime pytest tests/integration/test_browser_tool_policy.py -q`。
- **Refactor：** Browser Runtime 不直接调用 Model 或持有 Provider Secret；FakeBrowser 和真实 adapter 共用 contract。
- **验收命令：** `pnpm test:integration`；`pnpm test:security`。
- **完成标准：** observe-act-verify 顺序受测；session/profile 不跨用户混用；副作用不能被未验证结果静默标记成功。
- **Commit：** `feat: add guarded browser runtime adapter`

### Task 25 — Computer Runtime — Windows Only

- **目标：** 将 UFO² / UIA / Win32 与 UI-TARS Operator 接入受控 Computer Tool；只在 Windows Interactive User Session 验收。
- **前置依赖：** Tasks 10、18、24。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/computer/adapter.py`、`runtimes/python-worker/shuliu_runtime/computer/ufo_operator.py`、`runtimes/python-worker/shuliu_runtime/computer/uia_backend.py`、`runtimes/python-worker/shuliu_runtime/computer/win32_backend.py`、`runtimes/python-worker/shuliu_runtime/computer/verification.py`、`runtimes/python-worker/tests/computer/test_computer_contract.py`、`tests/e2e/windows/test_interactive_session.py`、`.github/workflows/windows-validation.yml`。
- **创建 / 修改：** Computer adapter 接口；capture / observe / action / verify；UI-TARS Operator 的受控调用；Windows-only capability health check。
- **接口 / Schema：** `observe(window_ref) -> ComputerObservation`；`act(action, approval_context) -> ComputerActionResult`; 若不是 Interactive User Session、锁屏或会话状态不明则 `UNAVAILABLE` 并拒绝副作用。
- **测试文件：** 所列平台 / E2E tests。
- **RED：** Windows runner / 实机测试锁屏、session 断开、窗口变化、DPI、动作失败、Policy deny；非交互 runner 必须明确标成不可执行而不能假绿。
- **最小实现：** Windows adapters + session capability guard + Tool Broker route；MAC/Linux 只运行 adapter contract fake，不声称 Computer Use 通过。
- **GREEN 命令：** Windows Interactive User Session 上运行 `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/computer tests/e2e/windows -q`。
- **Refactor：** 后端选型隐藏在 adapter；统一动作结果与 Evidence；raw coordinates 仅作为最低优先级且需要重新观察。
- **验收命令：** Windows runner / Windows 实机验证 Computer Use、UFO² / UIA / Win32、会话中断与恢复；Mac CI 不得覆盖 Windows Gate。
- **完成标准：** 有带机器与会话信息的 Windows 证据；锁屏 / 无会话时安全拒绝；动作验证、Approval 与审计链完整。
- **Commit：** `feat: add Windows interactive computer runtime`

### Task 26 — Installer / Update / Safe Mode

- **目标：** 建立签名更新包、迁移、健康门、Safe Mode 和兼容性检查后的回滚；覆盖 PyInstaller / NSIS Windows 安装路径。
- **前置依赖：** Tasks 01、05–06、17–18、20、22–25。
- **具体文件路径：** `apps/desktop/electron-builder.yml`、`apps/desktop/build/windows.spec`、`apps/desktop/installer/installer.nsi`、`apps/desktop/src/main/update/verify.ts`、`apps/desktop/src/main/update/apply.ts`、`apps/desktop/src/main/update/rollback.ts`、`apps/desktop/src/main/update/health-gate.ts`、`apps/desktop/tests/update/test-signature.test.ts`、`apps/desktop/tests/update/test-rollback-compatibility.test.ts`、`tests/e2e/windows/test_installer_smoke.ps1`、`tests/e2e/windows/test_upgrade_rollback.ps1`、`.github/workflows/windows-validation.yml`。
- **创建 / 修改：** Foundation / Runtime / Worker / Skill / Event / DB 版本 manifest；signature + checksum verification；PyInstaller build；NSIS installer；migration dry-run / rollback compatibility；runtime health gate。
- **接口 / Schema：** `CompatibilityManifest {foundation, runtime, worker, skill_protocol, event_schema, database_schema, python_runtime}`；update state `DOWNLOADED → VERIFIED → STAGED → MIGRATED → HEALTH_CHECKED → ACTIVE`。
- **测试文件：** 所列 update 和 Windows installer tests。
- **RED：** 测试坏签名、checksum 错、migration 失败、旧版不兼容、health failed、下载中断、rollback 不安全；预期错误地启用新版本的行为不得通过。
- **最小实现：** 先验证包，再 staging，migration / runtime health 成功才切换；失败进入 Safe Mode 或兼容性允许的回滚。
- **运行时版本验收：** 以 `.node-version` 中 Node 22 工具链构建；在 Windows runner / 实机启动实际 Electron 打包产物，并记录 Electron 内嵌 Node 版本（当前开发 Smoke 测得 Electron 44.9.0 / Node 24.22.0），验证 Preload、Sidecar 启动和健康检查；不得把开发态 Smoke 代替 Installer / Upgrade / Rollback 验收。
- **GREEN 命令：** `pnpm --filter @shuliu/desktop test -- update`；Windows runner 执行 `powershell -File tests/e2e/windows/test_installer_smoke.ps1`。
- **Refactor：** Update orchestrator 不负责平台打包细节；兼容性判定可单测；普通 update 不清理旧版本和 Evidence。
- **验收命令：** Windows runner / 实机运行 PyInstaller、NSIS、安装、升级、回滚；`pnpm test:migration`；`pnpm test:security`。
- **完成标准：** 所有坏包 / 坏迁移 / 健康失败都阻止启用；rollback 前做协议 / DB 检查；Windows smoke 证据已留档。
- **Commit：** `feat: add signed update and Windows installer gates`

### Task 28 — Real Model Provider Configuration / Connection Test

- **目标：** 将配置化 Provider Profile、SecretRef/API Key 解析和受策略约束的真实连接测试接入现有 Model Gateway；Fake Provider 仍用于常规 CI。
- **前置依赖：** Tasks 06、15、22、23。具体 Provider / Model 与测试凭据是运行环境输入，不是 Skill 字段或新 ADR；没有用户配置的 Secret 时，真实连接 Gate 为 BLOCKED，不得以 Fake 结果代替。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/models/config.py`、`runtimes/python-worker/shuliu_runtime/models/connection.py`、`runtimes/python-worker/shuliu_runtime/models/gateway.py`、`runtimes/python-worker/tests/models/test_provider_configuration.py`、`runtimes/python-worker/tests/models/test_provider_secret_resolution.py`、`runtimes/python-worker/tests/models/test_provider_connection.py`、`apps/desktop/src/main/ipc/providers.ts`、`apps/desktop/src/preload/api.ts`、`apps/desktop/tests/ipc/provider-settings.test.ts`、`apps/desktop/tests/security/provider-secret-redaction.test.ts`、`tests/release/model-provider-evidence.schema.json`、`runtimes/python-worker/pyproject.toml`、`uv.lock`。
- **创建 / 修改：** 提供经过校验的环境 / 组织级 Provider Profile 加载器、`secret_ref` 与 SecretResolver 集成、显式手动触发的连接测试入口及脱敏 evidence；实现 Task 23 声明的 Main / preload `providers.list/testConnection/saveProfile/replaceCredential` 窄接口并桥接 Python Model Gateway。只允许由 Gateway 经 Policy、预算、health 和 LiteLLM adapter 发出低敏 canary 请求；API Key 只能由 SecretResolver 写入 OS secure storage / Cloud Secret Manager，Profile 仅持有 `secret_ref`，不得把 API Key 写入 Renderer 持久状态、manifest、普通配置、日志、异常、Event、Evidence 或测试快照。为真实 Provider / Model 记录 license、数据处理与安全审查结果。
- **接口 / Schema：** `ProviderProfile {provider_id, model, capabilities, priority, is_local, secret_ref, enabled}`；`test_provider_connection(provider_id, capability) -> ProviderConnectionResult {status, provider_id, model, capability, latency_ms, trace_id, safe_error_code}`。明文 credential 只允许在 adapter 调用期间存在内存中。
- **测试文件：** 上述三个 `tests/models/test_provider_*.py` 与 release evidence schema test。
- **RED：** 测试无效 / 重复 Provider、未知 capability、缺失 SecretRef、SecretResolver 不可用、明文 API Key 被序列化、连接错误泄露 credential、测试入口绕开 Policy / 预算、LOCAL_ONLY 错误路由；先运行 Model tests，预期新入口或约束缺失而失败。Fake-only evidence 必须被判定为没有真实连接证据。
- **最小实现：** 配置解析只生成现有 `ModelProviderConfig`；连接测试复用已配置 Gateway 与 SecretResolver，返回不含输入 / Key 的结果；真实连接使用明确开启的 `SHULIU_MODEL_CONNECTION_TEST=1` 与外部注入 Secret，普通 CI 不读取真实凭据。
- **GREEN 命令：** `uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/models -q`；持有获批 Provider 配置与 Secret 的环境执行 `SHULIU_MODEL_CONNECTION_TEST=1 uv run --locked --package shuliu-python-runtime python -m shuliu_runtime.models.connection --provider <configured-id> --capability <configured-capability>`。
- **Refactor：** 配置加载、Secret 解析、Gateway 路由和 probe 输出分离；Provider 次序仍来自配置；日志 / Trace 只记录 `secret_ref` 与安全错误码。
- **验收命令：** `pnpm test:unit`；`pnpm test:security`；真实连接命令及 `tests/release/model-provider-evidence.schema.json` 校验；license / 数据处理 / 安全审查记录入 Release Evidence。
- **完成标准：** 缺 Secret 或配置时安全失败；真实请求必须通过 Gateway、Policy、预算、SecretResolver；外部返回可以证明真实配置 Provider 成功完成 canary；CI Fake 测试与真实连接证据分开。无真实 Provider 配置或 Secret 时本任务保持未完成。
- **Release Gate：** `real_model_provider_connection` 与 `model_provider_license_security`；任一缺失 / not-run 均 BLOCKED。
- **Commit：** `feat: configure secure model providers and connection checks`

#### Task 23 与 Task 28 的 Provider 设置职责边界

| 用户操作 / 能力 | 负责任务 | UI / 实际实现文件 | 验收标准 |
|---|---|---|---|
| Provider / Model 选择与能力展示 | Task 23 | `apps/desktop/src/renderer/routes/settings/providers.tsx`、`apps/desktop/src/renderer/components/provider-profile-form.tsx` | 可选择配置中可用 Provider / Model 与 capability；未知 Provider / Model 有明确错误；不允许 Skill 固定 Provider。 |
| API Key 输入 | Task 23 | `apps/desktop/src/renderer/components/provider-profile-form.tsx`、`apps/desktop/src/renderer/api/providers.ts`、`apps/desktop/src/preload/api.ts` | 使用受控敏感输入；只在用户操作时经 typed preload 传递；测试证明不进入 localStorage、日志、Event、异常或 snapshot，并在完成后清空。 |
| 手动连接测试 | Task 23（界面）；Task 28（真实调用） | `apps/desktop/src/renderer/routes/settings/providers.tsx`、`apps/desktop/src/main/ipc/providers.ts`、`runtimes/python-worker/shuliu_runtime/models/connection.py` | Task 23 用 Fake IPC 验证 pending/success/failure 状态；Task 28 用已配置真实 Provider / Secret，经 Gateway、Policy、预算和 SecretResolver 发低敏 canary；Fake 不能满足真实 Gate。 |
| 保存 Provider / Model 配置 | Task 23（界面）；Task 28（后端） | `apps/desktop/src/renderer/api/providers.ts`、`apps/desktop/src/main/ipc/providers.ts`、`runtimes/python-worker/shuliu_runtime/models/config.py` | 配置保存只保留 Provider / Model / capability / SecretRef，不保留明文 Key；未配置 Secret 时 fail closed。 |
| 更换 / 替换 API Key | Task 23（界面）；Task 28（后端） | `apps/desktop/src/renderer/components/provider-profile-form.tsx`、`apps/desktop/src/main/ipc/providers.ts`、`runtimes/python-worker/shuliu_runtime/models/config.py` | 显示现有凭据“已配置”状态但不回显；新 Key 保存到 secure storage 并更新 SecretRef；旧引用按明确替换流程处理；日志与审计只记录 SecretRef / safe error code。 |

Task 23 的 UI 可先用 Fake IPC 完成可交互验收；Task 28 才把相同 typed API 接到真实 Provider、Gateway 与安全存储。两个任务均不得把 API Key 暴露给 Skill、Browser Runtime 或普通 Renderer 持久化层。

### Task 29 — Persistent ModelCall / Cost / Budget

- **目标：** 将 Model Gateway 的调用记录、实际成本和预算预留 / 结算落到 Cloud PostgreSQL，进程重启后仍可查询且并发预留不会超支。
- **前置依赖：** Tasks 05、15；migration 使用当前 `0004_knowledge_pgvector.py` 之后的版本号。
- **具体文件路径：** `services/cloud-api/app/persistence/postgres.py`、`services/cloud-api/migrations/versions/0005_model_usage_budget.py`、`runtimes/python-worker/shuliu_runtime/models/postgres_usage.py`、`runtimes/python-worker/shuliu_runtime/models/gateway.py`、`runtimes/python-worker/tests/models/test_persistent_model_calls.py`、`runtimes/python-worker/tests/models/test_persistent_budget.py`、`services/cloud-api/tests/persistence/test_postgres_migrations.py`、`tests/integration/test_model_usage_persistence.py`。
- **创建 / 修改：** 建立按 organization 隔离的 `model_calls`、`model_budget_accounts` 与 `model_budget_reservations`；保存 requested / resolved route、model version、usage、latency、cost、error code、trace 和状态，不保存 prompt、completion 或 Secret。预算预留在调用前原子执行，成功按实际 usage 结算，确定未发送时释放；结果未知时保留 reservation 并进入对账，不能静默返还预算或重复扣款。
- **接口 / Schema：** `PersistentModelCall {call_id, organization_id, trace_id, requested_capability, provider_id, model, model_version, usage, cost, status, created_at}`；为 Gateway call 与现有 `ModelCallSink.record()`、`ModelBudget.reserve()/settle()/release()` 补充稳定 `call_id`；durable adapters 以 organization + call / reservation identity 幂等写入。
- **测试文件：** 上述 persistence / budget / migration / integration tests。
- **RED：** 先增加数据库重开后读取、重复 settle 不重复扣费、并发预算 reservation 不超限、失败 / 未发送释放、未知 outcome 不释放、成本精度和组织隔离断言；使用真实 PG migration test，预期表 / adapter 缺失而失败。
- **最小实现：** 新增扩展式 migration 与事务化 repository；Gateway 仍只依赖原有 ports；以 `call_id` / reservation ID 幂等写入。
- **GREEN 命令：** `uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/models -q`；`uv run --locked --package shuliu-cloud-api pytest services/cloud-api/tests/persistence/test_postgres_migrations.py -q`；`uv run --locked --package shuliu-cloud-api pytest tests/integration/test_model_usage_persistence.py -q`。
- **Refactor：** 统一 Cloud PostgreSQL transaction boundary；金额用 Decimal / 明确货币精度；持久化失败时 Gateway Fail Closed，不以内存预算降级。
- **验收命令：** `pnpm test:migration`；`pnpm test:integration`；`pnpm test:security`；PG17 / PG18 migration matrix。
- **完成标准：** ModelCall、实际用量、成本和预算 reservation 可跨进程恢复；并发、重试、失败路径按测试所示保持一致；无明文 Secret / 模型输入输出落库。
- **Release Gate：** `model_usage_budget_persistence`；migration、restart、duplicate settlement 任一证据缺失均 BLOCKED。
- **Commit：** `feat: persist model usage and budget accounting`

### Task 30 — Real Embedding / Knowledge Retrieval Closure

- **目标：** 用真实配置的 Embedding Provider 完成 Model Gateway → Embedding → pgvector ingest / query → ACL-first retrieval 的端到端闭环。
- **前置依赖：** Tasks 16、28、29。Embedding profile、维度和真实 Provider Secret 必须显式配置；维度与现有 pgvector schema 不兼容时先做向前兼容 migration，不得截断或静默重建向量。
- **具体文件路径：** `services/cloud-api/app/knowledge/embedding.py`、`services/cloud-api/app/knowledge/ingest.py`、`services/cloud-api/app/knowledge/retrieval.py`、`services/cloud-api/app/persistence/postgres.py`、`services/cloud-api/migrations/versions/0006_knowledge_embedding_profile.py`、`services/cloud-api/tests/knowledge/test_embedding_gateway_integration.py`、`services/cloud-api/tests/knowledge/test_real_embedding_retrieval.py`、`tests/integration/test_real_knowledge_retrieval.py`。
- **创建 / 修改：** 将现有 Gateway embedding capability 与 ingest / retrieve port 接实；索引记录 embedding profile、维度和索引版本；同一 profile 才可比较向量。查询和入库均记录 ModelCall / budget；ACL 候选集仍必须先于 pgvector 排序。保留 FakeEmbedding 做确定性单测，另设明确的凭据门控真实测试。
- **接口 / Schema：** `EmbeddingProfile {profile_id, provider_id, model, dimensions, capability="embedding", privacy_levels, index_version}`；`embed(texts, profile) -> vectors`；`KnowledgeRetriever.retrieve(query, trusted_context, top_k)` 返回引用及命中，不返回未授权 Chunk。
- **测试文件：** 上述 Knowledge tests 与现有 `test_acl_before_vector_search.py`、`test_revocation_invalidation.py`。
- **RED：** 测试 Gateway 实际返回向量并被 ingest 持久化、query 经过同一 profile 并命中预期文档、维度 / profile 不匹配拒绝、LOCAL_ONLY 不出网、撤权后无命中、真实连接门控未配置时 evidence 为 BLOCKED；PG integration 应在现有 fake-only 边界失败。
- **最小实现：** 仅添加 profile 校验与 Model Gateway adapter；复用已存在的 ACL-first 查询，禁止新增本地向量库或绕过 ModelCall / budget。
- **GREEN 命令：** `uv run --locked --package shuliu-cloud-api pytest services/cloud-api/tests/knowledge -q`；`pnpm test:migration`；`uv run --locked --package shuliu-cloud-api pytest tests/integration/test_real_knowledge_retrieval.py -q`；设置显式 Provider Secret 后运行真实 embedding integration marker。
- **Refactor：** Provider 适配与 Knowledge 索引 schema 解耦；维度/profile 变更走显式迁移与重嵌入状态，禁止部分更新冒充完整索引。
- **验收命令：** `pnpm test:migration`；`pnpm test:integration`；`pnpm test:security`；配置 Provider 下真实 Embedding 调用、pgvector 检索及 ModelCall / budget evidence。
- **完成标准：** 真实 embedding 被成功调用并产生持久化向量；Knowledge 真实查询完成 ACL-first 命中闭环；真实配置缺失、provider 错误、维度错配或预算拒绝均 Fail Closed。
- **Release Gate：** `real_embedding_knowledge_retrieval`；FakeEmbedding-only、未运行真实 provider 或无数据库证据均 BLOCKED。
- **Commit：** `feat: complete real embedding knowledge retrieval`

### Task 31 — PDF / Word / Excel Knowledge Ingestion

- **目标：** 将 PDF、Word（DOCX）与 Excel（XLSX）安全解析成通用 Document / Chunk，再调用 Knowledge ingest 与真实 Embedding 流程；不添加岗位业务字段。
- **前置依赖：** Tasks 16、30。Parser 依赖必须在加入 lockfile 前完成许可证、维护状态、安全公告和文件处理限制审查；未批准时停止该任务，不把解析推迟给 Release Gate。
- **具体文件路径：** `services/cloud-api/app/knowledge/parsers/__init__.py`、`services/cloud-api/app/knowledge/parsers/base.py`、`services/cloud-api/app/knowledge/parsers/pdf.py`、`services/cloud-api/app/knowledge/parsers/docx.py`、`services/cloud-api/app/knowledge/parsers/xlsx.py`、`services/cloud-api/app/knowledge/document_ingestion.py`、`services/cloud-api/tests/knowledge/test_pdf_parser.py`、`services/cloud-api/tests/knowledge/test_docx_parser.py`、`services/cloud-api/tests/knowledge/test_xlsx_parser.py`、`services/cloud-api/tests/knowledge/test_document_ingestion.py`、`services/cloud-api/tests/fixtures/knowledge/`、`services/cloud-api/pyproject.toml`、`uv.lock`、`tests/release/dependency-license-policy.yaml`。
- **创建 / 修改：** 支持 `.pdf`、`.docx`、`.xlsx` 的 MIME / 文件类型验证、大小 / 页数 / sheet / 解压比例上限、结构化解析、来源页码 / sheet 引用、文本规范化与 chunking；解析后调用 Task 30 Knowledge ingest。扫描件 OCR 不属于本任务；遇到无可提取文本的 PDF 返回明确 `DOCUMENT_TEXT_UNAVAILABLE`，不能伪装空文档成功。
- **接口 / Schema：** `DocumentParser.parse(file_stream, filename, mime_type) -> ParsedDocument {text_sections, source_references, warnings}`；`ingest_file(input, trusted_authorization_context) -> document_id`；拒绝不支持格式、超限文件、宏 / 加密或畸形容器按明确错误返回。
- **测试文件：** 所列 parser / ingestion tests，fixtures 覆盖有效、畸形、超限、空文本与恶意压缩比样本。
- **RED：** 先写各格式文档、来源引用、边界 / 损坏 / zip bomb 拒绝和 ingest 端到端断言；运行 Knowledge parser tests，预期 Parser / dispatcher 缺失而失败。
- **最小实现：** 采用经许可证审查批准的 parser packages；每种格式独立 adapter；统一通用 `KnowledgeChunkInput`，不写业务抽取规则。
- **GREEN 命令：** `uv run --locked --package shuliu-cloud-api pytest services/cloud-api/tests/knowledge/test_pdf_parser.py services/cloud-api/tests/knowledge/test_docx_parser.py services/cloud-api/tests/knowledge/test_xlsx_parser.py services/cloud-api/tests/knowledge/test_document_ingestion.py -q`。
- **Refactor：** parser 不访问 DB、Provider Secret 或 OS；文件临时存储按策略清理；解析错误不留下半成品 Document / Chunk；依赖 license 证据与 lockfile 一致。
- **验收命令：** 上述 parser tests；`pnpm test:security`；`pnpm test:integration`；依赖许可证 / 漏洞扫描；PG migration 与真实 Embedding ingestion integration。
- **完成标准：** PDF / DOCX / XLSX 均有有效与 adversarial fixture；来源可追溯；ACL、真实 Embedding 和事务写入闭环成功；OCR 保持明确不支持。
- **Release Gate：** `knowledge_document_ingestion_pdf_docx_xlsx`；依赖审查、解析安全测试或真实入库证据缺失均 BLOCKED。
- **Commit：** `feat: parse and ingest PDF Word and Excel knowledge`

### Task 32 — Trusted Knowledge RBAC / Trace / Audit

- **目标：** 把认证主体、组织 Membership、Role / Permission 与 Knowledge ACL 连接起来；授权上下文只能由服务端构造，访问与来源进入 Trace / Audit。
- **前置依赖：** Tasks 04、07、16、21。
- **具体文件路径：** `services/cloud-api/app/knowledge/dependencies.py`、`services/cloud-api/app/knowledge/routes.py`、`services/cloud-api/app/knowledge/audit.py`、`services/cloud-api/app/knowledge/acl.py`、`services/cloud-api/app/persistence/postgres.py`、`services/cloud-api/tests/knowledge/test_routes_trusted_authorization.py`、`services/cloud-api/tests/knowledge/test_retrieval_trace_audit.py`、`tests/integration/test_knowledge_server_rbac.py`。
- **创建 / 修改：** 通过已认证 User 与当前 Organization Membership 从 Identity / RBAC 服务端计算 scopes、classifications、permissions、privacy、budget_ref；HTTP 请求体只允许 query / top_k 等查询参数，忽略或拒绝 caller 提供 organization_id、subject_id、scope、classification、permission、budget_ref。查询、命中引用、拒绝和撤权产生带 trace_id 的结构化 Trace / Audit 记录，不写查询敏感正文或 Secret。
- **接口 / Schema：** `KnowledgeAuthorizationContext` 只由 `build_knowledge_context(authenticated_principal, membership, rbac)` 构造；`POST /knowledge/search {query, top_k}`；response 只含已授权 `KnowledgeHit`；audit payload 包含 subject / org / trace / document refs / outcome / policy version。
- **测试文件：** 所列 route / audit / integration tests。
- **RED：** 以恶意请求体伪造其他 organization、permissions、scope、classification 和预算，断言不会越权；无 Membership、已撤销 Permission、RBAC 服务异常时拒绝；断言 Trace 与 append-only Audit 写入及结果引用存在且正文 / Secret 不出现。当前只传入 dataclass 的单元路径应不能满足 HTTP 信任测试。
- **最小实现：** 新建窄 Knowledge Router 与 dependency；通过现有 Identity / RBAC port 与 persistence / Event audit port 生成上下文并调用既有 ACL-first Retriever。
- **GREEN 命令：** `uv run --locked --package shuliu-cloud-api pytest services/cloud-api/tests/knowledge -q`；`uv run --locked --package shuliu-cloud-api pytest tests/integration/test_knowledge_server_rbac.py -q`。
- **Refactor：** 客户端不能注入授权字段；RBAC 查询与向量检索同一 organization transaction context；Audit 追加失败时敏感检索 Fail Closed；日志字段统一脱敏。
- **验收命令：** `pnpm test:integration`；`pnpm test:security`；PG17 / PG18 Knowledge integration；权限撤销、跨组织伪造、Trace / Audit evidence 检查。
- **完成标准：** end-to-end 授权来自服务端认证身份和有效 Membership；caller 伪造字段不能改变授权；检索前 ACL、撤权生效，trace/audit 可追踪且无敏感正文泄漏。
- **Release Gate：** `knowledge_server_rbac_trace_audit`；无服务端身份来源、越权测试或审计证据时 BLOCKED。
- **Commit：** `feat: enforce trusted knowledge authorization and audit`

### Task 33 — Durable Tool Idempotency

- **目标：** 为真实副作用 Tool 提供进程重启后仍有效的幂等 claim / result store；保留 `InMemoryIdempotencyStore` 仅供测试或短期进程使用。
- **前置依赖：** Tasks 05、10、13、30（Task 30 仅作为顺序 migration base；Tool runtime 不依赖 Knowledge）。Cloud / Worker 共享动作使用 PostgreSQL；仅设备本地 task state 使用 SQLite；无可用 durable backend 时 Tool Broker Fail Closed。
- **具体文件路径：** `services/cloud-api/app/persistence/postgres.py`、`services/cloud-api/migrations/versions/0007_tool_idempotency.py`、`runtimes/python-worker/shuliu_runtime/tools/idempotency.py`、`runtimes/python-worker/shuliu_runtime/tools/postgres_idempotency.py`、`runtimes/python-worker/shuliu_runtime/persistence/sqlite_idempotency.py`、`runtimes/python-worker/shuliu_runtime/tools/broker.py`、`runtimes/python-worker/tests/tools/test_durable_idempotency.py`、`runtimes/python-worker/tests/persistence/test_sqlite_idempotency_restart.py`、`tests/integration/test_postgres_tool_idempotency.py`、`tests/integration/test_tool_idempotency_crash.py`、`services/cloud-api/tests/persistence/test_postgres_migrations.py`。
- **创建 / 修改：** durable schema 以 organization / tool / operation scope + idempotency_key 唯一约束，保存 request digest、状态、经过脱敏的结果引用及时间；状态区分 `CLAIMED`、`DISPATCHED`、`COMPLETED`、`INDETERMINATE`。只有确认尚未 dispatch 才能释放 Claim；dispatch 后异常或 ACK 丢失必须保留为待核验，禁止删除 key 后直接重做。DB/SQLite store unavailable 时禁止副作用执行。
- **接口 / Schema：** durable `IdempotencyStore` 支持 `begin`、`mark_dispatched`、`complete`、`mark_indeterminate`、`release_before_dispatch`；同 key 不同 digest 返回 CONFLICT，同 key 已完成返回同一结果，同 key 执行中 / 结果未知只允许 WAIT / RECONCILE。
- **测试文件：** 所列 durable store、restart、migration、crash integration tests。
- **RED：** 先用计数型 Fake Side Effect Tool 验证：并发重复只 dispatch 一次；重开 SQLite / PostgreSQL 后返回已存结果；ACK 丢失后状态为 INDETERMINATE 且不重复 dispatch；digest 冲突、DB 不可用拒绝；migration schema 缺失而失败。不能只用 Mock store 证明持久化。
- **最小实现：** 新增 PG / SQLite adapters 并接到 Broker；加入 dispatch 前后状态提交；对未知结果返回 `RECONCILIATION_REQUIRED`，绝不自动猜成功或失败。
- **GREEN 命令：** `uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/tools runtimes/python-worker/tests/persistence -q`；`uv run --locked --package shuliu-cloud-api pytest tests/integration/test_postgres_tool_idempotency.py tests/integration/test_tool_idempotency_crash.py -q`。
- **Refactor：** Store port 与 backend 分离；事务 / unique constraint 保证并发唯一；结果持久化遵守 Evidence retention 与敏感字段脱敏。
- **验收命令：** `pnpm test:migration`；`pnpm test:integration`；`pnpm test:security`；PG17 / PG18 并发、重启、ACK loss 故障注入。
- **完成标准：** Task 27 所需 durable evidence 可核验；真实副作用 Tool 未指定 durable adapter 或任何 store 失败时不能启用；不存在进程内存幂等冒充持久化。
- **Release Gate：** `durable_idempotency`；跨进程 restart、ACK loss、重复请求或 PG17 / PG18 任一未通过则 BLOCKED。
- **Commit：** `feat: add durable tool idempotency stores`

### Task 34 — Full Process Crash Recovery

- **目标：** 将 Task 13 Checkpoint / Reconciler、Task 22 Electron Sidecar Supervisor 和 Task 33 durable idempotency 接成真实进程退出 / 重启恢复闭环。
- **前置依赖：** Tasks 13、22、26、33。不能以 checkpoint 单元测试、重建 Python 对象或 scheduler persistence 代替进程级故障注入。
- **具体文件路径：** `apps/desktop/src/main/runtime/supervisor.ts`、`apps/desktop/src/main/runtime/recovery-coordinator.ts`、`apps/desktop/tests/ipc/runtime-crash-recovery.test.ts`、`runtimes/python-worker/shuliu_runtime/recovery/process_recovery.py`、`runtimes/python-worker/tests/recovery/test_process_restart_recovery.py`、`tests/integration/test_full_process_recovery.py`、`tests/integration/test_failure_recovery.py`、`.github/workflows/ci.yml`、`.github/workflows/windows-validation.yml`。
- **创建 / 修改：** 有界重启/backoff、health handshake、Checkpoint 版本校验、pending Approval 等待恢复、Worker lease / fencing 校验、durable side-effect reconciliation、Crash Event / Trace、连续崩溃 Safe Mode。结果未知的外部副作用进入 WAIT / SAFE_MODE / DEAD_LETTER，不自动重放；启动参数和凭据仍不含长期 Secret。
- **接口 / Schema：** `RuntimeRestartPolicy {max_restarts, backoff, health_timeout}`（数值可配置）；`recover_process(run_id) -> RESUME | WAIT | SAFE_MODE | DEAD_LETTER`；与 Task 13 `recover(run_id)`、Task 33 reconciliation 状态保持一致。
- **测试文件：** 所列 Desktop、Python subprocess 和 integration tests。
- **RED：** 使用子进程实际执行 Workflow：分别在 checkpoint 前、Tool dispatch 后 / ACK 前、Approval 等待中、checkpoint 写入后杀死 Runtime；重启后验证已完成副作用不重复、未知结果不续跑、Approval 不跳过、fencing 旧 Worker 被拒绝、连续崩溃进入 Safe Mode、Event / Audit trace 连续。先运行目标 tests，当前仅组件级恢复时预期失败。
- **最小实现：** Desktop Supervisor 通过 NDJSON handshake 重启 Sidecar；新 Runtime 从持久化状态调用统一 recovery coordinator，先核验 durable idempotency / 外部状态再恢复；重试次数和延迟由配置提供。
- **GREEN 命令：** `pnpm --filter @shuliu/desktop test -- runtime-crash-recovery`；`uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/recovery -q`；`uv run --locked --package shuliu-cloud-api pytest tests/integration/test_full_process_recovery.py -q`。
- **Refactor：** Supervisor 只管理进程健康，不承载 Workflow 业务；Recovery coordinator 以 ports 注入并保持判定可测；各失败状态写安全错误码和 Trace，不记录 Secret。
- **验收命令：** `pnpm test:ipc`；`pnpm test:integration`；`pnpm test:security`；Linux / macOS subprocess crash matrix；Windows runner 对 Sidecar restart / installer runtime health 执行平台验证。
- **完成标准：** 每个故障注入场景通过真实子进程退出和重建复现；已完成副作用不重放、未知状态 fail closed、等待态和 Safe Mode 可恢复；Windows 专属运行证据仅由 Windows runner / 实机产生。
- **Release Gate：** `process_crash_recovery`；无跨进程 checkpoint / durable idempotency 对账与恢复证据时 BLOCKED。
- **Commit：** `feat: recover foundation runs after process crashes`

### Task 35 — Skill Security Scanner / Security Gate

- **目标：** 在 Skill staging 前对实际安装的完整包内容执行恶意代码 / 内容安全扫描；扫描结果必须独立于 publisher signature、checksum 和兼容性校验，不能相互替代。
- **前置依赖：** Tasks 17–18；执行前先完成扫描引擎的维护状态、安全公告、许可证、离线能力、跨平台支持和规则更新机制审查。无获批且可在目标环境执行的扫描引擎时，本任务与 Skill 激活保持 BLOCKED，不得用 Fake 作为真实扫描证据。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/skills/security_scan.py`、`runtimes/python-worker/shuliu_runtime/skills/lifecycle.py`、`runtimes/python-worker/tests/skills/test_security_scan.py`、`runtimes/python-worker/tests/skills/test_security_scan_integration.py`、`runtimes/python-worker/tests/fixtures/skills/eicar.com`、`tests/release/skill-scanner-policy.yaml`、`tests/release/gate-manifest.yaml`、`tests/release/test_skill_security_gate.py`、`.github/workflows/ci.yml`、`uv.lock`（仅当通过依赖审查的 scanner SDK 需要时）。
- **创建 / 修改：** 定义可替换 `SkillSecurityScanner` port 和 `CLEAN | DETECTED | ERROR` 结果；把扫描接入签名 / checksum 验证之后、staging 之前；超时、scanner 不可用、结果未知或检测到内容一律拒绝安装和激活；只扫描已经验证 checksum 的字节并记录 package digest / scanner engine version / ruleset version / verdict，不记录包内 Secret 或扫描内容。
- **接口 / Schema：** `scan(package_path, expected_checksum, timeout) -> ScanEvidence {status, package_digest, engine, engine_version, ruleset_version, scanned_at}`；仅 `CLEAN` 且 digest 与已验证 package 相同可继续；其余结果 Fail Closed。真实 engine / 版本 / ruleset 由完成依赖审查后确定并记录，不在 Skill manifest 内由发布者自选。
- **测试文件：** `runtimes/python-worker/tests/skills/test_security_scan.py`、`runtimes/python-worker/tests/skills/test_security_scan_integration.py`、`tests/release/test_skill_security_gate.py`。
- **RED：** 先测试感染样本、scanner timeout、scanner unavailable、scanner error、checksum 与扫描字节不一致均阻止 staging / active pointer 切换；clean verdict 只对精确 package digest 有效。真实 scanner integration 使用 EICAR 测试字符串（不是恶意软件），验证被识别；Fake scanner 测试不得满足 Release Gate。
- **最小实现：** 先 pin 经审查的 scanner engine 与规则更新来源，再实现适配器、超时和 Fail Closed 集成；CI runner 真正运行扫描引擎并保存脱敏 evidence；不得把签名校验、checksum、静态 lint 或独立进程隔离标成安全扫描完成。
- **GREEN 命令：** `uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/skills/test_security_scan.py runtimes/python-worker/tests/skills/test_security_scan_integration.py -q`；`pnpm test:security`。
- **Refactor：** scanner port 与具体 engine 分离；证据 schema 对 engine / ruleset 有版本；包读取限制、timeout 和失败分类共用统一策略；离线 / 未支持平台仍 Fail Closed。
- **验收命令：** `uv sync --locked --all-packages`；上述 Skill scanner tests；`pnpm test:security`；GitHub Actions 在真实配置 scanner 的 runner 上执行 CLEAN package 和 EICAR detection；检查 release manifest 将 `skill_security_scan` 标为 required 且缺失 / not-run / Fake-only 均返回 BLOCKED；依赖许可证与安全审查通过。
- **完成标准：** 真实 scanner 引擎和 ruleset 有版本化证据；EICAR 测试可检出；感染、超时、未知和不可用场景均不能进入 staging / active；`skill_security_scan` gate 只接受真实引擎 evidence；所有已支持 OS 的 scanner availability 有 runner 证据，不支持的环境禁止 Skill 激活。
- **Release Gate：** `skill_security_scan`；无引擎、许可证审查、真实扫描运行、干净包 / EICAR 结果、规则版本或 Fail Closed 证据时 BLOCKED。此 Gate 在 Task 35 完成后仍须由 Task 27 汇总检查，不能在 Task 27 临时补做扫描实现。
- **Commit：** `feat: scan skill packages before staging`

### Task 36 — Platform Skill Sandbox Providers

- **目标：** 为目标 OS 提供真实可核验的 Skill sandbox provider；Provider 必须报告并实际实施文件范围、网络范围、Secret 隔离、Foundation DB 隔离和禁止直接 OS 控制。OS 机制未被 runner 攻击测试证明时保持 `UnavailableSandboxProvider`，不得启用该平台的 executable Skill。
- **前置依赖：** Task 18；先核实各 OS 官方接口、维护状态、权限模型及 Hosted Runner 限制。不能只因 `sandbox-exec`、namespace、Job Object 等 API / 命令存在就标为支持；若实现机制改变既有架构，应先新增 Pending ADR 并获确认，不修改 Accepted ADR。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/skills/platform/__init__.py`、`runtimes/python-worker/shuliu_runtime/skills/platform/linux.py`、`runtimes/python-worker/shuliu_runtime/skills/platform/macos.py`、`runtimes/python-worker/shuliu_runtime/skills/platform/windows.py`、`runtimes/python-worker/tests/skills/platform/test_linux_sandbox.py`、`runtimes/python-worker/tests/skills/platform/test_macos_sandbox.py`、`runtimes/python-worker/tests/skills/platform/test_windows_sandbox.py`、`runtimes/python-worker/tests/skills/platform/test_sandbox_attacks.py`、`.github/workflows/ci.yml`、`.github/workflows/windows-validation.yml`、`docs/risk-register.md`。
- **接口 / Schema：** 复用 `SandboxProvider.assess(policy) -> SandboxAssessment` 与 `wrap_command(command, policy, working_directory)`；`enforced_controls` 只能列实际由该 OS adapter 强制的控制。任何不支持或证据缺失均返回 unsupported 并阻止进程启动。
- **测试文件：** 所列 Linux、macOS、Windows 平台 tests；Generic CI 必须在 Linux / macOS runner 分别真实执行对应攻击测试；Windows-only 行为由 Windows runner / 实机验证。
- **RED：** 每个目标 OS 分别尝试读取用户目录 / 未授权文件、Secret 环境值、Foundation DB 文件，访问未允许网络目标、启动任意子进程 / OS 命令、绕过 Tool Broker / Policy / Approval；验证 provider 未支持或遗漏控制时 Skill 不启动。故障注入覆盖超时、主进程 crash、后代进程存活、POSIX `setsid` / `setpgid` 逃逸尝试、Windows Job Object 绑定前启动后代、取消和临时资源清理。
- **最小实现：** 选择经官方文档核实且适合当前 runner / 交互用户会话的系统隔离机制；实现 provider 和有限 allowlist；默认 deny；不能证明控制生效的 OS / 配置继续 unsupported。测试 fixture 不得作为 Release evidence。
- **GREEN 命令：** `uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/skills/platform -q`；`uv run --locked --package shuliu-python-runtime pytest runtimes/python-worker/tests/skills/test_process_host_lifecycle.py runtimes/python-worker/tests/skills/test_process_host_crash_cleanup.py -q`。
- **Refactor：** 各 OS adapter 只实现平台机制；共享 permission、Tool Broker 和 fail-closed 逻辑留在平台无关模块；证据按 OS / runner / provider version / enforced controls 分开记录。
- **验收命令：** `pnpm test:security`；Linux / macOS Generic CI 的平台攻击测试；Windows Validation 的平台攻击测试；`git diff --check`；Release manifest 的 `skill_sandbox_provider` gate 对缺失、Fake-only、not-run 均返回 BLOCKED。
- **完成标准：** 每个实际允许 executable Skill 的 OS 都有真实 provider 版本和 adversarial runner evidence；未验证 OS 仍禁用 executable Skill；进程 / PID 分离不能替代 OS provider evidence；不得宣称单一 provider 是完整 OS 级安全边界。
- **Release Gate：** `skill_sandbox_provider`；OS / provider / enforced controls / 文件、Secret、DB、网络、OS 命令攻击测试 / 进程树清理任一证据缺失则 BLOCKED。
- **Commit：** `feat: add verified platform skill sandbox providers`

### Task 27 — End-to-End / Release Gate Closure

- **目标：** 把 Architecture Spec 验收标准、ADR-027 全门禁与最小 Foundation Demo 串成可重复 Release Evidence。
- **前置依赖：** Tasks 01–26、28–36；所有 supplemental capability task 均须先完成并留存证据。
- **具体文件路径：** `packages/testing/src/fakes.ts`、`runtimes/python-worker/tests/fakes/fake_computer.py`、`runtimes/python-worker/tests/fakes/fake_worker.py`、`runtimes/python-worker/tests/fakes/fake_tool.py`、`runtimes/python-worker/tests/fakes/fake_approval.py`、`tests/e2e/test_foundation_lifecycle.spec.ts`、`tests/integration/test_failure_recovery.py`、`tests/security/test_release_security.py`、`tests/release/gate-manifest.yaml`、`tests/release/evidence-policy.yaml`、`tests/release/dependency-license-policy.yaml`、`tests/release/model-provider-evidence.schema.json`、`apps/desktop/package.json`、`apps/desktop/tests/fixtures/secure-storage-platform/main.cjs`、`apps/desktop/tests/security/secure-storage-platform.test.ts`、`.github/workflows/ci.yml`、`.github/workflows/windows-validation.yml`、`docs/release/foundation-v0.1-checklist.md`；并汇总 Tasks 28–36 列出的 Provider、Model usage、Knowledge、Idempotency、Recovery、Sandbox provider 与 Skill scan tests。
- **创建 / 修改：** TypeScript / Python FakeModel、FakeBrowser、FakeComputer、FakeWorker、FakeTool、FakeApproval；Demo Browser Skill lifecycle；release gate aggregator；evidence manifest；SBOM / dependency / license scan；可配置 evidence retention；CI 报告每个 gate pass / fail / not run。将 Tasks 28–36 的真实 Provider 连接、真实 Embedding / 文档入库、持久化 ModelCall / budget、服务端 RBAC / Trace / Audit、durable idempotency、完整 process crash recovery、经攻击性验证的 OS sandbox provider 与 `skill_security_scan` 真实 evidence 设为独立 required gates，不在 Task 27 临时实现这些能力。增加 Electron 原生 secureStorage smoke fixture：在 Mac 和 Windows runner 的 Electron Main 进程中调用真实 `safeStorage`，验证可用性、加密后读回、磁盘无明文及删除；不得用 FakeSafeStorage 作为平台证据。
- **接口 / Schema：** Release result 为 `PASS | BLOCKED`；required gates 固定为 unit / contract / integration / migration / security / IPC handshake / Windows build / installer smoke / runtime health，并增加 `secure_storage_macos`、`secure_storage_windows`、`real_model_provider_connection`、`model_provider_license_security`、`model_usage_budget_persistence`、`real_embedding_knowledge_retrieval`、`knowledge_document_ingestion_pdf_docx_xlsx`、`knowledge_server_rbac_trace_audit`、`durable_idempotency`、`process_crash_recovery`、`skill_sandbox_provider`、`skill_security_scan`；任何 missing / fail / not-run 均 BLOCKED。`skill_sandbox_provider` evidence 至少记录 OS / runner、provider 版本、实际 enforced controls、未经授权文件 / 网络 / Secret / DB / OS 命令攻击测试、进程树 timeout / crash 清理；独立进程 PID 不可充当 provider evidence。Skill scan evidence 至少记录 package digest、真实 engine / ruleset 版本、扫描时间和 verdict，Fake-only 不通过。Idempotency evidence 记录 store adapter 类型、持久化确认、重启后 key/result 对账及重复请求结果；不得记录 Secret。原生 smoke evidence 只包含 `platform`、`safe_storage_available`、`round_trip`、`plaintext_absent`、`delete_verified`、`run_id`、`commit_sha`。
- **测试文件：** 所列 E2E、integration、security、gate manifest tests，以及 Tasks 28–36 中所有 required capability tests；OS sandbox adversarial tests 分别在 Linux、macOS、Windows runner 执行；平台 smoke 由 `apps/desktop/tests/security/secure-storage-platform.test.ts` 启动 `apps/desktop/tests/fixtures/secure-storage-platform/main.cjs`，分别在 `macos-latest` 和 `windows-latest` 执行。Mac runner 的 Electron `safeStorage` 路径用于验证 Keychain；Windows runner 的 Electron `safeStorage` 路径用于验证 DPAPI。
- **原生 secure storage RED：** 先在两个原生 runner 上添加 smoke gate，但不提供 Electron fixture / adapter 调用；预期 gate 因缺少可核验证据而 `BLOCKED`。另验证 FakeSafeStorage-only 的结果不能满足两个平台 gate。
- **RED：** E2E 创建 Task → Policy → Approval → Tool → verify → Event / Audit；注入拒绝、crash、ack loss、worker stale fence、skill tamper、ACL revoke、bad update；分别缺少真实 Provider、持久化 usage、真实 Embedding、文档解析、服务端 RBAC / Audit、durable idempotency、进程恢复、真实 OS sandbox provider 或真实 Skill scan evidence 时对应 Gate 必须 BLOCKED。使用 Fake-only / InMemory store / 组件级重建 / Skill 签名校验 / PID 分离冒充真实能力也必须 BLOCKED；任何 gate missing / fail / not-run 时整体不得 PASS。
- **最小实现：** 通用 E2E 编排只连接 Fake / local test service，不在测试中调用真实账号或真实外部发送；原生 secure storage smoke 是明确例外，只调用本机 Electron OS secure storage API，不访问外部账号或发送数据。CI 将 Linux、macOS sandbox adversarial tests 与 Windows sandbox / secure storage smoke 分别绑定到对应 runner，并将平台结果作为独立 evidence gate。
- **GREEN 命令：** `pnpm test:e2e`；`pnpm test:integration`；`pnpm test:security`；`pnpm test:ipc`；Tasks 28–36 的 migration / integration / crash / sandbox / Skill scan tests；带外部配置的真实 Provider / Embedding tests；在 `macos-latest` 和 `windows-latest` 分别执行 `pnpm --filter @shuliu/desktop test:secure-storage-platform`；Windows build / installer Gate 在 Windows runner / 实机单独运行。
- **Refactor：** gate manifest 是唯一 required list；Evidence content 与 metadata / retention policy 分离；性能目标在测量环境和阈值获批前仅记录，不作阻断指标。
- **验收命令：** `pnpm test:unit`；`pnpm test:contract`；`pnpm test:integration`；`pnpm test:migration`；`pnpm test:security`；`pnpm test:ipc`；`pnpm test:e2e`；`pnpm --filter @shuliu/desktop test:secure-storage-platform`（必须分别在原生 macOS 和 Windows runner 成功）；Linux / macOS / Windows sandbox provider adversarial tests；Windows build / installer / Computer Use / upgrade / rollback；runtime health gate。
- **完成标准：** Spec §20.2 每项至少有一条自动化验收；ADR-027 与本计划列出的所有 required gates 有可查证通过证据；Mac Keychain 与 Windows DPAPI 的真实 Electron `safeStorage` adapter 验收均通过并保存不含 Secret 的 runner evidence；Tasks 28–36 的真实能力、进程恢复、Sandbox provider 与 Skill scan Gate 全部通过；任何失败 / 缺失阻止 Release；Foundation 仍无岗位业务逻辑。
- **Commit：** `test: add foundation end-to-end release gates`

## 非阻塞实现配置及负责人

这些不再是 Architecture Critical TBD，但必须在指定集成或发布门槛前完成；若未满足，相关外接能力或 Release 保持 BLOCKED：

| 配置 | 负责人 | 截止门槛 | 未满足时处理 |
|---|---|---|---|
| Desktop Runtime restart / backoff 和 Safe Mode 恢复参数 | Desktop / Runtime Owner | Task 34 process-crash integration 前 | Task 34 / Release Gate BLOCKED |
| 生产 Cloud 部署目标 / Identity Provider | Cloud / Security Owner | 生产 Cloud 接入前 | 使用 Fake Provider 做测试；不得声称生产身份已接入 |
| Worker device key backend、Session TTL / rotation、Lease / heartbeat / grace 数值 | Worker / Security Owner | Windows 设备试点前 | 拒绝设备注册或任务提交 |
| Schedule IANA timezone、DST 语义、misfire 默认配置 | Scheduler Owner | Scheduler Integration 前 | 拒绝缺 timezone 的生产 Schedule |
| Provider / Model Profile、真实 Secret 引用与连接 evidence | Model Gateway Owner | Task 28 / 真实 Provider Gate 前 | 只允许 Fake Provider；真实调用 Gate BLOCKED |
| ModelCall / 成本 / 预算 durable ledger | Model Gateway Owner | Task 29 / Release 前 | 不得将进程内预算或 telemetry 当作持久化完成 |
| Embedding profile / vector dimension / index migration / cache invalidation | Knowledge Owner | Task 30 / 真实 Knowledge Gate 前 | 保留 FakeEmbedding；真实检索 Gate BLOCKED |
| PDF / DOCX / XLSX parser packages 与许可证审查 | Knowledge Owner / Repository Owner | Task 31 添加 lockfile 依赖前 | 未通过 license / security review 不引入依赖，Document Ingestion Gate BLOCKED |
| 服务端可信 Knowledge RBAC context 与 Trace / Audit | Cloud / Security Owner | Task 32 / 真实 Knowledge API 前 | 禁止暴露真实 Knowledge 搜索 API |
| Tool durable idempotency backend 与结果保留配置 | Tool / Persistence Owner | Task 33 / 任何真实副作用 Tool 激活前 | 只允许测试 / 短时 Fake Tool；真实副作用 Tool 禁止启用 |
| 完整 Sidecar / Workflow process crash recovery 与恢复参数 | Desktop / Runtime Owner | Task 34 / Release 前 | Process Recovery Gate BLOCKED；未知动作不得自动重放 |
| Permission 粒度、Tool timeout 与有界重试值 | Tool / Policy Owner | Tool / Workflow Integration 前 | 对应 Tool 不可启用 |
| 每个 OS 的 Skill sandbox backend / signature trust root | Skill Security Owner | Task 36 / `skill_sandbox_provider` Release Gate 前 | 对该 OS 禁止激活 |
| Skill Security Scanner engine、规则来源与 License | Skill Security Owner / Repository Owner | Task 35 / Skill Security Gate 前 | Skill 不得 staging / 激活；`skill_security_scan` 保持 BLOCKED |
| 数据静态加密策略与外部渗透测试范围 | Security Owner | 生产数据写入前 | Production Release BLOCKED |
| Evidence retention、容量、export / deletion policy | Security / Operations Owner | Release 前 | Release BLOCKED |
| Compatibility window、rollout、health thresholds、性能门槛 | Release Owner | Stable Release 前 | 不发布 stable |
| 第三方 lockfile / license / vulnerability allowlist | Repository Owner | 每次 Release 前 | 未批准依赖阻断 Release |

## 最终自检

- **任务总数：36（原 27 项 + 补充 Task 28–36）。**
- **直接可执行任务：36 项均有精确路径、依赖、接口 / Schema、测试、RED、最小实现、GREEN、Refactor、验收、完成标准、Release Gate 和 commit message；Task 25、Task 26、Task 34、Task 36 的 Windows 专属部分必须在 Windows runner / 实机验证。**
- **Critical Architecture TBD：无。** 原计划审计出的 7 项能力缺口与 Skill 安全扫描均有独立实施任务和 Release Gate；实施配置 / Provider credentials 缺少时对应 Gate 保持 BLOCKED，不以 Fake 或最终验收代替实现。
- **Implementation Plan 审批状态：已由用户批准；Task 21–23 的本地验证和 GitHub CI（Generic Linux / macOS、PostgreSQL 17 / 18、Windows Validation）均通过。** Task 24–26、Task 28–36 与 Task 27 是后续施工和发布门槛；Task 35 补入独立安全扫描能力，Task 36 补入平台 sandbox provider 与攻击验证，不改变已 Accepted ADR。

## 实施入口门禁

Task 23 已提交并推送；Desktop UI、真实 Electron / Python Sidecar IPC、Generic Linux / macOS、PG17 / PG18 与 Windows Validation 全绿。下一项按依赖拓扑执行 Task 24；其后完成 Task 25–26、Task 28–36，最后执行 Task 27 Release Gate。Windows-only 项不得在 Mac 上标记完成。出现架构边界变化时先更新 Architecture Spec / ADR，再继续相关任务。
