# ShuLiu Agent 架构决策台账

## 状态口径

本台账只汇总 Architecture Spec 已确认的决策。`已确认` 表示架构结论已记录，不代表功能已经实现或测试通过。Implementation Plan 不得覆盖 Architecture Spec；不改变安全不变量的部署参数列入文末非阻塞项，并须在对应门槛前验证。

## 已确认决策

| ADR | 决策主题 | 已确认结论 | 来源 / 限定 |
|---|---|---|---|
| ADR-001 | Foundation 边界 | Foundation 提供通用执行底座；岗位 Agent、业务规则和业务数据模型不写入 Foundation。 | Spec §§2–3 |
| ADR-002 | Desktop Runtime | Electron Desktop 监督 Python Runtime；Renderer 走窄 IPC，不持有模型密钥或任意系统权限。 | Spec §§5–6 |
| ADR-003 | 存储边界 | Cloud PostgreSQL 是业务 Source of Truth；SQLite 仅存 cache、outbox、inbox、checkpoint、local task state；不做通用双向同步。 | Spec §8；ADR-015 |
| ADR-004 | 七大协议 | Agent、Skill、Tool、Workflow、Event、Worker、Model 七类协议分别定义，独立版本化。 | Spec §7 |
| ADR-005 | Policy / Approval | 运行 Tool 前评估 Policy；结果为 `ALLOW`、`DENY` 或 `REQUIRE_APPROVAL`；DENY 优先、默认 Fail Closed；审批绑定动作上下文。 | Spec §9；ADR-022 |
| ADR-006 | 运行版本快照 | AgentRun 固定启动时解析的 Agent、Skill、Workflow 等版本；更新只影响新 Run。 | Spec §§3、8.2 |
| ADR-007 | Worker 租约 | Worker 使用 heartbeat、Lease 与递增 fencing token；Cloud 校验租约后接受结果。 | Spec §§3、12 |
| ADR-008 | Observability / Audit | Event、Trace/Span、Log、Metric、Evidence、Audit 职责分离；敏感内容脱敏，Evidence retention 为可配置策略。 | Spec §10；ADR-026 |
| ADR-009 | Skill 生命周期与安全 | 校验 Manifest、Publisher、签名、Checksum、兼容性、依赖与权限；Staging / Contract Test 通过后激活；V0.1 Publisher 与沙箱边界见 ADR-025。 | Spec §15；ADR-025 |
| ADR-010 | 版本与发布 | Foundation / Skill 使用 SemVer，协议独立版本化；Release 使用签名、Compatibility Manifest、分阶段更新与健康门。 | Spec §17；ADR-027 |
| ADR-011 | 数据库升级 | Schema 独立版本化，采用 Expand → Migrate → Contract；迁移失败进入 Safe Mode。 | Spec §17.5 |
| ADR-012 | 故障恢复 | 统一错误分类；副作用恢复使用幂等键并核验外部状态；使用 Checkpoint、Safe Mode、Dead Letter，不直接重放最后一个 ToolCall。 | Spec §§3、18 |
| ADR-013 | Knowledge 向量存储 | V0.1 使用 Cloud PostgreSQL + pgvector，不引入独立向量库；先按 tenant / organization、scope、classification、Permission 得到授权候选集，再执行向量检索。 | Spec §§8.1、13；`docs/adr/ADR-013-knowledge-vector-storage.md` |
| ADR-014 | IPC / Worker 身份认证 | Electron Main ↔ Python Sidecar 使用 stdio + NDJSON，stdout 仅协议、stderr 仅日志；Cloud ↔ Worker 使用 WSS；一次性 Registration Token 绑定设备密钥并分配 `worker_id`；运行时短期会话凭据；长期 Secret 安全存储；V0.1 不强制 mTLS。 | Spec §§12、16；`docs/adr/ADR-014-ipc-worker-identity.md` |
| ADR-015 | 本地 / 云端同步 | Cloud PostgreSQL 为 Source of Truth；本地 outbox 事件以 `event_id` / `idempotency_key` 去重；server ACK 后标记同步；掉线续传；不做通用双向同步。 | Spec §8.1；`docs/adr/ADR-015-data-sync-boundary.md` |
| ADR-016 | Scheduler 语义 | APScheduler 3.11.3 持久化 Trigger，Workflow Engine 执行；稳定 Schedule ID、启动 replace、默认 latest、显式 all、同 Schedule 不并发、`misfire_grace_time` 可配置、副作用幂等。 | Spec §11；`docs/adr/ADR-016-scheduler-semantics.md` |
| ADR-017 | Repository / Toolchain / CI | pnpm workspaces；Node 22 LTS >=22.12；Python 3.11；Vitest / pytest；GitHub Actions；uv + `uv.lock`；通用 Linux / macOS 测试与 Windows 专属门禁分开；固定初始目录；实现使用 `feat/foundation-v0.1` 独立 worktree，不在 main 编写产品实现。 | `docs/adr/ADR-017-repository-toolchain-ci.md`；Accepted，2026-10-17 |
| ADR-018 | Identity | 单公司内部 V0.1，保留 `organization_id`，不实现完整多租户 SaaS；模型为 `User + Organization Membership + Role + Permission`；短期 Session / Access，长期 Refresh / Device Secret 安全存储；Identity Provider 经可替换抽象接口接入。 | `docs/adr/ADR-018-identity-model.md`；Accepted，2026-10-17 |
| ADR-022 | Policy 风险矩阵 | LOW 读 / 搜索 / 分析；MEDIUM 非破坏写入 / 普通表单；HIGH 对外发送 / 发布 / 正式记录创建；CRITICAL 付款 / 批量删除 / 权限或安全配置；HIGH 默认 Approval，CRITICAL 强制 Approval；DENY 优先，默认 Fail Closed。 | `docs/adr/ADR-022-policy-risk-matrix.md`；Accepted，2026-10-17 |
| ADR-023 | Knowledge 实施边界 | 存储与 ACL 顺序由 ADR-013 固定；parser、Embedding profile、索引迁移和缓存失效是集成配置，但不得改变 ACL-before-search 与撤权 / 删除失效约束。 | `docs/adr/ADR-023-knowledge-implementation-boundary.md`；架构边界已确认 |
| ADR-024 | Model Gateway Policy | ShuLiu Policy / capability 层通过 LiteLLM 接入模型；Skill 仅声明 capability；Gateway 按 capability、privacy、budget、provider health、fallback policy 路由；Provider 优先顺序配置化；`LOCAL_ONLY` 不得 fallback 到远端。 | `docs/adr/ADR-024-model-gateway-policy.md`；Accepted，2026-10-17 |
| ADR-025 | Skill Sandbox | V0.1 仅 ShuLiu Official / Company Internal Skill；可执行 Skill 独立进程隔离；不得直接获取 Secret、操作 OS、任意访问网络或直写 Foundation DB；副作用经 Tool Broker → Policy → Approval → Executor；不含第三方 Skill Store。 | `docs/adr/ADR-025-skill-sandbox.md`；Accepted，2026-10-17 |
| ADR-026 | Evidence Retention | Retention 是可配置策略而非协议固定天数；Evidence 按风险采集、受控访问、脱敏；Audit 不得被普通清理静默删除。 | `docs/adr/ADR-026-evidence-retention.md`；Accepted，2026-10-17 |
| ADR-027 | Release Gate | unit、contract、integration、migration、security、IPC handshake、Windows build、installer smoke、runtime health 全部通过才可发布；Windows 专属验证须有 Windows runner / 实机证据。 | `docs/adr/ADR-027-release-gates.md`；Accepted，2026-10-17 |

## 非阻塞实现参数

截至本版，Architecture Critical TBD：**无**。下列项目不改变已确认模块边界和安全不变量；必须在对应门槛前明确负责人、配置值和测试证据。

| 参数 | 责任方 | 关闭门槛 |
|---|---|---|
| Desktop Runtime restart / backoff 参数与 Safe Mode 恢复细节 | Desktop / Runtime Owner | Crash-recovery 集成验收前；连续崩溃、迁移失败、健康恢复测试通过 |
| Cloud 部署目标与生产 Identity Provider | Cloud / Security Owner | 生产 Cloud 接入前；Provider contract tests 证明抽象可替换 |
| Worker 设备密钥后端、Session TTL / 轮换、Lease / heartbeat / grace 数值 | Worker / Security Owner | Windows 设备试点前；过期、轮换、离线、fencing 故障注入通过 |
| Schedule IANA timezone、DST 与 grace 配置 | Scheduler Owner | 集成验收前；DST 跳时 / 重复时刻测试通过 |
| Knowledge parser、Embedding profile、索引迁移与缓存失效 | Knowledge Owner | 真实知识源接入前；ACL 先过滤、撤权、删除传播测试通过 |
| Provider / Model 清单、能力映射与预算数值 | Model Gateway Owner | 连接真实 Provider 前；Fake Provider 路由、隐私、预算、fallback 测试通过 |
| Permission 粒度、Tool timeout 与有界重试参数 | Tool / Policy Owner | Tool / Workflow 集成验收前；side effect 风险、timeout、retry 与幂等测试通过 |
| OS Skill sandbox backend 与签名 trust root | Skill Security Owner | 对应 OS 激活可执行 Skill 前；越权测试通过，否则禁止激活 |
| 数据静态加密策略与外部渗透测试范围 | Security Owner | 生产数据写入前；威胁模型审查和约定安全测试通过 |
| Evidence retention 周期、容量、导出与删除值 | Security / Operations Owner | Release 前；策略审查、访问控制、清理审计测试通过 |
| Compatibility Window、灰度比例、健康阈值与性能门槛 | Release Owner | Stable Release 前；配置评审且 ADR-027 全门禁通过 |
| 第三方依赖精确版本与许可证记录 | Repository Owner | 每次 Release 前；Lockfile / 许可证扫描无未批准项 |

## 权威关系

Architecture Spec 是架构事实源，Implementation Plan 是施工事实源。本文件是决策索引，不替代 Spec。ADR-019 至 ADR-021 已由 ADR-014 至 ADR-016 覆盖，不重复维护。
