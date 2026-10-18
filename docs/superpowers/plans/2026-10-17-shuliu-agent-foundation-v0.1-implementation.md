# ShuLiu Agent Foundation V0.1 Implementation Plan

> **状态：Implementation Plan ready for approval；审批前不得开始实现。** Architecture Spec 是架构事实源，本文件是施工事实源。本文已完成 Critical TBD 收口和逐文件 TDD 拆解，但未代表代码已实现、测试已通过或 Release 已批准。

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
- Skill：V0.1 只允许 ShuLiu Official / Company Internal；可执行 Skill 独立进程隔离；禁止 Skill 直接访问 Secret、OS、任意网络或 Foundation DB；副作用只经 Tool Broker → Policy → Approval → Executor；无第三方 Skill Store。
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

1. 所有任务按编号顺序执行；只允许在依赖已完成后开始。任务依赖图必须保持有向无环。
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
| 27 | End-to-End / Release Gates | 01–26 |

以上依赖只从低编号指向高编号，不存在循环依赖。Foundation 与岗位机器人分离：`skills/examples` 只放无业务语义的协议演示 Skill。

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
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/skills/manifest.py`、`runtimes/python-worker/shuliu_runtime/skills/registry.py`、`runtimes/python-worker/shuliu_runtime/skills/lifecycle.py`、`runtimes/python-worker/shuliu_runtime/skills/signature.py`、`runtimes/python-worker/tests/skills/test_lifecycle.py`、`runtimes/python-worker/tests/skills/test_package_verification.py`、`runtimes/python-worker/tests/skills/test_permission_change.py`、`skills/examples/echo/manifest.yaml`、`skills/examples/echo/README.md`。
- **创建 / 修改：** V0.1 trusted publisher allowlist interface；状态机 DISCOVERED → DOWNLOADED → VERIFIED → STAGED → INSTALLED → ENABLED / DISABLED → DEPRECATED → REMOVED；staging 和 active version 分离。
- **接口 / Schema：** Manifest 含 id / version / publisher / signature / checksum / foundation range / skill protocol / capabilities / permissions / dependencies；`install(package) -> SkillInstallResult`。
- **测试文件：** 所列 Skill tests。
- **RED：** 测试未知 Publisher、篡改签名、checksum 错、协议不兼容、依赖缺失、权限增加未审批、升级失败后旧版本仍 enabled、活跃 Run 阻止卸载。
- **最小实现：** 校验完整后写 Staging，Contract Test 通过才切换 active pointer；示例 Skill 只做 echo，不包含岗位业务。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/skills -q`。
- **Refactor：** 生命周期转移表纯函数化；signature provider 可替换；安装失败不污染旧版本和业务数据。
- **验收命令：** `pnpm test:contract`；`pnpm test:security`；`pnpm test:integration`。
- **完成标准：** 只有可信、签名有效、兼容且权限已批准的 Skill 可启用；更新失败旧版仍可用；无第三方 Store。
- **Commit：** `feat: add verified skill lifecycle runtime`

### Task 18 — Skill Process Sandbox

- **目标：** 为可执行 Skill 提供独立进程隔离与受控能力通道；环境不满足隔离时拒绝启动。
- **前置依赖：** Tasks 06、10、17。
- **具体文件路径：** `runtimes/python-worker/shuliu_runtime/skills/sandbox.py`、`runtimes/python-worker/shuliu_runtime/skills/process_host.py`、`runtimes/python-worker/shuliu_runtime/skills/capability_proxy.py`、`runtimes/python-worker/tests/skills/test_sandbox_denials.py`、`runtimes/python-worker/tests/skills/test_tool_proxy_only.py`。
- **创建 / 修改：** 启动参数 allowlist、有限 filesystem / IPC / network policy、capability proxy；OS sandbox provider abstract interface；拒绝直接 DB / Secret / OS / arbitrary network。
- **接口 / Schema：** `SandboxPolicy {skill_id, permissions, filesystem_roots, network_allowlist, secret_refs=[]}`；Skill 通过宿主 RPC 申请 Tool capability，不接收 Secret value。
- **测试文件：** 所列 sandbox tests；平台集成测试按 OS 分目录。
- **RED：** Skill 测试进程尝试读取 Secret、访问未授权文件、直连网络、连接 DB、执行 OS command；预期 sandbox 之前均能访问或宿主判定缺失。
- **最小实现：** 进程隔离 host + 能力 RPC；对当前 OS 无法强制的限制明确拒绝 executable Skill 启动。
- **GREEN 命令：** `uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/skills/test_sandbox_denials.py runtimes/python-worker/tests/skills/test_tool_proxy_only.py -q`。
- **Refactor：** sandbox policy 不依赖 Skill 自报；对每个平台使用独立 adapter；失效时 fail closed。
- **验收命令：** `pnpm test:security`；逐平台 sandbox runner job。
- **完成标准：** 四类越权尝试均失败；Side Effect 只能经 Tool Broker；未验证 OS 不激活 Skill。
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

- **目标：** 暴露经过 Identity / RBAC / Policy 的 Foundation API 与 Worker WebSocket 接入点。
- **前置依赖：** Tasks 05、07–10、20。
- **具体文件路径：** `services/cloud-api/app/main.py`、`services/cloud-api/app/routes/tasks.py`、`services/cloud-api/app/routes/approvals.py`、`services/cloud-api/app/routes/health.py`、`services/cloud-api/app/routes/workers.py`、`services/cloud-api/tests/api/test_task_routes.py`、`services/cloud-api/tests/api/test_health.py`、`services/cloud-api/tests/api/test_authz.py`、`tests/integration/test_cloud_api_contract.py`。
- **创建 / 修改：** FastAPI app factory、dependency injection、task create/read、approval list/decision、health、Worker WS handshake；无岗位业务 endpoint。
- **接口 / Schema：** `POST /v1/tasks`、`GET /v1/tasks/{task_id}`、`GET /v1/approvals`、`POST /v1/approvals/{approval_id}/decision`、`GET /health`、`/v1/workers/ws`；输入输出由共享 JSON Schema 与 OpenAPI 一致性测试约束。
- **测试文件：** 所列 Cloud API tests。
- **RED：** 缺认证、跨 organization、缺 Permission、无效 approval context、错误 protocol handshake、未知任务；断言拒绝响应与没有副作用。
- **最小实现：** app factory 和依赖注入；路由只调用已实现的 application service，数据库通过 repository ports 注入。
- **GREEN 命令：** `uv run --package shuliu-cloud-api pytest services/cloud-api/tests/api -q`；`uv run --package shuliu-cloud-api pytest tests/integration/test_cloud_api_contract.py -q`。
- **Refactor：** 路由不含领域决策；统一 ShuLiuError mapping、request_id / trace_id 和 structured logging。
- **验收命令：** `pnpm test:contract`；`pnpm test:integration`；`pnpm test:security`。
- **完成标准：** API 认证授权在服务端执行；HTTP / WS 都遵守 schema、Policy、事件和错误模型；不存在业务机器人路由。
- **Commit：** `feat: add authenticated foundation cloud API`

### Task 22 — Electron ↔ Python IPC

- **目标：** 实现 Electron Main 管理 Python Sidecar 的 stdio + NDJSON IPC、握手、超时和恢复信号。
- **前置依赖：** Tasks 02–03、06。
- **具体文件路径：** `apps/desktop/src/main/runtime/supervisor.ts`、`apps/desktop/src/main/runtime/ndjson-channel.ts`、`apps/desktop/src/main/runtime/handshake.ts`、`apps/desktop/src/preload/api.ts`、`runtimes/python-worker/shuliu_runtime/ipc/server.py`、`runtimes/python-worker/tests/ipc/test_ndjson_server.py`、`apps/desktop/tests/ipc/handshake.test.ts`、`tests/contract/ipc-roundtrip.test.ts`。
- **创建 / 修改：** Main spawn / supervise；stdout parser 只收 NDJSON、stderr logger；preload allowlist；startup protocol handshake + runtime health；request / trace id 和 timeout。
- **接口 / Schema：** `request_id`, `trace_id`, `protocol_version`, `method`, `params`; response 为 `result | ShuLiuError`；Renderer 仅用 typed allowlist API。
- **测试文件：** 所列 IPC tests。
- **RED：** 测试 stdout 插入 log、partial line、非法 JSON、版本不匹配、超时、sidecar crash、Renderer 任意命令请求；预期握手 / 解析 / allowlist 错误未被阻断。
- **最小实现：** newline-framed JSON parser、单请求 correlation、有限重启并健康检查；连续崩溃进入 Safe Mode。
- **GREEN 命令：** `pnpm --filter @shuliu/desktop test -- ipc`；`uv run --package shuliu-python-runtime pytest runtimes/python-worker/tests/ipc -q`。
- **Refactor：** Main IPC 与 UI renderer 分离；禁止在 stdout 打印调试日志；Sidecar 启动参数无长期 Secret。
- **验收命令：** `pnpm test:ipc`；`pnpm test:contract`。
- **完成标准：** stdout 纯协议、stderr 日志、握手可验证、请求错误可关联、主进程可拒绝任意 OS 命令。
- **Commit：** `feat: add supervised NDJSON runtime IPC`

### Task 23 — Desktop UI

- **目标：** 提供创建 / 查看任务、审批、健康状态和设置的桌面界面，通过窄 preload API 工作。
- **前置依赖：** Tasks 03、08–09、11、22。
- **具体文件路径：** `apps/desktop/src/renderer/App.tsx`、`apps/desktop/src/renderer/routes/tasks.tsx`、`apps/desktop/src/renderer/routes/approvals.tsx`、`apps/desktop/src/renderer/components/runtime-health.tsx`、`apps/desktop/src/renderer/api/client.ts`、`apps/desktop/tests/ui/task-flow.test.tsx`、`apps/desktop/tests/ui/approval-flow.test.tsx`、`apps/desktop/tests/ui/health-state.test.tsx`。
- **创建 / 修改：** 通用 Foundation UI：task create/status、approval detail/decision、Runtime health / Safe Mode；不添加采购等岗位页面。
- **接口 / Schema：** Renderer 调用 `window.shuliu.tasks.create/get`、`window.shuliu.approvals.list/decide`、`window.shuliu.health.get`；权限错误用共享 ShuLiuError 呈现。
- **测试文件：** 所列 UI tests。
- **RED：** 以 Fake IPC 测试创建 Task、拒绝审批后状态、Safe Mode 提示、缺权限隐藏 / 禁用；无 UI / typed API 时失败。
- **最小实现：** 页面 + typed API client + loading/error/empty states。
- **GREEN 命令：** `pnpm --filter @shuliu/desktop test -- ui`。
- **Refactor：** 页面只读写业务 API，不持有 Secret / DB access / arbitrary filesystem access。
- **验收命令：** `pnpm --filter @shuliu/desktop test`；`pnpm --filter @shuliu/desktop typecheck`。
- **完成标准：** 用户能查看任务、Approval、runtime health；拒绝 / 错误明确可见；UI 不能绕过 Policy。
- **Commit：** `feat: add foundation desktop task and approval UI`

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
- **GREEN 命令：** `pnpm --filter @shuliu/desktop test -- update`；Windows runner 执行 `powershell -File tests/e2e/windows/test_installer_smoke.ps1`。
- **Refactor：** Update orchestrator 不负责平台打包细节；兼容性判定可单测；普通 update 不清理旧版本和 Evidence。
- **验收命令：** Windows runner / 实机运行 PyInstaller、NSIS、安装、升级、回滚；`pnpm test:migration`；`pnpm test:security`。
- **完成标准：** 所有坏包 / 坏迁移 / 健康失败都阻止启用；rollback 前做协议 / DB 检查；Windows smoke 证据已留档。
- **Commit：** `feat: add signed update and Windows installer gates`

### Task 27 — End-to-End / Release Gate Closure

- **目标：** 把 Architecture Spec 验收标准、ADR-027 全门禁与最小 Foundation Demo 串成可重复 Release Evidence。
- **前置依赖：** Tasks 01–26。
- **具体文件路径：** `packages/testing/src/fakes.ts`、`runtimes/python-worker/tests/fakes/fake_computer.py`、`runtimes/python-worker/tests/fakes/fake_worker.py`、`runtimes/python-worker/tests/fakes/fake_tool.py`、`runtimes/python-worker/tests/fakes/fake_approval.py`、`tests/e2e/test_foundation_lifecycle.spec.ts`、`tests/integration/test_failure_recovery.py`、`tests/security/test_release_security.py`、`tests/release/gate-manifest.yaml`、`tests/release/evidence-policy.yaml`、`tests/release/dependency-license-policy.yaml`、`apps/desktop/package.json`、`apps/desktop/tests/fixtures/secure-storage-platform/main.cjs`、`apps/desktop/tests/security/secure-storage-platform.test.ts`、`.github/workflows/ci.yml`、`.github/workflows/windows-validation.yml`、`docs/release/foundation-v0.1-checklist.md`。
- **创建 / 修改：** TypeScript / Python FakeModel、FakeBrowser、FakeComputer、FakeWorker、FakeTool、FakeApproval；Demo Browser Skill lifecycle；release gate aggregator；evidence manifest；SBOM / dependency / license scan；可配置 evidence retention；CI 报告每个 gate pass / fail / not run。增加 Electron 原生 secureStorage smoke fixture：在 Mac 和 Windows runner 的 Electron Main 进程中注入真实 `safeStorage`，调用 Task 06 的 `LocalSecureStorage`，验证可用性、加密后读回、磁盘无明文及删除；不得使用 FakeSafeStorage 作为平台证据。增加持久化 Tool 幂等门禁：`InMemoryIdempotencyStore` 仅可用于测试 / 短期进程；任何真实副作用 Tool 未接入 durable idempotency store 并通过崩溃 / 重启 / ACK 丢失 / 重试验证前均不得启用。
- **接口 / Schema：** Release result 为 `PASS | BLOCKED`；required gates 固定为 unit / contract / integration / migration / security / IPC handshake / Windows build / installer smoke / runtime health，并增加独立 `secure_storage_macos`、`secure_storage_windows`、`durable_idempotency` gates；任何 missing / not-run 都算 BLOCKED。幂等 evidence 至少记录 store adapter 类型、持久化确认、进程重启后的 key/result 对账及重复请求结果，不记录 Secret。原生 smoke evidence 只包含 `platform`、`safe_storage_available`、`round_trip`、`plaintext_absent`、`delete_verified`、`run_id`、`commit_sha`，不得包含 secret 明文或密文。
- **测试文件：** 所列 E2E、integration、security 与 gate manifest tests；平台 smoke 由 `apps/desktop/tests/security/secure-storage-platform.test.ts` 启动 `apps/desktop/tests/fixtures/secure-storage-platform/main.cjs`，分别在 `macos-latest` 和 `windows-latest` 执行。Mac runner 的 Electron `safeStorage` 路径用于验证 Keychain；Windows runner 的 Electron `safeStorage` 路径用于验证 DPAPI。
- **原生 secure storage RED：** 先在两个原生 runner 上添加 smoke gate，但不提供 Electron fixture / adapter 调用；预期 gate 因缺少可核验证据而 `BLOCKED`。另验证 FakeSafeStorage-only 的结果不能满足两个平台 gate。
- **RED：** E2E 创建 Task → Policy → Approval → Tool → verify → Event / Audit；注入拒绝、crash、ack loss、worker stale fence、skill tamper、ACL revoke、bad update；Release gate 少一个或 not-run 时预期 BLOCKED。使用进程内幂等存储运行真实副作用 Tool 时，`durable_idempotency` gate 必须 BLOCKED；进程重启导致 key/result 状态丢失也必须 BLOCKED。
- **最小实现：** 通用 E2E 编排只连接 Fake / local test service，不在测试中调用真实账号或真实外部发送；原生 secure storage smoke 是明确例外，只调用本机 Electron OS secure storage API，不访问外部账号或发送数据。CI 将 macOS 与 Windows smoke 分别绑定到原生 runner，并将平台结果作为独立 evidence gate。
- **GREEN 命令：** `pnpm test:e2e`；`pnpm test:integration`；`pnpm test:security`；`pnpm test:ipc`；持久化幂等存储故障注入测试；在 `macos-latest` 和 `windows-latest` 分别执行 `pnpm --filter @shuliu/desktop test:secure-storage-platform`；Windows build / installer Gate 在 Windows runner / 实机单独运行。
- **Refactor：** gate manifest 是唯一 required list；Evidence content 与 metadata / retention policy 分离；性能目标在测量环境和阈值获批前仅记录，不作阻断指标。
- **验收命令：** `pnpm test:unit`；`pnpm test:contract`；`pnpm test:integration`；`pnpm test:migration`；`pnpm test:security`；`pnpm test:ipc`；`pnpm test:e2e`；`pnpm --filter @shuliu/desktop test:secure-storage-platform`（必须分别在原生 macOS 和 Windows runner 成功）；Windows build / installer / Computer Use / upgrade / rollback；runtime health gate。
- **完成标准：** Spec §20.2 每项至少有一条自动化验收；ADR-027 所有 gate 有可查证通过证据；Mac Keychain 与 Windows DPAPI 的真实 Electron `safeStorage` adapter 验收均通过并保存不含 Secret 的 runner evidence；任何失败 / 缺失阻止 Release；Foundation 仍无岗位业务逻辑。该平台 Gate 是 Task 06 的明确后续 Release 验收项，不阻塞开始 Task 07，但两项未通过前 Foundation V0.1 不得 Release。
- **Commit：** `test: add foundation end-to-end release gates`

## 非阻塞实现配置及负责人

这些不再是 Architecture Critical TBD，但必须在指定集成或发布门槛前完成；若未满足，相关外接能力或 Release 保持 BLOCKED：

| 配置 | 负责人 | 截止门槛 | 未满足时处理 |
|---|---|---|---|
| Desktop Runtime restart / backoff 和 Safe Mode 恢复参数 | Desktop / Runtime Owner | Crash-recovery 集成验收前 | 不通过崩溃恢复门禁 |
| 生产 Cloud 部署目标 / Identity Provider | Cloud / Security Owner | 生产 Cloud 接入前 | 使用 Fake Provider 做测试；不得声称生产身份已接入 |
| Worker device key backend、Session TTL / rotation、Lease / heartbeat / grace 数值 | Worker / Security Owner | Windows 设备试点前 | 拒绝设备注册或任务提交 |
| Schedule IANA timezone、DST 语义、misfire 默认配置 | Scheduler Owner | Scheduler Integration 前 | 拒绝缺 timezone 的生产 Schedule |
| Knowledge parser / Embedding profile / index migration / cache invalidation | Knowledge Owner | 真实 Knowledge source 前 | 保留 FakeEmbedding；不启用真实检索 |
| Model Provider / Model mapping / budgets / LOCAL_ONLY eligible models | Model Gateway Owner | 接入真实 Provider 前 | 仅 Fake Provider；生产模型请求阻断 |
| Permission 粒度、Tool timeout 与有界重试值 | Tool / Policy Owner | Tool / Workflow Integration 前 | 对应 Tool 不可启用 |
| 每个 OS 的 Skill sandbox backend / signature trust root | Skill Security Owner | 对应 OS 激活 executable Skill 前 | 对该 OS 禁止激活 |
| 数据静态加密策略与外部渗透测试范围 | Security Owner | 生产数据写入前 | Production Release BLOCKED |
| Evidence retention、容量、export / deletion policy | Security / Operations Owner | Release 前 | Release BLOCKED |
| Compatibility window、rollout、health thresholds、性能门槛 | Release Owner | Stable Release 前 | 不发布 stable |
| 第三方 lockfile / license / vulnerability allowlist | Repository Owner | 每次 Release 前 | 未批准依赖阻断 Release |

## 最终自检

- **任务总数：27（符合 20–35）。**
- **直接可执行任务：27 项在用户批准 Implementation Plan 后具备精确路径、前置依赖、接口 / Schema、测试、RED、最小实现、GREEN、Refactor、验收、完成标准和 commit message；Task 25、Task 26 的 Windows 部分必须有 Windows runner / 实机。**
- **Critical Architecture TBD：无。** Cloud Provider、部署参数、时间数值、具体模型 / parser、逐 OS sandbox 后端、保留值和发布阈值均被分配到非阻塞配置表及明确门槛；安全不变量和不满足时的 fail-closed 行为已确定。
- **Implementation Plan 审批状态：等待用户审批；审批前不写产品代码。**

## 实施入口门禁

批准后从 Task 01 开始：创建 `feat/foundation-v0.1` 独立 worktree；逐任务 TDD；每个 Task 单独提交；Windows-only 项不得在 Mac 上标记完成。出现架构边界变化时先更新 Architecture Spec / ADR，再继续相关任务。
