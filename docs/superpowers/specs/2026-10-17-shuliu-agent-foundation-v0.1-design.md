# ShuLiu Agent Foundation V0.1 Architecture Spec

| 项目 | 内容 |
|---|---|
| 文档状态 | 架构评审稿 |
| 规格版本 | 0.1 |
| 日期 | 2026-10-17 |
| 产品 | ShuLiu Agent Foundation |
| 目标仓库路径 | docs/superpowers/specs/2026-10-17-shuliu-agent-foundation-v0.1-design.md |
| 范围 | 架构规范；不含业务代码实现 |

## 1. 文档目的与决策标记

本文将已经讨论的 ShuLiu Agent Foundation V0.1 架构结论整理为可评审、可追溯的规范，作为之后 Implementation Plan 和工程实现的输入。本文不代表系统已实现、已测试或已发布。

为避免把缺失信息写成既定事实，采用以下标记：

- **已确认**：在架构讨论中明确形成的方向或约束。
- **基线**：为让架构可执行而整理出的最低要求；若改变，应通过 ADR 记录。
- **待决**：现有讨论没有足够信息确定，进入实现计划前需要决策。
- 示例中的版本号、阈值和比例仅作说明，除非另有标记，不是最终生产配置。

## 2. 目标与范围

### 2.1 产品目标

Foundation 是 ShuLiu Agent 系统的公共底座，为多个 Agent、Skill 和岗位自动化提供一致的运行机制。它负责执行边界、协议、状态、权限、恢复和审计；业务 Agent 与业务流程在其上组合，不把业务规则写死在 Foundation 内。

V0.1 的目标是建立可验证的执行闭环：

1. 用户提交任务，系统创建可追踪的 Task 和 AgentRun。
2. Agent 根据固定版本的定义运行，并通过受控的 Tool、Skill、Workflow、Knowledge 和 Model Gateway 完成工作。
3. 有副作用的动作在执行前经过 Policy；要求人工决定时进入 Approval 等待。
4. 执行过程持续产生 Event、Trace、Log、Metric 和必要的 Evidence。
5. 遇到失败或进程中断时，系统能分类、暂停、重试、恢复或明确终止，不盲目重复副作用。
6. Desktop、Python Runtime、Cloud 和 Worker 的升级具有兼容检查、健康门和可控回滚。

### 2.2 V0.1 范围

- Agent Foundation 的运行时与模块边界。
- Agent、Skill、Tool、Workflow、Event、Worker、Model 七类协议。
- Electron Desktop 与 Python Runtime 的进程协作；Cloud 与 Worker 的任务协作。
- 本地 SQLite 与共享服务 PostgreSQL 的逻辑数据模型。
- Policy、Approval、Skill 权限与副作用控制。
- Scheduler、Workflow、Checkpoint、Worker Lease 和恢复机制。
- Knowledge 访问控制与 Model Gateway 的能力抽象。
- Event、Trace、结构化 Log、Metric、Evidence、审计和错误模型。
- Skill 安装、验证、启用、升级、降级、停用和卸载规则。
- 版本、发布、数据库迁移、更新健康检查与回滚约束。
- V0.1 测试矩阵与验收标准。

### 2.3 非目标

- 在 Foundation 中实现某个岗位的业务 Agent、业务规则或业务数据模型。
- 在 V0.1 开放任意第三方 Skill Publisher；当前信任范围限于 ShuLiu 官方和企业内部发布者，其他来源默认阻止。
- 把高风险动作做成无需 Policy、审批或结果验证的自由执行。
- 把某一家模型供应商或具体模型名称写入 Skill 的业务契约。
- 在本规格中选定所有云服务、数据库同步、身份系统、部署区域和第三方依赖的具体版本。
- 以架构文档、演示或 Mock 连接代替真实运行验收。

## 3. 架构原则

1. **Foundation 与业务能力分层**：Foundation 提供通用执行能力，业务 Agent、Skill 和 Workflow 通过协议接入。
2. **契约先于实现**：跨进程、跨服务和可独立升级组件必须有版本化契约。
3. **策略先于副作用**：外部写入、消息发送、发布、桌面控制等动作必须经过 Policy；是否要求人工 Approval 由风险策略决定。
4. **运行绑定不可变版本**：AgentRun 固定本次使用的定义、Skill 和 Workflow 版本。运行中更新只影响新 Run。
5. **所有恢复都检查外部状态**：恢复点不能作为重复执行最后一步的理由；先核对副作用是否已经发生。
6. **副作用重试必须幂等**：通过 idempotency_key 和执行结果核验防止重复提交。
7. **Worker 使用 Lease 与 fencing token**：失去有效 Lease 的 Worker 不得提交关键结果。
8. **权限最小化且显式**：Skill 仅获得声明并获批的权限；权限增加必须重新审批。
9. **健康状态决定是否继续运行**：严重故障进入 Safe Mode，暂停 Agent 执行和高风险 Tool。
10. **依赖和升级可复现**：发布包、运行时、依赖、数据库迁移和协议兼容状态都要记录。

## 4. 系统上下文

### 4.1 外部参与者与系统

| 参与者 / 系统 | 责任 |
|---|---|
| 普通用户 | 创建任务、查看进度、提供必要输入、响应审批 |
| 管理员 | 配置 Policy、审批权限、管理发布渠道、Skill 与 Worker |
| ShuLiu Desktop | 用户入口、本地状态界面、启动和监督 Python Runtime |
| Python Runtime | Foundation 核心执行环境，编排 Agent、Workflow、Tool、Knowledge 与 Model 调用 |
| ShuLiu Cloud | 多设备任务协调、Worker 注册与派发、共享状态和服务端存储 |
| Worker | 在获授权的设备或环境中执行 Cloud 派发的工作 |
| Model Provider | 提供对话、推理、视觉或向量能力 |
| 外部系统 / Tool | 浏览器、桌面、文件、企业 API 等受控能力 |
| Skill Package | 通过版本化契约组合 Tool、知识和 Workflow 能力 |

### 4.2 上下文图

~~~mermaid
flowchart LR
  U[用户 / 管理员] --> D[ShuLiu Desktop]
  D --> R[Python Foundation Runtime]
  R --> L[(本地 SQLite)]
  R <--> C[ShuLiu Cloud]
  C --> P[(PostgreSQL)]
  C <--> W[Worker Runtime]
  R --> A[Agent / Workflow]
  W --> A
  A --> PL[Policy / Approval]
  PL --> T[Tool 与 Skill Runtime]
  A --> MG[Model Gateway]
  MG --> MP[Model Providers]
  A --> K[Knowledge Service]
  T --> X[Browser / Computer / 外部系统]
  R --> O[Event / Trace / Audit / Evidence]
  C --> O
  W --> O
~~~

图中体现逻辑关系，不限定 Cloud 与 Desktop 的部署拓扑，也不表示本地和云端数据已具备自动双向同步。

## 5. 模块边界与依赖方向

| 模块 | 职责 | 不应承担 |
|---|---|---|
| Desktop UI / Renderer | 任务、审批、健康状态和设置界面 | 直接访问数据库、模型或外部系统 |
| Electron Main / Runtime Supervisor | 启停 Python 子进程、IPC、健康检查、崩溃处理与 UI 安全边界 | 实现业务 Agent 决策 |
| Python Runtime Bootstrap | 初始化配置、数据库、协议、模块健康状态 | 绕过应用服务直接执行任意 Skill |
| Agent Runtime | 管理 AgentRun 状态、上下文和模型决策循环 | 自行授予权限或绕过 Policy |
| Policy / Approval | 判定动作是否允许、需要审批或拒绝；记录决策依据 | 执行 Tool 副作用 |
| Skill Registry / Host | 校验 Manifest、版本、依赖、权限和生命周期；按固定版本加载 Skill | 把 Skill 代码默认为可信 |
| Tool Registry / Adapters | 发布受控 Tool 契约并适配 Browser、Computer、API 等运行时 | 自行决定业务授权 |
| Workflow Engine | 执行不可变版本的步骤、分支、等待与恢复 | 在运行中热替换 Workflow 定义 |
| Scheduler | 持久化触发规则、创建任务并交给 Workflow / Agent Runtime | 直接执行外部副作用 |
| Model Gateway | 按能力、Policy 和隐私级别路由模型调用，记录真实提供方与模型 | 将供应商选择写入 Skill 的业务逻辑 |
| Knowledge Service | 文档、分块、索引、检索和 ACL 检查 | 返回未经授权的片段 |
| Worker Coordinator | 注册 Worker、派发任务、续租、撤销和对账 | 只凭失联就认定任务已经停止 |
| Persistence | 数据访问、事务、迁移和存储适配 | 跨越模块边界执行领域决策 |
| Event / Observability | 统一事件、Trace、Log、Metric、Evidence 和 Audit | 记录明文 Secret 或不必要的敏感内容 |
| Update / Release | 签名包校验、兼容性检查、分阶段更新、健康门与回滚 | 在健康门未通过时启用新版本 |

### 5.1 依赖规则

- UI 通过 IPC 调用应用服务，不直接调用数据库或执行 Tool。
- Agent、Workflow 和 Scheduler 通过稳定接口调用 Policy、Tool、Knowledge、Model Gateway 与 Persistence。
- 有副作用的 Tool 必须由受控执行层执行，并返回执行状态和验证状态。
- 模块之间通过契约和事件传递结果；禁止形成循环依赖。
- Cloud 与 Worker 只通过 Worker Protocol 通信；不得依赖远程桌面式的隐式共享进程状态。

## 6. 进程架构

### 6.1 Desktop

- **Electron Renderer**：呈现用户界面；不持有模型密钥，不直接访问本机任意文件或外部网络。
- **Electron Main**：拥有应用级系统权限，负责创建受限 IPC、启动 Python Runtime、检测其退出和执行健康检查。
- **Python Runtime**：承载 Foundation 的主要编排、协议适配、Policy、Skill、Workflow、Knowledge、Model Gateway 和本地持久化逻辑。
- **SQLite**：保存本机运行所需的本地数据、状态和缓存。哪些数据属于本地唯一数据、哪些需要同步尚待确定。
- **Browser / Computer Runtime**：作为受控 Tool Adapter 运行，可位于 Desktop 或 Worker 环境；每次动作都要支持观察与结果验证。

Electron 与 Python Runtime 在发布时视为一个 Release Unit。Python 子进程崩溃时，Electron Main 应标记 Runtime 不健康、暂停新任务、写入 Crash Event、尝试有限恢复并执行 Health Check；连续恢复失败时进入 Safe Mode，不能无限重启。

### 6.2 Cloud 与 Worker

- **Cloud Control Plane**：接受任务、协调共享执行、维护 Worker 在线状态与 Lease，并保存共享数据。
- **Worker Runtime**：以注册身份连接 Cloud，领取被派发的任务，发送心跳、执行状态、Event 和结果。
- Worker 失联后先进入不可达等待窗口，等待恢复并检查 Lease；Lease 到期后由 Cloud 判断能否重新派发。
- 所有关键提交都检查 Lease 和 fencing token，避免旧 Worker 作为 Zombie Task 提交过期结果。
- Worker 的操作范围由其能力声明、Policy、任务授权和本机配置共同限制。

### 6.3 进程健康状态

关键组件统一报告 HEALTHY、DEGRADED 或 UNHEALTHY。至少覆盖 Desktop、Python Runtime、SQLite、Cloud 连接、PostgreSQL、Model Gateway、Knowledge、Browser / Computer Runtime、Worker Connection 和 Skill Registry。组件健康状态不等于某个业务任务已成功。

## 7. 七大协议

Foundation Version 与协议版本独立演进。协议版本不得简单绑定到产品版本；新增不兼容协议时保留旧版 Adapter 和明确的兼容窗口，完成废弃期后才能删除。Runtime IPC 也单独版本化，它是进程传输契约，不替代以下七类领域协议。

所有协议的实际序列化格式、必填字段和错误响应需维护机器可校验 Schema；初始兼容性基线为 protocol_version 1。

| 协议 | 定义内容 | 核心约束 |
|---|---|---|
| Agent Protocol | Agent 身份、版本、输入输出、能力需求和执行配置 | AgentRun 固定 Agent 版本；权限来自 Policy，而非 Agent 自声明 |
| Skill Protocol | Skill Manifest、版本、依赖、能力、权限、入口和兼容范围 | 安装前完整验证；运行绑定 Skill 版本快照 |
| Tool Protocol | Tool 名称、版本、输入输出 Schema、权限、风险、超时、幂等要求和验证状态 | 执行结果区分调用成功与业务结果已验证 |
| Workflow Protocol | Workflow 定义、步骤、条件、重试 / 等待策略、版本和输入输出 | WorkflowRun 固定定义版本；步骤状态可 checkpoint 和恢复 |
| Event Protocol | Event 名称、版本、主体、时间、关联对象、Trace、载荷和敏感级别 | Event 可序列化、可审计、可演进；消费方能识别未知版本 |
| Worker Protocol | Worker 注册、能力、任务租约、心跳、进度、取消、结果和错误 | Lease、租约过期、fencing token 和幂等键必须可核验 |
| Model Protocol | 能力请求、消息 / 内容输入、结构化输出、用量、延迟和错误 | Skill 请求能力而非固定供应商；Gateway 记录实际路由结果 |

### 7.1 协议兼容策略

- 每个消息包含可识别的协议名和协议版本；跨进程或跨服务消息还包含请求关联 ID 和 Trace ID。
- Schema 只允许向后兼容的字段增补进入小版本；字段删除、语义改变或必填变化需要新主版本或明确 Adapter。
- 消费方拒绝无法安全解释的主版本，并写入可读的兼容错误。
- Foundation Release 生成 Compatibility Manifest，声明支持的 Runtime、Worker、Skill、Event、协议和数据库 Schema 版本。

## 8. 数据模型与持久化

本节描述逻辑实体，不预先规定全部物理表名、索引和分片方式。

| 逻辑实体 | 用途与关系 |
|---|---|
| AgentDefinition / AgentVersion | Agent 的不可变版本定义 |
| Task | 用户或 Scheduler 发起的工作请求 |
| AgentRun | 一次 Agent 执行；关联固定的 Agent、Skill、Workflow 版本和 Trace |
| SkillPackage / SkillVersion | Skill 发布包、Manifest、签名、权限、依赖和版本 |
| ToolDefinition / ToolCall | Tool 契约与某次执行请求、结果、验证状态 |
| WorkflowDefinition / WorkflowVersion | 不可变的流程版本 |
| WorkflowRun / StepRun | 流程执行及每一步的状态、输入输出和错误 |
| Policy / PolicyDecision | 策略规则、上下文、判定结果与理由 |
| Approval | 待办审批、审批人、决定、到期时间和绑定的动作上下文 |
| Event / Trace / Span | 事实事件和跨模块执行路径 |
| Checkpoint | 可恢复运行状态、已完成步骤和未决动作 |
| Worker / Lease | Worker 注册能力、在线状态、租约和 fencing token |
| KnowledgeDocument / Chunk / ACL | 知识来源、切块、索引和访问控制 |
| ModelCall | 请求能力、最终提供方、模型版本、延迟、用量和费用 |
| Evidence | Tool 或任务的截图、DOM 摘要、页面 URL 和其他核验材料 |
| SchemaMigration | 数据库 Schema 版本、Migration ID、时间和校验和 |

### 8.1 存储基线

- Desktop 本地运行使用 SQLite。
- Cloud 共享数据使用 PostgreSQL。
- pgvector 是讨论中出现的候选依赖；是否作为 V0.1 的正式向量存储需要 ADR 确认。
- 数据库 Schema 有独立版本、Migration ID、应用时间和 checksum；不能只依赖 App Version 推断数据库状态。
- 本地与云端的权威数据边界、同步队列、冲突解决和离线写入策略仍是待决项。
- Skill 代码包与业务数据分离。卸载 Skill 不级联删除该 Skill 生成的业务数据；数据删除是独立的高风险管理操作。

### 8.2 运行快照

AgentRun 至少记录启动时解析出的 AgentVersion、SkillVersion、WorkflowVersion、Policy 版本或决策快照、Runtime / 协议版本和 ModelCall 实际路由信息。更新定义只作用于新 Run，旧 Run 按原版本继续或按明确的兼容失败策略暂停。

## 9. Policy 与 Approval

### 9.1 Policy 决策

Policy 在 Tool 执行前评估主体、请求动作、资源、权限、风险、数据敏感度、运行环境和已有授权，并返回 ALLOW、DENY 或 REQUIRE_APPROVAL。每次判定保存决策标识、规则版本、理由、关联 Trace 和执行对象。

- 参数不合法、明确拒绝和权限不足不能靠重试绕过。
- Model fallback 也必须经过 Policy；LOCAL_ONLY 数据不能在本地模型失败后自动发往远端。
- Browser / Computer 的动作执行后必须验证实际页面或桌面状态；点击成功不等于业务动作成功。
- Tool 风险分级和具体动作映射需要维护可审计的策略表。讨论中使用 LOW、HIGH、CRITICAL 示例；V0.1 完整分级表仍待确认。

### 9.2 Approval

- 当 Policy 返回 REQUIRE_APPROVAL，运行进入等待状态，不执行被保护动作。
- Approval 记录申请人 / 目标审批人、动作摘要、资源、风险、过期时间、关联 PolicyDecision 和最终决定。
- Approval 应绑定动作参数摘要或上下文版本；审批后动作内容变化必须重新判定，不能沿用旧批准。
- 用户拒绝审批时不重试原动作；Workflow 可以选择定义好的替代分支。
- Approval 过期作为明确的 APPROVAL_EXPIRED 错误处理。
- Skill 新版本新增权限必须重新授权；权限减少可以自动沿用升级流程。

### 9.3 高风险动作

是否要求人工批准由 Policy 决定。需要 Approval 的动作必须在获批后执行，并保留执行前后 Evidence 和最终验证结果。未经验证的副作用不得让 Workflow 静默继续。

## 10. Event、Observability 与 Audit

Event、Trace、Log、Metric、Evidence 和 Audit 是不同类型的记录：

| 数据 | 回答的问题 |
|---|---|
| Event | 系统发生了什么状态变化 |
| Trace / Span | 一次执行经过了哪些组件、耗时多久 |
| Log | 某个组件内部发生了什么 |
| Metric | 成功率、错误率、延迟、用量和成本如何变化 |
| Evidence | 某一步实际执行结果的可核验材料是什么 |
| Audit | 谁在何时批准、拒绝、配置或触发了什么操作 |

### 10.1 Trace 与结构化记录

- 从用户动作或定时任务开始创建 Trace ID，并贯穿 Task、AgentRun、WorkflowRun、ToolCall、Worker 和 ModelCall。
- Span 使用 span_id 和 parent_span_id 表示调用关系，结构兼容 OpenTelemetry 思路；V0.1 不要求上线完整遥测平台。
- 核心 Log 使用结构化字段，至少包括时间、级别、组件、Trace ID、关联运行 ID、Error Code 和 retryable 状态。
- Event 带 Schema 版本；Audit 不得被普通业务清理任务静默删除。
- Evidence 按风险保留：低风险少量采集，高风险保留关键证据，关键动作保留完整证据链。具体保存周期和容量上限待决。

### 10.2 Metrics

至少提供 Task / Agent 成功率、Tool 错误率、平均耗时、Model 延迟与 Token / 成本、Retry 次数、Approval 等待时间、Worker 在线率和更新回滚率。指标不得泄露用户内容或 Secret。

### 10.3 隐私

日志和事件不得写入 API Key、密码、Cookie、Authorization Header、完整身份证信息或支付信息。只记录 secret_ref 和经过脱敏的错误上下文。事件载荷和 Evidence 的访问权限、加密和保留周期需要按数据敏感度设置。

## 11. Scheduler、Workflow 与 Checkpoint

### 11.1 Scheduler

Scheduler 持久化计划定义和下次触发状态；触发时创建 Task / WorkflowRun 并使用稳定的 idempotency_key，避免重启或重复触发造成重复任务。调度引擎、时区、错过触发后的补偿规则和分布式调度一致性待 ADR 确定。

### 11.2 Workflow

- Workflow 定义有独立版本；每个 WorkflowRun 在启动时固定 workflow_version_id。
- 管理员发布新定义不会改变正在运行的 Run。
- Workflow 步骤记录输入、输出引用、开始 / 结束时间、错误、验证结果、重试次数和 Approval 状态。
- Step 失败后的 retry、fallback、pause、fail 由 Workflow 策略显式决定，不允许通用无限重试。
- 用户审批、外部依赖和 Worker 恢复可让 Workflow 进入等待；等待状态必须持久化。

### 11.3 Checkpoint 与恢复

Checkpoint 至少记录 run_id、sequence、可序列化状态、last_completed_step、pending_action、pending_approval、context_version 和 created_at。

恢复流程：

1. 读取最新有效 Checkpoint 并校验版本。
2. 校验 Workflow / Skill 版本仍可用，恢复所需上下文。
3. 检查最后一个外部副作用是否已经发生，并核对 idempotency_key。
4. 若存在未完成动作，按动作语义执行验证、补偿、等待或人工处理。
5. 只有确认可安全继续时才恢复后续步骤；否则暂停并给出明确错误。

不能在重启后直接重放 Checkpoint 中的最后一个 ToolCall。

## 12. Worker 架构与租约

- Worker 注册自身版本、协议版本和能力；Cloud 只派发满足约束的任务。
- Worker 按期发送 heartbeat，领取任务时获得 lease_id、到期时间和递增 fencing token。
- Worker 续租失败或拿到过期 token 时，必须停止尚未提交的关键动作。
- Cloud 对失联 Worker 设置 grace period；恢复后先校验 Lease，再决定继续；过期后才考虑重新派发。
- 任务派发按至少一次交付语义设计；防重复依赖 idempotency_key、Lease 和结果对账。对外部副作用无法证明恰好一次时，必须通过核验或人工确认处理。
- 多次重试仍失败的任务可进入 DEAD_LETTER，等待人工查看、修复、重新执行或标记忽略。
- Worker 的认证方式、传输协议、任务队列、Lease 时长、心跳间隔和 grace period 是待决配置。

## 13. Knowledge

- Knowledge Service 管理知识来源、Document、Chunk、索引、Embedding 引用和 ACL。
- ACL 检查必须在检索结果返回给 Agent 之前完成；不能依靠模型提示词来实现访问控制。
- 知识访问和来源引用进入 Trace / Audit，便于解释回答使用了哪些资料。
- Embedding 请求走 Model Gateway，并遵守数据敏感度和 LOCAL_ONLY 限制。
- V0.1 是否使用 PostgreSQL / pgvector、如何做本地知识索引、文件解析器与同步策略仍待 ADR 确定。
- 删除或权限变更必须能使索引和缓存中的旧内容失效。

## 14. Model Gateway

- Skill 和 Agent 请求能力，例如 fast_chat、reasoning_high、vision、embedding；不把 GPT、Claude、Qwen 等供应商名称作为 Skill 契约。
- Gateway 根据能力、Policy、隐私级别、可用性、成本和组织配置解析 Provider / Model。
- 每次调用记录 requested_capability、resolved_provider、resolved_model、model_version、延迟、Token、成本和错误码。
- Provider fallback 受 Policy 约束；对 LOCAL_ONLY 数据禁止自动切换到远端 Provider。
- 需要支持统一超时、限流错误处理、退避策略和 Provider 隔离；具体候选 Provider、密钥存储、预算限制和本地模型范围待决。

## 15. Skill 生命周期与包管理

### 15.1 状态

Skill 的生命周期状态为：

~~~text
DISCOVERED
DOWNLOADED
VERIFIED
STAGED
INSTALLED
ENABLED
DISABLED
DEPRECATED
REMOVED
~~~

ENABLED / DISABLED 是可切换状态；其他状态表示发现、校验、安装、废弃和移除过程。合法状态转换由 Registry 校验。

### 15.2 安装与升级

~~~text
获取包
→ 校验 Manifest Schema
→ 校验 Publisher 与签名
→ 校验 Checksum
→ 检查 Foundation / Protocol 兼容性
→ 检查依赖与权限
→ 安全扫描
→ 写入 Staging
→ 执行 Contract Tests
→ 注册 Tool / Workflow
→ 激活
~~~

- V0.1 仅信任 ShuLiu 官方和企业内部 Publisher；第三方包默认 BLOCKED。
- 升级先保留旧版本，把新版本放入 Staging，验证通过后切换 Active Version；任何失败不得破坏旧版本。
- 权限增加产生 PERMISSION_CHANGE_DETECTED 并要求管理员重新确认；权限减少不增加授权。
- 正在运行的 AgentRun 继续使用启动时锁定的 Skill 版本。
- 卸载前检查活跃 AgentRun、Schedule、Workflow 和其他 Skill 依赖；有依赖时阻止移除或先要求停用。
- 移除 Skill 代码不自动删除业务数据。

### 15.3 兼容声明

Manifest 声明 Skill 自身 SemVer、所需 Foundation 范围、Skill Protocol 版本、Publisher、能力、权限和依赖。版本不兼容时安装阶段返回 INSTALL_BLOCKED，并说明最低要求。

## 16. IPC 与 Cloud-Worker 通信

### 16.1 Electron ↔ Python IPC

- IPC 有独立 Runtime Protocol 版本和可校验消息 Schema。
- Renderer 只访问 Electron Main 暴露的最小 allowlist API；不得提供任意命令执行或任意文件路径访问接口。
- 每个请求有 request_id、trace_id、超时和结构化错误响应；调用方可识别超时、进程退出和协议不匹配。
- Main 在 Python Runtime 启动后执行版本握手和 Health Check；版本或数据库不兼容时进入 Safe Mode。
- 本机 IPC 的具体传输方式、身份校验和权限隔离配置待决。

### 16.2 Cloud ↔ Worker

- Cloud 与 Worker 通过 Worker Protocol 发送注册、能力、任务租约、心跳、取消、进度和结果。
- 消息有协议版本、消息 ID、Trace ID、lease_id 和幂等标识。
- 传输必须加密并认证；凭据生命周期、证书或 Token 机制待 ADR 确定。
- Cloud 根据 Lease 和 fencing token 接受或拒绝 Worker 的结果。

## 17. 版本、发布、更新与回滚

### 17.1 版本维度

至少分开管理：

- Foundation 产品版本。
- Runtime Protocol 版本。
- Worker Protocol 版本。
- Skill Protocol 版本及各 Skill 自身版本。
- Event Schema 版本。
- 数据库 Schema / Migration 版本。

Foundation、协议、Skill、Workflow、数据库 Schema 分开版本化。产品成长不要求所有 Skill 同步升级。

### 17.2 SemVer 与兼容

Foundation 与 Skill 使用 MAJOR.MINOR.PATCH：

- PATCH：兼容的缺陷修复，不改变协议语义。
- MINOR：向后兼容的新能力。
- MAJOR：存在破坏性变更。

协议升级保留 Adapter 和明确的 Compatibility Window。Release 附 Compatibility Manifest，列出 Foundation、Runtime、Worker、Skill Protocol、Event Schema、数据库 Schema 和 Python Runtime 的兼容范围。具体支持窗口由版本策略 ADR 定义。

### 17.3 发布和依赖

- Release Channel 支持 dev、alpha、beta、stable；员工正式设备默认 stable，测试设备可使用 beta。
- Electron Desktop 与 Python Runtime、Tool Runtime、Adapters、Migration 和内置 Skills 作为一个 Release Unit。
- 第三方生产依赖使用精确版本并提交 Lockfile；依赖升级单独审查、测试和兼容验证。
- 记录第三方依赖的名称、版本、License、来源、修改状态和分发要求。
- 更新包需验证数字签名和 Checksum；安装或迁移失败不得继续启用 Agent。

### 17.4 分阶段更新与健康门

更新按小批量逐步扩大范围，观察 Crash Rate、Task Failure Rate、Runtime Health 和 Rollback Rate；异常时停止扩展。具体百分比和观察窗口在发布配置中定义。

新版本只有在以下检查通过后才标记 RELEASE_HEALTHY 并启用 Agent：

- Desktop 与 Python Runtime 健康。
- IPC 握手成功。
- 数据库健康、迁移和 Schema 校验通过。
- Worker Connection 达到配置要求。
- Skill Registry 可用。
- Model Gateway 与 Knowledge Service 达到可用状态。

失败时进入 Safe Mode 或执行兼容性检查后的回滚。

### 17.5 数据库迁移与回滚

- Migration 有唯一 ID、时间和 checksum。
- Schema 变化采用 Expand → Migrate → Contract，保留一段新旧代码兼容期。
- Migration 失败进入 Safe Mode；不得在未知 Schema 状态继续执行任务。
- App Rollback、Skill Rollback、Workflow Rollback 分别处理。
- 回滚前检查数据库和协议兼容性；不支持回滚时明确阻止，不能假定旧程序能读取已变更数据。

## 18. 错误模型与恢复

### 18.1 统一错误

ShuLiuError 包含 code、category、message、retryable、recoverable、severity、details、cause 和 trace_id。category 至少覆盖 VALIDATION、AUTH、PERMISSION、POLICY、APPROVAL、MODEL、TOOL、BROWSER、COMPUTER、NETWORK、WORKER、WORKFLOW、STORAGE 和 SYSTEM。

错误码使用可读名称，例如 MODEL_RATE_LIMIT、MODEL_TIMEOUT、BROWSER_NAVIGATION_TIMEOUT、WORKER_LEASE_EXPIRED、POLICY_DENIED、APPROVAL_EXPIRED 和 WORKFLOW_STEP_FAILED。

### 18.2 重试、降级与隔离

| 场景 | 恢复方式 |
|---|---|
| 模型限流 | 按 Provider 策略退避并抖动 |
| 临时网络故障 | 有上限的退避重试 |
| Browser 元素未找到 | 重新观察页面后再决定是否重试 |
| Computer 动作未确认 | 重新截图 / 观察，再验证当前状态 |
| 权限或参数错误 | 不重试 |
| 审批拒绝 | 不重试原动作，可走已定义的替代分支 |
| Worker 失联 | 等待 grace period，检查 Lease 后恢复或重新调度 |
| 多次失败仍不可恢复 | 进入 DEAD_LETTER，等待人工处理 |

对持续失败的外部服务使用 Circuit Breaker 的 CLOSED、OPEN、HALF_OPEN 状态，避免无限调用。Model fallback、Tool 重试和 Worker 重新派发都必须保留原 Trace，并通过 Policy 与幂等规则。

### 18.3 Safe Mode

以下情况可触发 Safe Mode：Python Runtime 连续崩溃、数据库迁移失败、Skill Runtime 异常、协议版本不兼容或其他无法安全继续的核心故障。

Safe Mode 下允许用户查看 Chat、日志和设置并使用修复工具；暂停 Agent 执行、Computer Use 和高风险 Tool。退出 Safe Mode 前必须重新执行 Health Check。

### 18.4 Browser / Computer 验证

执行遵循 OBSERVE → ACT → VERIFY。动作失败后先重新获取 DOM 或 Screenshot，再根据新状态决定后续动作；不得盲目重复点击。Tool 结果显式区分 VERIFIED、UNVERIFIED 和 FAILED，高风险流程要求验证通过后才能继续。

## 19. 安全与信任边界

1. **最小权限**：Skill 声明权限，管理员授予权限；每次运行只获得完成该次任务所需能力。
2. **Skill 供应链**：校验 Manifest、Publisher、签名、Checksum、兼容范围、依赖和安全扫描结果；未知 Publisher 默认拒绝。
3. **Secret 管理**：Secret 保存在专用安全存储；运行日志仅引用 secret_ref，不记录明文。
4. **Tool 执行边界**：任意副作用经过 Policy；高风险动作有审批、幂等键、执行后验证和审计。
5. **IPC 边界**：Renderer 不持有系统级权限；Main 只开放窄接口，不接受任意代码执行请求。
6. **Worker 边界**：Cloud 与 Worker 相互认证、加密传输；任务和结果均校验身份、协议版本和 Lease。
7. **Knowledge ACL**：先授权后检索；权限撤销后缓存和索引不能继续暴露旧内容。
8. **日志与 Evidence**：默认脱敏，按风险控制采集、访问和保留。
9. **依赖安全**：依赖版本固定、License 可追踪，升级经验证后进入 Release。

本节不替代具体威胁建模。身份模型、租户隔离、密钥管理实现、Skill 沙箱、签名根密钥轮换、数据静态加密和外部渗透测试范围均是待决安全项。

## 20. 测试与 V0.1 验收

### 20.1 测试层级

- **Unit**：Agent State Machine、Policy Decision、Workflow Condition、Scheduler Rule、Event Serialization、Migration 等纯逻辑。
- **Contract**：七大协议及 Runtime IPC、Cloud-Worker 消息 Schema、兼容性和错误响应。
- **Integration**：Agent + Model、Agent + Tool、Skill + Tool、Workflow + Approval、Scheduler + Workflow、Knowledge + Permission、Worker + Task、Event + Audit。
- **E2E**：以 Demo Browser Skill 走通创建任务、审批、执行、验证、完成和审计。
- **Failure Injection**：断网、超时、限流、Provider 失败、Browser 错误、Tool 重复回调、Worker 失联与 Lease 过期。
- **Crash Recovery**：杀死 Python Runtime、重启 Desktop、重启设备，在恢复后核对状态、事件、Checkpoint 和副作用。
- **Windows 实机**：验证打包安装、进程管理、权限、升级和回滚。
- **Security**：Skill 包篡改、权限升级、越权 Knowledge、敏感日志、IPC 非法请求和 Lease 伪造。
- **Release / Migration**：下载中断、签名或 Checksum 错误、安装中断、迁移失败、协议不兼容、健康门失败和回滚。

测试环境需提供 FakeModel、FakeBrowser、FakeComputer、FakeWorker、FakeTool 和 FakeApproval，避免核心测试依赖真实模型费用、真实账号和不稳定外部站点。

### 20.2 验收标准

V0.1 需要证明：

1. 七类协议均有机器可校验 Schema、版本和契约测试。
2. 一次 AgentRun 能从用户入口追踪到 Model、Tool、Workflow、Worker 和结果。
3. 高风险动作可被 Policy 阻止或转为 Approval；拒绝后不会执行副作用。
4. Workflow 与 Skill 更新不会改变正在运行的版本快照。
5. 对可重试副作用使用幂等键，故障恢复不会造成重复提交。
6. Checkpoint 恢复前检查外部动作状态；不能安全恢复时明确暂停或失败。
7. Worker 失联、租约过期和旧 fencing token 的结果均被正确处理。
8. 关键组件故障可进入 Safe Mode，用户能看到可读状态和 Trace。
9. Skill 包校验失败、权限增加未审批或协议不兼容时无法启用。
10. Release 健康门失败时不会启用 Agent；兼容性不允许时不会回滚到旧版。
11. 故障注入后 Event / Audit 保持连续，错误能定位到组件和执行节点。

### 20.3 初始性能目标

讨论中提出的第一阶段参考目标如下，必须通过实测校准后才能作为发布阻断门槛：

| 项目 | 初始目标 |
|---|---:|
| Desktop 冷启动 | 小于 5 秒 |
| 普通 UI 操作体感响应 | 小于 200 毫秒 |
| Agent 任务创建 | 小于 1 秒 |
| Event 写入 | 100 毫秒级 |
| 普通 Policy 决策 | 小于 50 毫秒 |

模型和第三方网站耗时不计入 Foundation 本身性能。Worker 心跳需稳定，具体延迟和丢包阈值待测量后确定。

## 21. ADR 目录

以下记录把本规格中的主要架构决策归档。具体 ADR 文件应在仓库连接后建立；这份工作区文档本身不代表 ADR 已独立评审。

| ADR | 决策主题 | 本规格记录的结果 | 状态 |
|---|---|---|---|
| ADR-001 | Foundation 产品边界 | 通用执行底座与业务 Agent / Skill 分离 | 讨论已确认 |
| ADR-002 | Desktop Runtime | Electron Desktop 监督 Python Runtime | 讨论已确认；实现细节待决 |
| ADR-003 | 本地与共享存储 | 本地 SQLite、Cloud PostgreSQL | 逻辑基线已确认；同步权威边界待决 |
| ADR-004 | 七大协议 | Agent、Skill、Tool、Workflow、Event、Worker、Model 独立定义 | 讨论已确认 |
| ADR-005 | Policy / Approval | 副作用前策略判定，必要时人工审批 | 讨论已确认；风险矩阵待补 |
| ADR-006 | 运行版本快照 | AgentRun 固定 Skill 与 Workflow 版本 | 讨论已确认 |
| ADR-007 | Worker 任务租约 | Lease、heartbeat、fencing token 防止 Zombie Task | 讨论已确认 |
| ADR-008 | 可观测性 | Trace 贯穿执行链，结构化 Log、Event、Metric、Evidence、Audit 分离 | 讨论已确认 |
| ADR-009 | Skill 生命周期 | 校验、Staging、Contract Tests 后激活；权限增加重新审批 | 讨论已确认 |
| ADR-010 | 版本和发布 | SemVer、协议独立版本、分阶段发布与健康门 | 讨论已确认 |
| ADR-011 | 数据库升级 | Schema 独立版本化，使用 Expand → Migrate → Contract | 讨论已确认 |
| ADR-012 | 故障恢复 | 错误分类、幂等重试、Checkpoint、Safe Mode 与 Dead Letter | 讨论已确认 |
| ADR-013 | Knowledge 向量存储 | pgvector 或其他实现 | 待决 |
| ADR-014 | IPC / Worker 身份认证 | 传输、身份、密钥和轮换机制 | 待决 |
| ADR-015 | 本地 / 云端数据同步 | 权威数据、离线写入、冲突解决 | 待决 |
| ADR-016 | 调度语义 | 引擎、时区、错过触发、分布式去重 | 待决 |

## 22. 风险

| 风险 | 影响 | 控制方向 |
|---|---|---|
| 任务恢复误判外部副作用 | 重复发消息、重复提交或重复发布 | 幂等键、执行前后核验、人工处理不确定结果 |
| 本地与 Cloud 状态不一致 | 丢任务、覆盖数据或错误展示 | 先明确权威边界、同步队列与冲突策略 |
| 恶意或被篡改的 Skill | 数据泄露、越权操作、Runtime 受损 | Publisher 信任、签名、Checksum、扫描、权限确认与沙箱决策 |
| Worker 失联但仍在执行 | 重复派发或旧结果覆盖新结果 | Lease、fencing token、grace period、提交校验 |
| 数据库迁移与 App 回滚不兼容 | 旧版本无法启动或数据损坏 | Expand / Migrate / Contract、Migration 校验、回滚兼容检查 |
| 模型路由变化导致行为变化 | 输出质量、成本或隐私边界改变 | 记录真实 Provider / Model，fallback 再过 Policy |
| Event / Evidence 携带敏感数据 | 隐私泄露和存储风险 | 脱敏、ACL、按风险采集与保留 |
| 未明确的身份与租户模型 | 权限边界无法验收 | 在接入真实 Cloud / 多用户前完成身份、租户和审计决策 |

## 23. 未决项与下一阶段输入

以下项目不会由本规格臆测，应在 Implementation Plan 或对应 ADR 中确定：

1. ShuLiu Foundation 的目标代码仓库、所有者、分支策略与 CI 环境。
2. Cloud 部署形态、身份和租户模型、管理员 / 用户角色以及授权来源。
3. SQLite 与 PostgreSQL 的数据所有权、离线队列、同步协议和冲突解决。
4. PostgreSQL / pgvector 的最终选择、Knowledge 本地索引和 Embedding Provider。
5. IPC 与 Worker 传输协议、身份认证、密钥 / 证书存储和轮换周期。
6. Scheduler 实现、时区、错过触发补偿、租约时间、心跳周期和 grace period。
7. Tool 风险分级、Approval 矩阵、权限粒度、默认超时和重试上限。
8. Skill 沙箱强度、扫描工具、签名根信任、Publisher 发布流程和废弃窗口。
9. Model Provider 清单、能力映射、预算限额、LOCAL_ONLY 的可用模型。
10. Event / Audit / Evidence 保留时间、容量、导出和删除规则。
11. Compatibility Window 的具体版本跨度、Release 分批比例和观察窗口。
12. 性能目标的真实测量环境与发布门槛。

## 24. 架构验收边界

本 Architecture Spec 完成后，架构阶段的交付物是：

- 可供评审的 V0.1 架构基线。
- 已确认决策与待决项的区分。
- ADR 目录和后续实施规划的输入。

这不等于 Foundation 已实现。进入工程阶段前，应先连接目标 ShuLiu 仓库，评审并确认本文，再形成 Implementation Plan；随后按计划实现并通过本规格定义的测试与验收。本文不包含业务代码。
