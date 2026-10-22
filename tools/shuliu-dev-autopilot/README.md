# ShuLiu Dev Autopilot

这是 ShuLiu 仓库旁边的 Windows 本地开发控制工具。状态文件和运行日志保存在用户本机，不包含 API Key。

## 当前运行边界

当前任务仍是 **Task 28，状态 BLOCKED**。执行器只会跑 Task 28 的本地 Fake/Mock、Desktop 和类型检查；不会调用真实 Provider、Task 29–36、Task 27、提交、推送或部署。持久计划按 28→29→30→31→32→33→34→35→36→27 管理；每个任务必须通过原计划门禁和匹配完整 SHA 的 Windows Validation 才能标为 PASSED。当前只实现 Task 28 本地执行器，其他任务处理器尚未交付，不能把它称为全项目无人值守开发已经完成。

Codex 通过当前 Windows 用户登录的 Codex CLI，以只读方式生成补丁。编排器会校验路径、禁止新增/删除/改权限、保护并行修改，再要求第二次独立只读审查明确通过后才应用。单个问题最多三轮修复；每条测试命令有时限，单轮最长 90 分钟。真实 Provider 请求没有自动执行器，必须逐次由用户明确批准。

## 日常控制

双击本目录中的：

- 启动自主开发.cmd：启动一轮当前任务的本地验收。
- 暂停.cmd：在当前安全边界暂停。
- 恢复.cmd：清除暂停标记；仍会遵守 BLOCKED 和授权门禁。
- 查看进度.cmd、查看最后失败.cmd：查看状态与脱敏失败原因。
- 紧急停止.cmd：写入停止标记，后台任务在安全边界退出。
- 人工授权.cmd：显示当前单次授权的任务、范围和有效期；计划任务/非交互进程不能批准。
- 配置飞书通知.cmd：交互式输入机器人 Webhook 和签名密钥，输入不回显，保存到当前 Windows 用户的 Credential Manager。只有输入 SEND 才会发一条测试通知。
- 后台状态.cmd、安装后台任务.cmd：查询或安装 Windows 任务计划程序入口。安装脚本会检查本地 AUTOPILOT_ACCEPTED.json 验收标记；当前标记尚未生成，因此不会启用后台运行。

PowerShell 备用命令：

    python .\tools\shuliu-dev-autopilot\autopilot.py start
    python .\tools\shuliu-dev-autopilot\autopilot.py status
    python .\tools\shuliu-dev-autopilot\autopilot.py last-failure
    python .\tools\shuliu-dev-autopilot\autopilot.py pause
    python .\tools\shuliu-dev-autopilot\autopilot.py resume
    python .\tools\shuliu-dev-autopilot\autopilot.py emergency-stop

## Feishu 与人工授权

使用飞书群自定义机器人时，机器人只能向群发消息，不能接收并处理你的回复。因此消息只负责提醒；授权必须回到本机“人工授权”入口完成。消息只包含当前任务、阻塞、已尝试步骤、唯一需要你做的事和恢复方法，不带日志正文、密钥或 Webhook。

在飞书群机器人配置页启用签名校验。将 Webhook 和签名密钥粘贴到本机设置窗口中；不要发到聊天或终端。机器人当前未配置，本机没有完成真实飞书投递测试。

## 状态与记录

默认路径：

    %LOCALAPPDATA%\ShuLiuAgent\DevAutopilot\Task28\state.json

events.jsonl、project-progress.json、approvals.json、notifications.json 和停止/锁文件在同一目录。

日志记录任务、完整 Git SHA、测试命令退出码、耗时、修复轮次、阻塞原因及通知发送状态；不记录密钥、Webhook 或通知正文。暂停或崩溃后，确定可安全重跑的本地测试会从检查点恢复；未知结果的外部操作不会自动重放。

## Codex 执行引擎与验收情况（2026-10-22）

当前安装路径为 `C:\Users\Administrator\.npm-global\codex.ps1`，版本 `codex-cli 0.163.0`。Autopilot 使用受支持的短任务入口 `codex exec --sandbox read-only --ephemeral`，以结构化 JSON Schema 接收补丁；仓库代码没有调用旧的 `codex mcp-server`。短任务采用 `codex exec`，持久流式会话和产品内审批才需要评估 app-server。

真实失败的根因已通过前后对照定位：启动的子 CLI 继承了桌面父会话的内部工具管道、会话/线程/调用标识和来源覆盖环境变量，导致独立 CLI 误用父会话工具连接并在 MCP 握手阶段退出。只移除这组父会话上下文后，保留用户 Codex 配置、登录态、沙箱和网络策略，同一 CLI 命令可以正常完成临时仓库任务。此前日志只给出握手失败，没有 HTTP 状态，所以无法据此声称是认证失效或远端服务故障。

真实临时仓库验收已完成：初始单元测试按预期退出 1；Codex CLI 在 56.2 秒内以结构化响应返回只修改 `calculator.py` 的补丁，CLI 退出 0；补丁仅应用到临时仓库，复测退出 0。没有修改 ShuLiu 产品文件、访问 Provider 或触发外部副作用。另一个原有错误映射问题也用 RED→GREEN 修复：超时退出不再被 MCP 文本覆盖成错误类别。

Windows 本地回归现为 **118 项通过**（本轮加入授权、进程恢复和安全边界回归）。TDD RED 已分别复现父会话变量泄漏、MCP 故障未进入人工等待、权限错误未分类、超时被误分类、恢复审批过于宽泛、检查点可重放、并发授权记录写入冲突及消费审批后的崩溃窗口；新增测试覆盖环境隔离、错误分类、明确人工确认、Windows 独立进程单次审批消耗、强制终止后的未知结果恢复、三轮修复耗尽后的 WAITING_APPROVAL 及单次本地复测恢复。人工复测授权现在绑定唯一白名单套件，执行器不会跑其他套件或再次改代码。修改后定向测试和全套 118 项测试均通过。

MCP、Codex 登录或权限类失败现在保存安全错误类别/阶段并转为 `WAITING_APPROVAL`，创建限时单次的 `codex_service_recovery` 请求。精确确认语句要求人工明确声明“CODEX SERVICE RESTORED”；仅检测到外部状态变化不会自动授权。执行前会在有界的跨进程审批锁内消耗单次许可。若执行中断且结果未知，检查点不会自动重放，需人工核对。批准仅恢复一次安全的本地验证，无法自动批准 Provider 请求或其他外部操作。结构化日志不保存原始异常、URL、请求正文或密钥。超时状态保留为超时，不会被输出中的 MCP 字样覆盖。

真实飞书投递未测试：本机 Windows Credential Manager 尚无 Autopilot 飞书凭据。审批、崩溃恢复、去重通知目前有单元测试证据；本轮真实 CLI 临时仓库任务通过，但完整 Autopilot 产品修复加独立真实审查尚未端到端验证。隔离子进程被强制终止后的检查点恢复已有 Windows 实际进程测试证据；后台计划任务的崩溃续跑尚未验收。真实机器人投递、GitHub 自动提交/推送/CI 对账和 Tasks 29–36、Task 27 的通用任务执行器也尚未交付。Windows 计划任务尚未安装或启动，必须继续保持关闭。

由于端到端自动修复、真实飞书通知、后台任务恢复和独立审查均未验收通过，持续计划任务保持关闭；Task 28 继续 BLOCKED。
本轮补充证据（2026-10-22）：Windows Autopilot 回归 86/86 通过，16 个 Python 文件语法编译通过。test_force_killed_autopilot_process_does_not_replay_recovery_effect 在真实独立 Python 子进程中强制终止执行器，再通过新进程恢复；副作用标记保持恰好一条，授权保持 OUTCOME_UNKNOWN，任务进入 WAITING_APPROVAL，恢复时未重放；该强制终止用例连续运行 3 次均通过。repair_resume 授权现在只可对应一个白名单本地测试套件，失败、暂停、超时或异常不会恢复代码修改权限；检查点、单次使用数和续期范围均有定向测试。

当前 Codex CLI 的只读启动探针退出码为 0。默认受限执行时曾因外层沙箱拒绝 CLI 写入当前用户 .codex 状态数据库而失败（Windows os error 5）；日志定位到 logs_2.sqlite。隔离真实修复闭环共尝试 3 次，已按上限停止：一次审查未明确通过，一次补丁格式校验拒绝，一次独立审查以退出码 0 明确返回 REJECT。所有补丁都未应用到数流项目。修复后针对新的 Autopilot 状态机做的独立审查未能完成：CLI 请求 Codex 服务时出现 chatgpt.com TLS EOF、MCP 初始化连接失败和请求超时；本轮没有最终审查结论，未重复启动。真实飞书凭据检查为未配置，未发送通知。计划任务仍未安装。

### 最新验收补充（2026-10-22）

Autopilot 当前完整本地套件为 **118 项通过**。本轮增加了失败输出不可信提示、密钥脱敏、源码路径/硬链接/文件身份校验、候选补丁写盘前校验，以及独立审查拒绝时最多三轮后进入 `WAITING_APPROVAL` 的边界测试。独立安全复审确认本轮修复关闭了 PEM 补丁落盘泄露及路径边界问题；极端同用户并发路径替换仍不等价于操作系统级沙箱，Windows junction 场景尚未实测。

真实 Codex 临时仓库修复演练启动了三轮。三轮候选均未通过独立审查，执行器按上限停止并保存审批等待状态，没有应用候选补丁，也没有发起第四轮。因而“Codex 自动编码后可通过审查并完成修复”的端到端验收仍未通过；后台任务继续关闭。真实飞书尚未配置或发送，DeepSeek 请求未发起。

飞书与授权恢复的本地模拟集成测试已覆盖完整链路：阻塞触发签名通知、重复事件去重、单次人工批准、仅运行批准的本地复测，并确认 Task 28 仍保持 BLOCKED。测试使用合成凭据和拦截式 HTTP 传输，没有发送真实飞书消息或 Provider 请求。真实飞书投递仍待本机配置机器人凭据。
