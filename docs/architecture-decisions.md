# ShuLiu Agent 架构决策台账

## 状态口径

本文件只把 Architecture Spec 明确标为已确认的内容记录为确认决策。`🟢 已确认` 指架构结论已写入 Spec，不代表功能已实现或运行测试已通过。待决项保持 Pending。Implementation Plan 与 Spec 不一致时，按 `AGENTS.md` 以 Spec 为架构事实源，并记录冲突待协调。

## 已确认决策

| ADR | 决策主题 | 已确认结论 | 来源 / 限定 |
|---|---|---|---|
| ADR-001 | Foundation 产品边界 | Foundation 提供通用执行底座；岗位 Agent、业务规则和业务数据模型不写入 Foundation。 | Architecture Spec §§2–3；讨论已确认 |
| ADR-002 | Desktop Runtime | Electron Desktop 监督 Python Runtime；Renderer 不持有模型密钥或任意系统权限，Main 负责子进程监督与窄 IPC。 | §§5–6；方向已确认，具体实现细节待决 |
| ADR-003 | 本地与共享存储 | Cloud PostgreSQL 为业务 Source of Truth；SQLite 限于 cache、outbox、inbox、checkpoint 和 local task state；不做通用双向同步。 | §§6、8；同步细节由 ADR-015 进一步明确 |
| ADR-004 | 七大协议 | 分别定义 Agent、Skill、Tool、Workflow、Event、Worker、Model 七类协议；产品版本与协议版本独立演进。 | §7；讨论已确认 |
| ADR-005 | Policy / Approval | Tool 执行前评估 Policy；决策为 `ALLOW`、`DENY` 或 `REQUIRE_APPROVAL`；拒绝时不执行受保护动作，批准绑定动作上下文。 | §9；基本机制已确认，风险矩阵与具体映射待决 |
| ADR-006 | 运行版本快照 | AgentRun 固定启动时解析的 Agent、Skill、Workflow 等版本；更新只影响新 Run。 | §§3、8.2；讨论已确认 |
| ADR-007 | Worker 租约 | Worker 使用 heartbeat、Lease 与递增 fencing token；Cloud 校验租约后接受结果，失联时经过 grace period 再决定恢复或重新派发。 | §§3、12；机制已确认，认证细节和时间参数待决 |
| ADR-008 | Observability / Audit | Event、Trace/Span、Log、Metric、Evidence、Audit 分别记录状态变化、执行路径、组件日志、指标、核验证据和管理动作；敏感内容需脱敏。 | §10；讨论已确认，保留策略待决 |
| ADR-009 | Skill 生命周期 | 安装时校验 Manifest、Publisher、签名、Checksum、兼容性、依赖和权限；Staging 与 Contract Test 通过后再激活；权限增加需重新审批。 | §15；讨论已确认，沙箱强度和扫描细节待决 |
| ADR-010 | 版本与发布 | Foundation 与 Skill 使用 SemVer；协议独立版本化并保留兼容窗口；Release 需签名、Compatibility Manifest、分阶段发布和健康门。 | §17；方向已确认，窗口、批次和阈值待决 |
| ADR-011 | 数据库升级 | 数据库 Schema 独立于应用版本；采用 Expand → Migrate → Contract，迁移失败进入 Safe Mode。 | §17.5；讨论已确认 |
| ADR-012 | 故障恢复 | 统一错误分类；副作用重试使用幂等键并核验外部状态；使用 Checkpoint、Safe Mode 与 Dead Letter，不能直接重放最后一个 ToolCall。 | §§3、18；讨论已确认 |
| ADR-013 | Knowledge 向量存储 | V0.1 使用 Cloud PostgreSQL + pgvector，不引入第二套独立向量数据库；Knowledge 属于 Foundation server-side infrastructure。查询必须先按 tenant / organization、scope、classification 与 Permission 形成授权候选集，再执行 vector search。 | §§8.1、13；Accepted，2026-10-17 |
| ADR-014 | IPC / Worker 身份认证 | Electron Main 通过 stdio + NDJSON 启动 Python Sidecar；stdout 仅协议、stderr 仅日志。Cloud ↔ Worker 使用 WSS；一次性 Registration Token 绑定 Worker 本地生成的设备密钥，Cloud 分配 `worker_id`；运行时使用短期会话凭据；长期 Secret 不明文存储；V0.1 不强制 mTLS。 | §§12、16；Accepted，2026-10-17；TTL、密钥保护细节与轮换策略仍待定 |
| ADR-015 | 本地 / 云端数据同步 | Cloud PostgreSQL 是业务 Source of Truth。SQLite 仅存 cache、outbox、inbox、checkpoint 和 local task state。Outbox event 使用 `event_id` / `idempotency_key` 去重；server ACK 后标记已同步；离线时保留未 ACK 事件并续传；不做通用 SQLite ↔ PostgreSQL 双向同步。 | §§8.1、21–22；Accepted，2026-10-17 |
| ADR-016 | Scheduler 语义 | 使用 APScheduler 3.11.3 与 PostgreSQL persistent datastore；Trigger 与 Workflow Execution 分离。Schedule 使用稳定唯一 ID，启动冲突默认 replace；错过的周期任务默认 `latest`，`all` 仅显式设置；同一 Schedule 不并发；`misfire_grace_time` 可配置；副作用流程必须幂等。 | §11；Accepted，2026-10-17；timezone / DST 与部署级参数仍待定 |

## Pending 决策

以下是已接受 ADR 之外的后续实现参数或未解决议题；列在此处不表示 ADR-013 至 ADR-016 被重新标记为 Pending。

| 待决主题 | Spec 中的待决范围 | 关联 ADR |
|---|---|---|
| Knowledge 实施细节 | Pending：文档解析器、Embedding Provider / profile、索引迁移、ACL 缓存失效与删除传播。存储和 ACL 先过滤再向量检索已由 ADR-013 锁定。 | ADR-013 |
| Worker 认证实现参数 | Pending：设备私钥具体安全存储、短期会话凭据 TTL / 轮换；mTLS 不属于 V0.1 强制项。 | ADR-014 |
| Cloud 用户身份与租户授权 | Pending：用户身份来源、角色授权来源和完整租户模型。Implementation Plan 中的单公司 `User + Role + Permission` 方案尚待 Architecture Spec 协调。 | ADR-018（Implementation Plan） |
| 调度部署参数 | Pending：时区 / DST 细节、部署级 `misfire_grace_time` 默认值及未来多实例高可用方案。 | ADR-016 |

## 与当前 Implementation Plan 的冲突提示

ADR-013 至 ADR-016 已写入 Architecture Spec，并与 Implementation Plan 对应依赖同步。Implementation Plan 的单公司身份与角色模型仍未在 Architecture Spec 中关闭；完整 Cloud 用户身份 / 租户模型继续 Pending，不能因这些 ADR 被视为已解决。

本文件不新增长期架构决策；所有结论均可追溯到 Architecture Spec §21 的 ADR 目录及对应章节。
