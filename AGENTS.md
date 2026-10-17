# ShuLiu Agent 仓库协作说明

## 项目身份与当前阶段

- 当前项目唯一名称：**ShuLiu Agent**。
- 当前阶段：**Foundation V0.1**。
- 当前只开发第一层通用积木底座。
- 不开发采购、仓库、直播、新媒体、HR 等第二层岗位机器人，也不把岗位业务规则写入 Foundation。
- 当前唯一允许操作的仓库是 `paisleyli0116-boop/shuliu-agent`。不得因为其他工作区、目录、仓库或聊天内容而切换项目上下文，也不得读取或修改其他项目来完成本项目工作。

## 开始任何项目工作前

依次读取以下文件：

1. `docs/superpowers/specs/2026-10-17-shuliu-agent-foundation-v0.1-design.md`
2. `docs/superpowers/plans/2026-10-17-shuliu-agent-foundation-v0.1-implementation.md`
3. `docs/project-context.md`

如果任务只涉及记忆或决策台账，也要先读取上述文件中与任务相关的部分。

## 项目事实源

- Architecture Spec 是**架构事实源**。
- Implementation Plan 是**施工事实源**，描述实现顺序、文件、测试和验收。
- `docs/project-context.md` 是项目定位与分层关系的快速入口；它不覆盖前两份正式文档。
- 本目录其他说明文档是索引或专题台账，不能自行覆盖 Architecture Spec 或 Implementation Plan。
- 聊天内容与仓库正式文档冲突时，以仓库文档为准。Architecture Spec 与 Implementation Plan 对架构事实存在冲突时，以 Architecture Spec 为准，并把冲突标记为待协调；不要自行选择一方改写为已确认。
- 未确认的技术决策必须保持 Pending。不得把示例、讨论、外部项目经验或个人推断写成已确认决策。

## 实现边界与工程纪律

- Foundation 只提供通用 Agent 运行、协议、Tool、Skill、Workflow、Knowledge、Policy、Approval、存储、Worker、更新、恢复与审计能力。
- 岗位能力只能通过 `Skill + Workflow + Tool + Connector + Knowledge + Permission` 组合接入。任何岗位需求若要求修改 Agent Core，应先检查架构边界。
- 不允许在 `main` 分支直接编写或修改产品实现代码。实现必须在独立开发分支或 worktree 中进行。
- 产品实现采用 TDD：先写失败测试并确认 RED，再做最小实现得到 GREEN，然后整理代码并重跑验收。
- 文档任务可以按用户明确要求在 `main` 上完成；不得借文档任务开始产品实现。
- Windows 专属的 GUI 自动化、打包、安装、升级和回滚验证必须标记并安排在 Windows runner 或 Windows 实机完成。Mac 验证不能代替 Windows 验收。
- 对任何跨模块接口、风险等级、同步语义、模型路由或安全边界，先检查正式文档；若未决，先保持 Pending，不擅自补成事实。
