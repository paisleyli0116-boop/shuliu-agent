# ShuLiu Agent 项目上下文

## 项目目标

ShuLiu Agent 面向企业场景，目标是提供一套可组合、可审计、可恢复的 AI Agent 平台。Foundation 负责通用运行机制、权限边界、协议、状态、恢复和审计；具体岗位能力在其上组合。

## 三层产品架构

1. **ShuLiu Foundation**：通用积木底座，提供 Agent Runtime、Model Gateway、Skill / Tool Runtime、Workflow、Scheduler、Knowledge、Policy / Approval、Event / Audit、Worker、存储、更新与恢复等共用能力。
2. **Role Agents**：采购、仓库、新媒体、直播、HR 等岗位机器人，按岗位需求组合 Foundation 能力。
3. **Enterprise Intelligence**：跨岗位协同与老板 Agent，在多个岗位 Agent 的数据与能力之上形成企业级协同和决策支持。

当前只开发第一层 **Foundation V0.1**。第二、三层是产品方向说明，不属于当前工程交付范围。

## Foundation 与岗位能力的边界

岗位能力通过以下组件组合接入 Foundation：

```text
Skill + Workflow + Tool + Connector + Knowledge + Permission
```

Agent Core 只负责通用任务状态、运行编排和受控决策，不知道采购、仓库、直播、新媒体或 HR 的具体业务。岗位需求原则上应由上述组合实现；若必须改 Agent Core，先视为架构边界问题并回到正式文档审查。

## 当前架构重点

- AgentRun 固定启动时使用的定义和版本。
- 有副作用的动作先经过 Policy；需要人工决定时进入 Approval。
- Worker 通过 Lease、fencing token 和幂等规则处理至少一次投递与失联恢复。
- 运行过程通过 Event、Trace、结构化 Log、Metric、Evidence 和 Audit 留下可追踪记录。
- 故障恢复前检查外部副作用状态，不能盲目重放最后一步。
- Safe Mode 用于核心组件不健康或兼容性无法确认时暂停不安全执行。

## 文档入口与权威关系

- `AGENTS.md`：每次新会话或开始工作时的项目入口与协作约束。
- `docs/superpowers/specs/2026-10-17-shuliu-agent-foundation-v0.1-design.md`：架构事实源，明确范围、边界、协议和待决项。
- `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-implementation.md`：施工事实源。Implementation Plan 已获批准，现有 36 项逐文件 TDD 工程任务（含独立 Skill 安全扫描 Task 35、OS sandbox provider Task 36 和 Release Gate）；当前按顺序执行 Task 18。经验证的 OS sandbox provider 尚未就绪，因此 executable Skill 默认 fail closed。计划获批不代表所有能力或 Release Gate 已完成。
- `docs/architecture-decisions.md`：按 Architecture Spec 汇总已确认与 Pending 的架构决策。
- `docs/technical-baseline.md`：列出 Spec 已记录的技术基线及尚未归档到 Spec 的实现选型输入。
- `docs/risk-register.md`：记录已知风险与需要验证的控制项；状态不表示功能已实现。
