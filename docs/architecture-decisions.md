# ShuLiu Agent 架构决策台账

## 状态口径

本文件只把 Architecture Spec 明确标为已确认的内容记录为确认决策。`🟢 已确认` 指架构结论已写入 Spec，不代表功能已实现或运行测试已通过。待决项保持 Pending。Implementation Plan 与 Spec 不一致时，按 `AGENTS.md` 以 Spec 为架构事实源，并记录冲突待协调。

## 已确认决策

| ADR | 决策主题 | 已确认结论 | 来源 / 限定 |
|---|---|---|---|
| ADR-001 | Foundation 产品边界 | Foundation 提供通用执行底座；岗位 Agent、业务规则和业务数据模型不写入 Foundation。 | Architecture Spec §§2–3；讨论已确认 |
| ADR-002 | Desktop Runtime | Electron Desktop 监督 Python Runtime；Renderer 不持有模型密钥或任意系统权限，Main 负责子进程监督与窄 IPC。 | §§5–6；方向已确认，具体实现细节待决 |
| ADR-003 | 本地与共享存储 | 本地运行使用 SQLite，Cloud 共享数据使用 PostgreSQL；Schema 独立版本化并记录 Migration ID 与 checksum。 | §§6、8；逻辑基线已确认，数据权威边界与同步仍待决 |
| ADR-004 | 七大协议 | 分别定义 Agent、Skill、Tool、Workflow、Event、Worker、Model 七类协议；产品版本与协议版本独立演进。 | §7；讨论已确认 |
| ADR-005 | Policy / Approval | Tool 执行前评估 Policy；决策为 `ALLOW`、`DENY` 或 `REQUIRE_APPROVAL`；拒绝时不执行受保护动作，批准绑定动作上下文。 | §9；基本机制已确认，风险矩阵与具体映射待决 |
| ADR-006 | 运行版本快照 | AgentRun 固定启动时解析的 Agent、Skill、Workflow 等版本；更新只影响新 Run。 | §§3、8.2；讨论已确认 |
| ADR-007 | Worker 租约 | Worker 使用 heartbeat、Lease 与递增 fencing token；Cloud 校验租约后接受结果，失联时经过 grace period 再决定恢复或重新派发。 | §§3、12；机制已确认，认证细节和时间参数待决 |
| ADR-008 | Observability / Audit | Event、Trace/Span、Log、Metric、Evidence、Audit 分别记录状态变化、执行路径、组件日志、指标、核验证据和管理动作；敏感内容需脱敏。 | §10；讨论已确认，保留策略待决 |
| ADR-009 | Skill 生命周期 | 安装时校验 Manifest、Publisher、签名、Checksum、兼容性、依赖和权限；Staging 与 Contract Test 通过后再激活；权限增加需重新审批。 | §15；讨论已确认，沙箱强度和扫描细节待决 |
| ADR-010 | 版本与发布 | Foundation 与 Skill 使用 SemVer；协议独立版本化并保留兼容窗口；Release 需签名、Compatibility Manifest、分阶段发布和健康门。 | §17；方向已确认，窗口、批次和阈值待决 |
| ADR-011 | 数据库升级 | 数据库 Schema 独立于应用版本；采用 Expand → Migrate → Contract，迁移失败进入 Safe Mode。 | §17.5；讨论已确认 |
| ADR-012 | 故障恢复 | 统一错误分类；副作用重试使用幂等键并核验外部状态；使用 Checkpoint、Safe Mode 与 Dead Letter，不能直接重放最后一个 ToolCall。 | §§3、18；讨论已确认 |

## Pending 决策

| ADR | 待决主题 | Spec 中的状态与待决范围 |
|---|---|---|
| ADR-013 | Knowledge 向量存储 | Pending：pgvector 或其他实现；本地索引、解析器和 Embedding Provider 也未定。 |
| ADR-014 | IPC / Worker 身份认证 | Pending：IPC 传输与身份校验、Worker 凭据、加密认证及密钥轮换。 |
| ADR-015 | 本地 / Cloud 数据同步 | Pending：数据权威、离线写入、队列及冲突解决。 |
| ADR-016 | Scheduler 语义 | Pending：引擎、时区、错过触发、分布式去重、Lease、heartbeat 与 grace period 参数。 |

## 与当前 Implementation Plan 的冲突提示

当前 Implementation Plan 对身份、Cloud PostgreSQL Source of Truth、SQLite 本地数据范围和 Electron↔Python / Cloud↔Worker 传输列有“已确认”表述；Architecture Spec §8.1、§12、§16、§23 仍将其中部分权威边界、传输认证和实现参数列为待决。依据 `AGENTS.md`，本台账暂不把计划中的表述升级为 Spec 已确认的架构事实。应由后续正式架构修订或 ADR 明确协调后再更新状态。

本文件不新增长期架构决策；所有结论均可追溯到 Architecture Spec §21 的 ADR 目录及对应章节。
