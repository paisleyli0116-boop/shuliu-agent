# ShuLiu Agent 技术基线

## 阅读说明

Architecture Spec 是架构事实源。下表区分 **Spec 已记录**、**部分记录**、**Spec 待决** 和 **仅在当前项目输入中提出**。后两类不能在实施时被当作 Spec 已批准的事实；若要固化，先更新 Architecture Spec 或对应 ADR。与 Spec 不一致时，以 Spec 为准。

## 基线与状态

| 技术 / 约束 | 当前基线 | Spec 状态 |
|---|---|---|
| Desktop | Electron Desktop；Electron Main 监督 Python Runtime，Renderer 通过窄 IPC 工作。 | 已记录；React 与 TypeScript 未在 Spec 中明确。 |
| Node | Node 22 LTS，最低 22.12。 | 仅在当前项目输入中提出；Spec 未规定版本。 |
| Python | Python 3.11。 | Spec 确认 Python Runtime，但未规定版本。 |
| Desktop UI | React + TypeScript。 | 仅在当前项目输入中提出；Spec 未明确前端框架和语言。 |
| Cloud 数据库 | PostgreSQL 用于 Cloud 共享数据；数据库 Schema 独立版本化。 | 已确认逻辑基线；Cloud 是否为业务 Source of Truth 在 Spec §23 仍列为待决。 |
| 向量检索 | PostgreSQL + pgvector 是当前技术输入中的目标组合。 | PostgreSQL 已记录；pgvector 在 ADR-013 Pending。不能标为已批准选型。 |
| 本地数据库 | Local SQLite。 | 已记录；其与 Cloud 的数据权威边界及同步方案待决。 |
| SQLite 数据范围 | cache / outbox / inbox / checkpoint / local task state；不做完整双向同步。 | 当前 Implementation Plan 有此确认；Spec §8.1、§23 仍列同步权威边界待决，需先协调 Spec。 |
| Electron ↔ Python | stdio + NDJSON。 | 当前 Implementation Plan 有此确认；Spec §16.1 将传输方式与身份校验列为待决。 |
| Cloud ↔ Worker | WSS + heartbeat + lease + ACK + idempotency。 | Spec 确认 heartbeat / lease / 幂等 / fencing 的架构机制；WSS、ACK、身份认证和时序参数尚未在 Spec 中闭合。 |
| Browser Runtime | browser-use adapter。 | 仅在当前项目输入中提出；Spec 只规定受控 Browser Tool 与 OBSERVE → ACT → VERIFY。 |
| Windows Runtime | UFO² / UIA / Win32 adapter。GUI 自动化在 Interactive User Session 执行，不作为普通 Windows Service 执行。 | 仅在当前项目输入中明确具体 Adapter 与会话约束；Spec 要求 Windows 实机测试，但未选定这些实现。 |
| GUI Agent | UI-TARS Operator。 | 仅在当前项目输入中提出；Spec 未记录此具体组件。 |
| Model Gateway | ShuLiu Policy Layer + LiteLLM；Skill 请求能力，不绑定供应商；路由需服从 Policy 和数据敏感级别。 | Gateway 与能力抽象已记录；LiteLLM、Provider 清单、模型映射、预算和本地模型范围待决。 |
| Scheduler | APScheduler 负责 Trigger；Trigger 与 Workflow Execution 分离。 | Scheduler 与持久化触发目标已记录；APScheduler、时区、错过触发、去重和运行参数在 ADR-016 Pending。 |
| MCP | MCP 作为外部 Tool / Connector 协议。 | 当前项目输入中提出；Architecture Spec 七类协议与模块边界未明确记录 MCP。 |
| Secrets | 本地使用 OS secure storage；Cloud 使用 cloud secret manager；日志只记录 secret_ref，不记录明文。 | Spec 确认专用安全存储与日志脱敏原则；OS/cloud 的具体机制在 Spec 未定。 |
| Windows 构建与验收 | Windows 专属打包、安装、升级、回滚在 Windows runner 或 Windows 实机验证；GUI 行为还需 Interactive User Session 实机验收。 | Spec §20.1 明确 Windows 实机测试；具体 runner 标签和构建工具未定。 |

## 已记录的跨模块约束

- 七类领域协议分别版本化；Runtime IPC 另有独立协议版本。
- 初始领域协议兼容性基线为 `protocol_version: 1`。
- Model fallback 受 Policy 约束；`LOCAL_ONLY` 数据不得因本地模型失败自动转发到远端 Provider。
- Policy 决策为 `ALLOW`、`DENY`、`REQUIRE_APPROVAL`；Fail Closed 及完整风险矩阵需按正式 Spec / ADR 确认。当前 Implementation Plan 记录了 Fail Closed，但 Architecture Spec 尚未以相同明确措辞闭合。
- 高风险动作需按 Policy 进入 Approval，并保留执行前后 Evidence 与结果验证。
- Skill / Update 包需校验签名、Checksum、兼容性；具体工具、信任根、沙箱和更新渠道仍待正式 ADR。
- Windows-only 验收不可由 Mac 测试替代。

## 后续固化规则

这份清单是技术基线索引，不替代 ADR。凡“仅在当前项目输入中提出”或与 Spec 冲突的条目，在作为实现约束前都应由 Architecture Spec 或正式 ADR 记录并解决状态冲突。
