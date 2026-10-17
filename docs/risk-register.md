# ShuLiu Agent 风险登记表

## 状态定义

- 🟢 **Verified**：Architecture Spec 明确写出了该设计约束或风险控制方向；不代表代码已经实现或运行验收通过。
- 🟡 **Needs validation**：需要通过 ADR、原型、自动化测试或目标设备验收，当前没有实现级证据。
- 🔴 **Not recommended**：明确不建议采用的做法；不代表整个能力不可做。

## 风险列表

| 风险 | 状态 | 影响 / 触发条件 | 已知控制方向 | 仍需验证或关闭 |
|---|---|---|---|---|
| Windows GUI 自动化 | 🟡 Needs validation | UIA / Win32 / 视觉操作会受交互会话、权限、DPI、窗口状态和应用变化影响。 | 按 API → Native API/COM → Browser DOM → UIA → Vision GUI → raw coordinates 的优先级；动作后必须重新观察并验证。 | 目标 Windows 版本、Interactive User Session、锁屏状态、UFO² / UIA / Win32 适配和故障恢复需 Windows runner 与实机验证；具体 Adapter 未写入 Spec。 |
| 锁屏 / 休眠 | 🟡 Needs validation | 设备锁定、睡眠或用户会话断开可能暂停或改变 GUI 自动化行为。 | Worker Lease 与任务状态应持续可观测；失去执行环境时暂停安全敏感步骤并进入恢复流程。 | 锁屏、休眠、唤醒和用户注销的行为与恢复边界未由 Spec 定义；需目标 Windows 实机测试。 |
| 第三方平台变更 | 🟡 Needs validation | API、页面 DOM、登录流程、速率限制或政策变化可能使 Connector / Tool 失效或误操作。 | 优先使用稳定 API；Browser / Computer 遵循 OBSERVE → ACT → VERIFY；失败后重新观察，不盲目重复动作。 | 每个 Connector 的兼容范围、变更监控和回归证据待具体计划定义；平台侧变化无法由 Foundation 保证不发生。 |
| 浏览器会话 | 🟡 Needs validation | Cookie、登录态、授权弹窗和会话过期可能导致越权、数据泄露或错误目标操作。 | Secret 不写入 Log / Event；凭证只通过安全存储引用；Tool 执行前后验证当前账号和目标页面。 | Browser profile 隔离、会话生命周期、跨用户清理、锁屏行为和 Evidence 脱敏需验证。 |
| Skill 安全 | 🟡 Needs validation | 被篡改或越权的 Skill 可能访问不必要数据或触发副作用。 | Spec 要求校验 Publisher、签名、Checksum、兼容性、依赖和权限；V0.1 仅信任 ShuLiu 官方和企业内部 Publisher；权限增加需重新审批。 | 沙箱强度、任意代码是否允许、扫描工具、签名根轮换和供应链验收仍待决（Spec §§15、19、23）。 |
| Worker 掉线 | 🟡 Needs validation | 网络中断或进程崩溃可能导致任务长时间悬挂或重新派发时重复执行。 | heartbeat、Lease、grace period、fencing token、at-least-once delivery 与 DEAD_LETTER。 | Lease / heartbeat / grace period 数值、WSS 身份认证及网络分区测试待 ADR 与实现验证。 |
| Zombie task | 🟡 Needs validation | 已失去 Lease 的 Worker 仍运行并尝试提交旧结果。 | 关键结果提交时校验 lease_id、当前 Lease 和递增 fencing token；过期 Worker 停止提交。 | Worker 被暂停、断网后恢复、旧 token 与新 Worker 并存的故障注入测试尚未执行。 |
| 重复副作用 | 🟡 Needs validation | 外部动作已成功但本地未持久化结果，恢复时可能重复发消息、提交或发布。 | 使用 idempotency_key；恢复时先核对外部状态；不确定时暂停并要求人工处理。 | 对各类外部系统的幂等能力与对账方法不同；恰好一次执行不能作为默认保证。 |
| 本地 / Cloud 数据同步 | 🟡 Needs validation | 权威边界不清可能造成丢失、覆盖、重复或陈旧数据。 | Architecture Spec 要求先定义数据权威、离线写入和冲突策略；本地 / Cloud 不得假设完整双向同步。 | Spec §8.1 与 §23 明确将权威边界和同步列为待决；Implementation Plan 的 Cloud PostgreSQL Source of Truth / SQLite 范围结论需与 Spec 协调。 |
| 自动更新 | 🟡 Needs validation | 签名失效、下载中断、Schema 不兼容或健康门误判可能使客户端不可用或数据无法回滚。 | 校验签名、Checksum、兼容性与 Migration；健康门失败进入 Safe Mode；只做兼容性允许的回滚。 | 更新框架、分批比例、观察窗口、回滚窗口、签名轮换和断点恢复测试待决。 |
| Windows 打包 | 🟡 Needs validation | 原生依赖、签名、安装器、自动更新和权限行为可能与 macOS/Linux 不同。 | Windows 打包与安装、升级、回滚在 Windows runner 或 Windows 实机进行；Mac CI 不声明这些项通过。 | Windows runner 版本、证书与签名配置、目标 Windows 版本和实机回归尚未验证。 |
| 第三方依赖许可证 | 🟡 Needs validation | 不兼容许可证或分发义务可能阻止企业内部发布或后续商用。 | Architecture Spec 要求记录依赖名称、版本、License、来源、修改状态及分发要求。 | 自动许可证扫描、例外审批和发布阻断规则待实施计划定义并验证。 |
| 模型 fallback | 🔴 Not recommended | 无策略检查的 fallback 可能把 `LOCAL_ONLY` / 敏感数据发送给远端 Provider，或绕过模型预算与审批规则。 | 每次 fallback 都重新经过 Policy；记录实际 Provider / Model；`LOCAL_ONLY` 请求不得自动切换到远端。 | LiteLLM 的重试 / fallback 配置必须能被 ShuLiu Policy 层约束；Provider 路由与故障注入测试待完成。 |
| Knowledge ACL | 🟡 Needs validation | 检索、缓存或向量索引可能返回用户无权访问的文档片段。 | Spec 明确要求 ACL 在检索结果返回 Agent 前执行；权限撤销后使索引和缓存内容失效。 | pgvector 选择、ACL 查询计划、缓存失效和并发权限变更测试待决（Spec §§13、23）。 |

## 使用规则

新风险按 Spec 条目登记，明确来源、控制、状态和可验证证据。状态只有在实际证据满足定义后才能升级；把风险控制写进计划不等于验证完成。
