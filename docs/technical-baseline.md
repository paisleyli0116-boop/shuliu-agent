# ShuLiu Agent 技术基线

## 阅读说明

Architecture Spec 是架构事实源。本表中的“已确认”指架构决策已写入 Spec / ADR，不表示功能已实现或验证通过。具体 Provider、部署值和 OS 隔离后端等仍按文末非阻塞参数管理。

## 已确认技术基线

| 领域 | 基线 | 依据 |
|---|---|---|
| 产品范围 | ShuLiu Foundation V0.1；只做第一层通用底座，不实现岗位机器人业务逻辑。 | Spec §§2–3 |
| Repository | pnpm workspaces Monorepo；初始目录包括 `apps/desktop`、`services/cloud-api`、`runtimes/python-worker`、`packages/protocols`、`packages/shared-types`、`packages/event-model`、`packages/testing`、`skills/examples`、`tests/contract`、`tests/integration`、`tests/e2e`、`docs`。 | ADR-017 |
| 分支与工作区 | 产品实现不得直接在 `main`；实现使用 `feat/foundation-v0.1` + 独立 worktree，采用 TDD。 | ADR-017、AGENTS.md |
| Node / Desktop | Node.js 22 LTS >=22.12；Electron + React + TypeScript；Electron Main 管理 Python Sidecar，Renderer 通过窄 IPC。 | ADR-017、Spec §§6、17.6 |
| Python | Python 3.11；Python Runtime / Worker 由 Python 进程承载。 | ADR-017、Spec §§6、17.6 |
| Python packaging | `pyproject.toml` + committed `uv.lock`；CI 使用 locked dependency resolution。 | ADR-017 |
| 测试工具 | TypeScript 使用 Vitest；Python 使用 pytest。 | ADR-017 |
| CI | GitHub Actions。通用测试可在 Linux / macOS runners 执行；Windows 专属工作单独验收。 | ADR-017 |
| Cloud 数据库 | PostgreSQL 是业务 Source of Truth。 | ADR-015 |
| Knowledge 向量库 | Cloud PostgreSQL + pgvector；V0.1 不引入独立向量数据库或第二套本地向量索引。 | ADR-013 |
| Knowledge ACL | 先按 organization / scope / classification / Permission 构造授权候选集，再执行向量检索；禁止全库 Top-K 后应用层过滤。 | ADR-013 / 023 |
| Local SQLite | 只承担 cache、outbox、inbox、checkpoint、local task state；不做通用 SQLite ↔ PostgreSQL 双向同步。 | ADR-015 |
| Electron ↔ Python | stdio + NDJSON；stdout 仅协议通道，stderr 写日志；Main 提供 allowlist IPC、启动握手和健康检查。 | ADR-014 |
| Cloud ↔ Worker | WSS + heartbeat + Lease + ACK + idempotency；一次性 Registration Token 建立设备身份，Worker 生成设备密钥，服务端分配 `worker_id`，运行时使用短期会话凭据。 | ADR-014 / 015 |
| Worker GUI | GUI 自动化运行在 Interactive User Session，不作为普通 Windows Service 执行。 | Spec §§6、17.6 |
| Tool Adapter 顺序 | API → Native API / COM → Browser DOM → UIA → Vision GUI → raw coordinates。每次动作 OBSERVE → ACT → VERIFY。 | Spec §5.1 / 18.4 |
| Browser Runtime | `browser-use` adapter。 | Spec §6.1 |
| Windows Computer Runtime | UFO² / UIA / Win32 adapter；Windows Computer Use 只在 Windows runner / 实机验证。 | Spec §§6.1、17.6 |
| GUI Agent Operator | UI-TARS Operator。 | Spec §6.1 |
| Model Gateway | ShuLiu Policy / capability layer + LiteLLM Provider integration。Skill 声明 `fast_chat`、`reasoning_high`、`vision`、`embedding` 等能力，不绑定 Provider。 | ADR-024、Spec §14 |
| Model routing | 按 capability、privacy、budget、provider health、fallback policy 选择；Provider 优先级配置化；`LOCAL_ONLY` 不得 fallback 到远端。 | ADR-024 |
| Scheduler | APScheduler 3.11.3 persistent datastore 负责 Trigger；Workflow Engine 负责 Execution；stable schedule_id、启动 replace、默认 latest、显式 all、同 Schedule 不并发、可配置 `misfire_grace_time`、副作用幂等。 | ADR-016 |
| Identity | 单公司内部 V0.1；保留 `organization_id`，不实现完整多租户 SaaS；`User + Organization Membership + Role + Permission`；Provider 经可替换抽象接口接入。 | ADR-018 |
| MCP | 外部 Tool / Connector 接入协议，不替代七大内部领域协议，不绕过 Tool Broker / Policy / Approval。 | Spec §7.2 |
| Secrets | Local 使用 OS secure storage；Cloud 使用 cloud secret manager；长期 Refresh / Device Secret 不得明文存储；日志只记录 `secret_ref`。 | ADR-014 / 018、Spec §19 |
| Skill | V0.1 仅 ShuLiu Official / Company Internal；独立进程隔离；不得直接访问 Secret、OS、任意网络或 Foundation DB；副作用经 Tool Broker → Policy → Approval → Executor；无第三方 Skill Store。 | ADR-025 |
| Windows packaging | PyInstaller + NSIS；Windows build、installer smoke、安装 / 升级 / 回滚只能由 Windows runner / 实机证明。 | ADR-017 / 027 |
| Release gate | unit、contract、integration、migration、security、IPC handshake、Windows build、installer smoke、runtime health 全部通过。 | ADR-027 |

## Implementation Plan 中供审批的工程工具选择

下列项目用于把逐文件任务和命令写到可执行程度；它们是 Implementation Plan 的具体工具选择，不改变 Architecture Spec 的模块边界。用户审批 Implementation Plan 时也一并审阅这些选择：

- TypeScript workspace pin：建议使用与 Node 22.12 最低版本兼容的 pnpm `11.28.6`，并在 `package.json` 精确固定版本。
- Python dependency lock：建议使用 uv `0.12.20`；`pyproject.toml` 与 `uv.lock` 已由 ADR-017 固定。
- Cloud API：建议使用 FastAPI；数据库迁移建议 SQLAlchemy + Alembic。Provider / Identity Provider 仍通过接口替换，不在 Skill 中写入。
- 跨语言契约：建议采用 JSON Schema Draft 2020-12，TypeScript 与 Python 各自使用 validator 做契约测试。
- Integration test PostgreSQL：建议使用带 pgvector 的隔离 CI 服务容器；具体 PostgreSQL 主版本在第一项数据库任务实施前锁定并通过迁移测试。

## 非阻塞配置与验证项

| 项目 | 约束 | 负责人 / 门槛 |
|---|---|---|
| 生产 Identity Provider 与 Cloud 部署目标 | 必须经可替换 Provider 接口；不改变 V0.1 单公司边界。 | Cloud / Security Owner；生产部署前 |
| Worker 凭据 TTL、轮换、设备密钥后端和 Lease 数值 | Session 短期；长期 Secret 安全存储；失效 token 拒绝提交。 | Worker / Security Owner；Windows 试点前 |
| Scheduler timezone / DST | Schedule 显式配置 IANA timezone，不依赖机器默认时区。 | Scheduler Owner；集成验收前 |
| Knowledge parser / Embedding profile / cache invalidation | 遵守 ACL-before-search、撤权和删除失效。 | Knowledge Owner；真实知识源接入前 |
| Model Provider、能力映射和预算值 | 只能配置在 Gateway / Organization policy，fallback 重新检查 Policy。 | Model Gateway Owner；连接真实 Provider 前 |
| OS Skill sandbox backend / signature trust root | 无法满足隔离不变量时，对应 OS 禁止激活 Skill。 | Skill Security Owner；逐 OS 激活前 |
| Evidence retention / export / deletion | 由策略配置，不作为协议固定值；Audit 清理必须可审计。 | Security / Operations Owner；Release 前 |
| Compatibility Window / rollout / health thresholds | 作为发布配置，所有 ADR-027 Gate 通过后才可发布。 | Release Owner；Stable Release 前 |

## 权威约束

若本文件与 Architecture Spec 有冲突，以 Architecture Spec 为准；若某工具选择仍未获 Plan Approval，不得在产品实现中悄悄当成已批准事实。
