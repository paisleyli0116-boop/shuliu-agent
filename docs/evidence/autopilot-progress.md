# ShuLiu Dev Autopilot 执行进度

更新时间：2026-10-22（Windows）
项目分支：feat/foundation-v0.1
项目 HEAD：b2efed6e0f225b832e5d0eacb64f9768253d563f
产品任务：Task 28，仍为 BLOCKED。Task 29 未开始。

## 工作区保护

本轮开始及结束时，分支和 HEAD 未改变；分支仍比 origin/feat/foundation-v0.1 领先 2 个本地提交。工作区仍包含其他会话的 4 个已修改文件：apps/desktop/src/main/ipc/providers.ts、apps/desktop/tests/ipc/provider-settings.test.ts、apps/desktop/tests/ipc/sidecar-launch.test.ts、apps/desktop/tests/update/installer-config.test.ts；以及未跟踪的 docs/test-selection-map.md、scripts/。这些文件未修改、未暂存、未提交。Task Scheduler 查询结果为 NOT_INSTALLED。

## 本轮完成

- 真实 Windows 强制终止/重启验收：通过。测试启动独立 Python 进程，在本地标记副作用发生后强制终止；新进程恢复检查点时不重复副作用。审批使用次数保持为 1，未知结果保持 OUTCOME_UNKNOWN，任务为 WAITING_APPROVAL。
- 修复上限与人工恢复：通过。最多 3 轮后保存 WAITING_APPROVAL，创建限时、单次、限定为安全本地复测的 repair_resume 授权；已测试批准后只执行一次本地 runner，任务仍在门禁处停止。
- 安全修复：等待授权不再因工作区变化丢失；直接启动不能恢复已消费的审批；调度器只在本进程新预留审批后签发一次性恢复能力。repair_resume 授权绑定一个白名单测试套件，并核验 max_uses=1；失败、暂停和超时不会重新开放修复。续期只允许相同任务/操作/范围，OUTCOME_UNKNOWN 不允许续期。
- Mock 通知、审批、去重、脱敏及恢复相关单元测试包含在完整回归中。

## 测试结果

- Autopilot Windows 单元/集成测试：86 passed。
- Python 语法编译：16 个 Python 文件通过。
- 授权/恢复定向测试：10 passed；强制结束后重启的真实独立进程用例连续 3 次通过。
- 新增定向用例：修复耗尽等待审批、一次本地复测恢复、单套件授权、单次能力记录校验、过期续期和补丁格式约束均通过。
- 真实 DeepSeek 请求：未发起。
- Windows Credential Manager 中 Autopilot 飞书凭据：未配置（只检查存在状态，没有显示或导出秘密）；未发送真实飞书通知。
- Windows 计划任务：未安装、未启用。

## Codex 执行引擎实际诊断

Codex CLI 路径为 C:\Users\Administrator\.npm-global\codex.ps1，版本 codex-cli 0.163.0。默认受限子进程失败的实际根因是外层 Codex 文件沙箱拒绝 CLI 在当前用户 .codex 目录创建/打开运行状态数据库，日志包含 logs_2.sqlite 和 Windows os error 5。这不是 GitHub、DeepSeek 或 API Key 认证错误。一次受限执行环境外的只读 CLI 探针退出码为 0；未修改 Codex 全局配置或 Windows ACL。

真实修复闭环在隔离临时 Git 仓库中执行了最多 3 次：

1. 通用计算器夹具未能通过独立审查。该尝试没有完整保留审查分类，不能推断拒绝原因。
2. Task 28 合规门禁夹具的补丁未满足标准 unified Git diff 格式，UnsafePatch 在应用前拒绝。
3. 加强补丁格式约束后，Codex 返回候选补丁，但独立审查以退出码 0 返回 approved=false（明确 REJECT）。没有应用补丁。安全机制按预期阻止修改。

三次尝试上限已用完，本轮不再重试该问题。第三次运行的受控临时证据目录：D:\Documents\ChatGPT\数流\autopilot-task28-repair-final-u5jf22yd。目录仅含合成门禁代码、测试和脱敏状态/事件；未修改数流项目文件。

在修改授权恢复代码后，又发起了一次 Codex CLI 只读独立审查。它已读完指定文件，但 Codex 服务连接出现 TLS EOF、MCP 初始化失败和请求超时；本轮没有取得审查终结结论。没有再次启动同一请求。此前对旧版本的独立审查曾报告 3 项 HIGH 和 1 项 MEDIUM；上面的修复有本地测试证据，但当前版本仍待一次成功的独立审查，不能写成审查通过。

## Git 与交付

HEAD 保持 b2efed6e0f225b832e5d0eacb64f9768253d563f；本轮未提交、未推送，也未触发 CI。Autopilot 工具目录仍是未跟踪内容；四个其他会话修改保持原样。

## 阻塞与后续动作

- 自动修复端到端：BLOCKED。已达到三次上限；需要人工查看审查拒绝原因，再决定是否开启新一轮。本轮事件只保存了安全分类，没有保留原始审查内容。
- 当前版本独立审查：BLOCKED。唯一阻塞是 Codex CLI 到 Codex 服务的 TLS/MCP 连接失败，没有审查结论。恢复 Codex Desktop 的服务连接后，再对当前版本发起一次只读审查；不要重复此前已用完上限的三轮修复。
- 真实飞书通知端到端：WAITING_APPROVAL。若要启用，请创建飞书群自定义机器人并开启签名校验，然后在本机双击 tools/shuliu-dev-autopilot/配置飞书通知.cmd，在不回显的输入框中录入 Webhook 和签名密钥；不要把它们发到聊天或终端。完成后再授权发送一条测试通知。
- 在 Autopilot 修复/审查闭环和真实飞书通知均完成验证之前，后台计划任务必须保持关闭。
- Task 28 产品验收仍因真实 Provider 响应、Provider 合规审查、匹配 SHA 的 Windows Validation 等条件保持 BLOCKED；没有开始 Task 29。

## 2026-10-22：Autopilot 五项缺陷收尾（覆盖此前 Autopilot 数量记录）

本节是本轮最新执行证据；不改写以上历史记录。仓库仍在 `feat/foundation-v0.1`，HEAD 为 `b2efed6e0f225b832e5d0eacb64f9768253d563f`，相对上游领先 2 个提交。未提交、未推送、未运行 CI。Task 28 仍 BLOCKED；Task 29 未开始。真实 DeepSeek、飞书和后台任务均未启动。

本轮代码和测试修改限于 `tools/shuliu-dev-autopilot/` 内的 Autopilot 文件：`autopilot.py`、`test_autopilot.py`、`scheduler.py`、`test_scheduler.py`、`orchestration.py`、`approvals.py`、`test_approvals.py`、`progress.py`、`test_progress.py`；另在现有未跟踪的 `docs/evidence/autopilot-progress.md` 末尾追加本节。整个 `tools/` 仍为未跟踪目录；本轮未暂存。其他会话的四个已修改桌面文件、`docs/test-selection-map.md` 和 `scripts/` 保持原样。

### 五项缺陷及 RED/GREEN 证据

1. **授权范围恢复**：授权记录绑定 Task、动作、唯一白名单测试套件、到期时间及单次消费。RED 变异复现中，恢复错误地运行了 `approved-suite` 和 `unapproved-suite`，断言显示未授权套件被调用。修复后，恢复路径只运行授权套件，严格要求有效通过结果，不进入自动修复；暂停、失败、超时或中断均不恢复代码修改权限。对应 `test_codex_recovery_approval_runs_only_approved_suite_and_never_repairs`、`test_repair_resume_runs_only_the_suite_bound_to_approval_scope`、`test_repair_resume_pause_before_tests_consumes_approval_without_success`、`test_resume_that_returns_zero_without_running_approved_suite_is_not_success` 通过。
2. **修复额度持久化**：RED 强制终止复现得到持久 `repair_count=0`（预期已预留的 1），证明重启会丢额度。修复为每轮修复在工作开始前递增并立即保存。强杀/重启序列观察到 `[1, 2, 3, 3]`；到 3 轮后为 `WAITING_APPROVAL`，不再自动修复。两个持久化回归测试通过。
3. **并发与复测状态**：RED 并发复现中，另一个写入者未因正在运行的恢复检查点而被拒绝（`rejected=False`）。现在调度和授权写入共用实例锁及状态版本检查；只有匹配授权、实际执行唯一指定套件且得到有效成功结果才可记 `SUCCEEDED`。NOT_RUN、未知结果、冲突和中断均不能通过。对应并发调度、并发授权写入及恢复证明测试通过；并发测试各重复 10 次通过。独立审查指出一个线程测试 50ms 窗口存在调度空通过风险；已改为等待 STOP 线程实际遇到锁竞争的事件，定向测试通过，随后该竞争测试重复 50/50 通过。
4. **证据来源与门禁**：RED 复现/审查发现 Windows 盘符路径可以作为手工证据引用，且本地测试可能被误用于真实 Provider 门禁。现验证器按每个条件限定证据类型与来源；CI 记录必须匹配可信仓库、完整目标 SHA、工作流及成功结论；Provider 真实响应不接受本地测试替代。盘符、UNC、设备路径及无效/过期/错源 CI 证据被拒绝，Task 28 合成错误证据保持 BLOCKED，不能放行 Task 29。相关进度门禁测试通过。
5. **统一 STOP**：RED 在最终目标摘要校验后插入 STOP，复现补丁应用仍返回成功（预期拒绝）；另一个 RED 令操作锁超时，状态仍为 RUNNING（预期必须停止/阻塞）。最终独立审查另发现：用户在交互确认期间发布 STOP，审批记录仍可能变为 APPROVED。新增 `test_stop_published_during_confirmation_keeps_approval_pending`，RED 实际失败为“ApprovalError not raised”。现在进程启动、最终应用补丁、STOP 标记发布和交互审批 ledger 写入共用 `operation-start.lock` 顺序；审批提交时若 STOP 已生效则保持 PENDING，门锁不可用也 fail-closed。超时保存 BLOCKED 和 `operation_gate_unavailable`，不误报成功。直接启动、调度、恢复、审批、进程启动、测试边界、审查后应用均有 STOP 回归。最终检查竞态、确认期间 STOP、子进程启动、并发 STOP 及 gate 超时测试通过。

### 本轮验证

- Autopilot Windows 完整套件（含审批 STOP 竞态修复）：`python -B -m unittest discover -v`，**105 项通过，退出码 0**（1.299 秒）。
- 审批定向测试：新增 STOP 确认竞态测试先 RED（确认期间 STOP 生效后审批仍成功），修复后定向 2 项通过；审批模块全组 **11 项通过**。
- 修订后的 STOP 锁竞争测试：单次通过，另以同进程新临时目录重复 **50/50** 通过。
- Windows Skill 安全/进程生命周期相关回归：`test_windows_sandbox.py`、`test_sandbox_denials.py`、`test_process_host_lifecycle.py`、`test_process_host_crash_cleanup.py`，**29 项通过**（6.68 秒）；pytest 仅报告缓存目录不可写的警告，不影响测试结果。
- Python AST：Autopilot 目录 **16 个文件通过**；PowerShell AST：**2 个文件通过**。
- `git diff --check` 退出码 0。Git 对其他会话现有三个 LF 文件显示 LF→CRLF 提示；本轮未改这些文件的换行。
- 一次错误的测试类选择器在加载阶段报告类名不存在、没有运行用例；使用真实类名重跑后通过。授权定向运行首次导入 Autopilot 时显示其既有 `invalid escape sequence '\.'` DeprecationWarning，非失败且未改动该无关字符串。

### 独立复审与剩余风险

本轮独立只读复审确认 STOP 与操作锁的串行化、并发测试屏障及审批写入锁接线；未发现新的错误放行、循环导入、锁序死锁或错误报告成功。复审指出的并发测试同步弱点已改为等待实际锁竞争事件，并完成 50 次复测。复审随后指出交互审批确认期间的 STOP 竞态；该路径按 RED→GREEN 修复，独立审查确认：STOP 先发布则审批保持 PENDING，审批先拿到 gate 则 STOP 等审批写入完成后生效，锁序无环。

- GitHub CI 证据目前依据持久化证据字段校验仓库、完整 SHA、工作流、运行标识和结论，未在本地实时调用 GitHub API 对运行记录进行远端真实性核验；远端 CI 仍必须以对应 SHA 的真实 GitHub Actions 结果为准。
- STOP/审批对操作锁的等待有界（35 秒）；超时会 fail-closed，审批保持 PENDING 并返回错误。该机制不终止任意卡死的操作系统进程，用户需在 gate 空闲后确认 STOP 请求成功写入或重新操作。
- 相关 Python 测试曾因系统 Python 未安装 pytest 而未启动；随后用仓库现存 `.venv` 中的锁定测试运行器完成上述 29 项测试。没有新装依赖。
- 本轮未执行全项目测试、GitHub CI、真实 Provider、飞书或计划任务验证；Task 28 及 Autopilot 无人值守验收仍未关闭。

## 2026-10-22：Autopilot 安全修复与真实执行闭环复验

本节为最新执行证据，保留之前的历史记录。分支仍为 `feat/foundation-v0.1`，HEAD 仍为 `b2efed6e0f225b832e5d0eacb64f9768253d563f`。本轮未触碰 Task 28 产品代码、其他会话文件或 Git main；Task 29 未开始，DeepSeek 请求未发起，后台计划任务未安装/启用。

### 本轮变更与 RED→GREEN

- 失败诊断文本现在明确作为不可信输入，且敏感内容进入 Codex 提示上下文前经过脱敏。RED 用例曾复现失败输出缺少不可信边界和 PEM 内容未充分脱敏；修复后定向测试通过。
- 源码上下文仅允许读取预先批准的项目文件；新增 symlink、路径解析偏移、硬链接和文件身份变化测试，避免把路径外内容带入模型上下文。对应测试先暴露未拒绝的硬链接/身份变化路径，修复后通过。
- 候选补丁先在内存中完成结构和安全校验，再写入临时补丁文件。RED 测试实际观察到带 PEM 的无效补丁曾先落盘；修复后测试确认拒绝发生且秘密从未写入临时文件。
- 独立审查拒绝现在消耗有限修复轮数；最多三轮后持久记录 `WAITING_APPROVAL`，不得进行第 4 次模型生成或应用未通过审查的补丁。RED 时仅执行 1 轮即错误停止；修复后模拟审查连续拒绝，断言生成和审查都恰好 3 次，修复计数为 3，进入等待授权状态，并且没有应用补丁。

### 实际验证

- Autopilot 完整 Windows 测试：`python -B -m unittest discover -s . -p test_*.py -q`，**117 tests，OK，退出码 0**，约 1.5 秒。
- 定向安全测试：硬链接、读取期间文件身份变化及补丁 PEM 写盘前拒绝均通过； bounded-review-rejection 测试确认严格三轮上限并停入 `WAITING_APPROVAL`。
- Python AST：16 个文件通过；PowerShell AST：2 个文件通过；`git diff --check` 退出码 0。
- 独立只读安全复审：确认补丁临时文件先校验后写入、文件上下文检查拒绝符号链接/硬链接并核对文件身份；本轮未发现新的阻断项。
- 复审记录的限制：极端同一 Windows 用户并发替换路径仍不是操作系统级隔离；真实 Windows junction 夹具本轮未执行，不能宣称该场景已验证。

### 真实 Codex 执行结果

在隔离临时仓库进行一项合成代码修复与测试任务。Codex CLI 最多进行了三轮候选生成/独立审查；三轮候选均被审查拒绝（最后一轮结构化结果为 `decision=REJECT`，分类为 `other`）。没有候选补丁应用到临时仓库或 ShuLiu 产品仓库，没有调用 Provider、发送外部消息或执行其他有副作用操作。执行器达到上限后保存 `WAITING_APPROVAL`，未发出第 4 次 Codex 调用。真实自动修复端到端验收因此仍为 BLOCKED，不能用 117 项本地通过代替成功的真实修复闭环。

### 外部服务和交付状态

- 飞书：本机尚无机器人凭据；只验证了 Mock 通知/去重/脱敏，没有发送真实消息。要完成真实端到端通知，用户需在本机安全入口配置飞书群机器人的 Webhook 与签名密钥，并另行明确授权发送一条测试消息；密钥不得发到聊天或终端。
- GitHub：Autopilot 修改已提交到本地功能分支，提交为 `3892feef1612d8a3c595ef77041ad03abb3f6949`（完整 SHA 见下方交付回执）。本轮一次只读 `ls-remote` 因 Windows Credential Manager 无法保存凭据、无法取得密码而退出 1；已授权 GitHub 只读连接对私有仓库分支返回 404。未重复认证，也未推送；匹配 SHA 的 Windows Validation 尚无远端证据。
- Windows 任务计划程序：未安装、未启用。由于真实修复与飞书端到端验收未通过，不满足开启条件。
- Task 28：继续 BLOCKED（Provider 实际响应、合规审查和匹配提交 SHA 的 Windows Validation 等原始门禁仍未满足）。Task 29 未开始。

### 当前 Git 保护范围

本轮 Autopilot 文件在 `tools/shuliu-dev-autopilot/`；进度记录位于本文件；README 仅更新 Autopilot 测试数量和最新限制说明。其他会话的四个已修改 Desktop 文件、`docs/test-selection-map.md`、`scripts/` 均未纳入本轮。本节写入时 HEAD 未变化且尚未提交；随后交付回执记录了限定范围的本地提交。

### 本轮交付回执

- Autopilot 代码与说明已在 `feat/foundation-v0.1` 本地提交：`3892feef1612d8a3c595ef77041ad03abb3f6949`（提交内容仅为 `tools/shuliu-dev-autopilot/` 与 `docs/evidence/autopilot-progress.md`）。
- 推送被现有凭据状态阻塞：一次只读 Git 请求返回 `wincredman` 无法保存凭据及 `unable to get password`；GitHub 连接器读取私有分支返回 404。未再次登录、未向远端写入、没有对应新 SHA 的 CI 运行结果。
- 其他会话的四个已修改 Desktop 文件、`docs/test-selection-map.md` 和 `scripts/` 未提交；Git main 未修改。

## 2026-10-22：飞书通知与授权恢复组合验收准备

- 新增 `NotificationApprovalRecoveryIntegrationTests.test_blocker_notice_single_approval_and_scoped_recovery_with_fake_transport`。它从 `orchestration.queue_approval` 真实入口走到运行时通知格式化、签名请求构造和通知去重；HTTP 传输被拦截，凭据为合成值。随后在模拟本地交互控制台中批准唯一请求，并通过 `scheduler.tick` 仅执行批准的白名单本地复测。
- 结果：定向测试 **1/1 通过**（0.166 秒）。重复阻塞只构造一次发送；审批消费次数为 1；恢复测试集仅为 `autopilot self-tests`；恢复结果有效，但任务状态仍为 **BLOCKED**，没有改变产品任务门禁。
- 随后完整 Autopilot Windows 套件 **118/118 通过**（1.631 秒）；Python AST **16/16 通过**。
- 此为 Mock/拦截式传输验收，不是飞书真实投递。真实机器人凭据仍未配置，没有发送飞书消息，也没有调用 DeepSeek。
- GitHub：当前最新本地提交为 `9122cc7bc71a84c578be6c80662f2560e871ae29`，功能分支相对本地跟踪引用 `02b99c200e5168c6c3658f1b651a84293893fdb7` 领先 5 个提交。凭据确认仍失败于 GCM `wincredman` 无法保存凭据/无法取得密码；未推送，没有该 SHA 的 Windows Validation。未重复启动浏览器授权。
- 飞书组合模拟测试、README 和本进度记录已包含在本地 Autopilot 提交 `9122cc7bc71a84c578be6c80662f2560e871ae29`。四个其他会话 Desktop 修改、`docs/test-selection-map.md` 和 `scripts/` 仍未暂存、未纳入。

- `python -B autopilot.py status` 只读检查显示持久 Task 28 状态仍为 BLOCKED、`repair_count=1/3`、本地验证字段 `NOT_RUN`，状态记录的最后运行提交为 `b2efed6e0f225b832e5d0eacb64f9768253d563f`。该状态文件未被本轮改写；这是此前 Task 28 执行检查点，不代表 `9122cc7` 上重新运行过 Task 28。

## 2026-10-22：Autopilot 源码路径同用户 Junction 竞态

- 检查时真实仓库为 `feat/foundation-v0.1`，HEAD `9122cc7bc71a84c578be6c80662f2560e871ae29`，本地跟踪引用 `origin/feat/foundation-v0.1` 为 `02b99c200e5168c6c3658f1b651a84293893fdb7`，本地领先 5 个提交。领先提交为 `9d55a5b`、`b2efed6`、`3892fee`、`0fbd323`、`9122cc7`；工作区另有四个 Desktop 文件、`docs/test-selection-map.md`、`scripts/dev-env.ps1`，均属于既有其他会话工作，本轮未改、未暂存。
- Task 28 Autopilot 检查点初始记录 `last_commit_sha=b2efed6e0f225b832e5d0eacb64f9768253d563f`、状态与验收状态均为 `BLOCKED`、`repair_count=1/3`、`local_verification=NOT_RUN`。随后通过现有单实例锁和 `save_state` 将提交元数据依次对齐至 `9122cc7bc71a84c578be6c80662f2560e871ae29`（状态版本 1）及 `d5332417cc7980b531eea1ac6355dfaa360cce6a`（状态版本 2），并各写入一条脱敏事件；两次复核均保留 `BLOCKED`、`repair_count=1/3`、`local_verification=NOT_RUN`。未运行 Task 28 全套；该元数据核对不代表在这些提交上完成 Task 28 验收。
- 真实根因：`_read_allowed_source_context` 在解析 allowlist 路径后，使用路径字符串打开文件；同一 Windows 用户可在路径检查与 `open` 之间将父目录替换为指向仓库外的真实 junction。修复前的隔离复现确实从仓库外读取到合成哨兵内容；这属于已确认的同用户路径越界缺陷，不是单纯的测试竞争。
- TDD RED：新增 `test_repair_context_rejects_real_junction_race_to_outside_source` 后，在 Windows 临时目录中先完成 `mklink /J` fixture，再运行定向用例。修复前真实失败为 `UnsafePatch not raised`。初次测试选择器和 junction 命令调用曾分别在装载/fixture 阶段失败；修正后得到上述有效 RED 证据。
- 最小修复：`autopilot.py` 在打开文件句柄后、读取前及读取后调用 `GetFinalPathNameByHandleW`，将句柄实际最终路径与预先解析的 allowlist 路径做 Windows 大小写不敏感比较；查询失败、超长路径或不一致时 fail closed。未改变其它平台分支或操作系统权限。
- GREEN：真实 junction 回归 **1 项通过**；该回归另连续重复 **10/10 通过**。相关路径边界、读取期间文件身份变化、硬链接拒绝 **4/4 通过**；允许文件读取及脱敏上下文 **3/3 通过**；Python AST **2/2 通过**；`git diff --check` 通过。未重跑此前已通过的 118 项 Autopilot 全套或 Skill 进程回归。
- 独立只读安全审查完成：审查未发现该句柄最终路径校验存在明显绕过，确认 Windows 大小写与 UNC 前缀处理后会拒绝不匹配路径，且查询失败时 fail closed。审查建议进一步断言具体安全错误并确认错误文本不含外部内容；已将这两项加入真实 Junction 测试，并在 Windows 上复测 **1/1 通过**。本轮另运行 Python AST **2/2 通过**及 `git diff --check` 通过。代码与测试已限于三文件提交 `01fb5ed86cb419c51c8d931e9fd06738a9ade88b`；官方 Provider 资料的一审记录已单文件提交 `d5332417cc7980b531eea1ac6355dfaa360cce6a`。未包含其他会话修改。
- Skill Job Object 启动/清理代码本轮未改。此前已有本地回归记录为 55 passed、2 skipped，但本进度文件未保存两项跳过用例名称；CI #51 曾报告 `descendant-survived`，其修复后的当前 HEAD 仍无对应 Windows Validation。真实 Skill 隔离、退出清理和 CI 结果因此尚不能整体标为已验收。
- 对远端仅进行一次无提示只读 `ls-remote`：退出码 **128**，停在 `wincredman` 无法取得凭据（`unable to get password from user`），未到仓库权限阶段；未尝试第二次认证、未推送。需用户在正常 Windows 登录桌面运行 `git credential-manager github login --browser` 并完成浏览器登录，不要复制 Token。
- Task 28 仍为 **BLOCKED**；没有真实 DeepSeek 请求。必需未完成项仍包括匹配提交完整 SHA 的 Windows Validation、真实 Provider 成功响应及 Provider 许可/数据处理/安全审查。Task 29 未开始，后台计划任务保持关闭。
