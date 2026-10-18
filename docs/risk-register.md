# ShuLiu Agent Foundation V0.1 风险登记表

## 状态定义

- 🟢 **Verified**：架构约束已写入 Architecture Spec / ADR；不代表实现或运行验收已经通过。
- 🟡 **Needs validation**：需要通过 TDD、故障注入、目标环境或发布门禁取得实现级证据。
- 🔴 **Not recommended**：明确禁止或不建议的模式；不表示整个能力不可做。

## 风险列表

| 风险 | 状态 | 影响 / 触发条件 | 已确认控制 | 尚需验证 / 门槛 |
|---|---|---|---|---|
| Windows GUI 自动化 | 🟡 Needs validation | Interactive Session、权限、DPI、窗口焦点和应用变化会造成误操作或无法执行。 | Windows GUI 在 Interactive User Session；工具优先级 API → Native API / COM → Browser DOM → UIA → Vision GUI → raw coordinates；OBSERVE → ACT → VERIFY。 | UFO² / UIA / Win32 适配、锁屏状态、安全暂停、结果验证必须在 Windows runner / 实机验证；不支持的环境不得声称通过。 |
| 锁屏 / 休眠 / 注销 | 🟡 Needs validation | 桌面会话被锁定、休眠或用户注销，可能暂停 GUI 动作或使结果不确定。 | Worker Lease 和任务状态持续可观测；安全敏感操作不能在失去执行环境时盲目续跑。 | Windows Interactive User Session 覆盖锁屏、睡眠、唤醒、注销恢复；结果不确定时进入暂停 / 人工处理。 |
| 第三方平台变更 | 🟡 Needs validation | API、DOM、认证、速率限制或平台规则变化会破坏 Connector 或改变目标。 | 优先 API / Native API；动作重新观察并验证；失败后禁止盲目重复副作用。 | 按平台建立回归与变更监控；Foundation 不保证第三方界面稳定。 |
| 浏览器会话 | 🟡 Needs validation | Cookie / 登录态混淆、会话过期或账号切换可能造成越权或错误账户操作。 | `browser-use` adapter 作为受控 Tool；Secret 不进入日志；执行前后验证当前身份与目标。 | Profile 隔离、会话过期、重登、清理和敏感 Evidence 脱敏测试。 |
| Skill 供应链与沙箱 | 🟡 Needs validation | 恶意、篡改或越权 Skill 可能窃取数据、秘密或控制主机；签名和 checksum 只证明发布者与内容完整性，不证明内容安全。 | V0.1 仅 ShuLiu Official / Company Internal；独立进程隔离；禁止直接拿 Secret、操作 OS、任意网络和直写 Foundation DB；副作用经 Tool Broker → Policy → Approval → Executor；无第三方 Store。 | Task 18 验证受控独立进程及 fail-closed 平台能力；Task 35 单独接入真实安全扫描引擎并建立 `skill_security_scan` Gate，验证 EICAR、超时、不可用和规则版本；不能隔离或扫描的环境必须拒绝激活。 |
| Worker 掉线 | 🟡 Needs validation | 断网或崩溃导致任务悬挂或被重复派发。 | WSS、heartbeat、Lease、短期会话凭据、at-least-once、ACK、重连和幂等去重。 | 故障注入覆盖断网、恢复、过期凭据、Lease grace 与 ACK 丢失；数值参数试点前配置。 |
| Zombie task | 🟡 Needs validation | 失去 Lease 的旧 Worker 恢复后提交过时结果或副作用。 | Cloud 提交校验当前 `lease_id` 与递增 fencing token；无有效 Lease 不接收结果。 | 暂停 / 断网旧 Worker 与新 Worker 并发，验证旧 fencing token 永远不能覆盖新结果。 |
| 重复副作用 | 🟡 Needs validation | 外部动作成功但 ACK / 本地持久化丢失，恢复时产生重复发送、发布或记录；进程内 `InMemoryIdempotencyStore` 在重启后不保留 key / result。 | 有副作用的 Tool 使用稳定 idempotency key；真实副作用 Tool 启用前必须接入 durable idempotency store；恢复前核对外部状态；无法确认时暂停并交由人工。 | Release Gate 的 `durable_idempotency` 必须通过；跨进程重启、重复请求、ACK 丢失、超时后成功、key 冲突和结果对账测试；不承诺通用 exactly-once。 |
| 数据同步 / Outbox | 🟡 Needs validation | 离线、ACK 丢失或重复 Event 导致数据遗漏、重复应用或本地状态陈旧。 | Cloud PostgreSQL 是 Source of Truth；SQLite 限定本地状态用途；`event_id` / `idempotency_key` 去重；持久 ACK 后标记已同步；断线续传。 | 服务端已提交但 ACK 丢失、重复 ACK、断网重连、缓存重建和本地磁盘故障测试；禁止双向全库同步。 |
| 自动更新 / 回滚 | 🟡 Needs validation | 签名、Schema、健康误判或回滚不兼容导致客户端不可用 / 数据损坏。 | 签名、Checksum、Compatibility Manifest、Migration、runtime health；健康门失败进入 Safe Mode；回滚须检查兼容性。 | 下载中断、迁移失败、坏包、health gate 失败、回滚兼容矩阵和 Windows 更新回归。 |
| Windows 打包 | 🟡 Needs validation | 原生依赖、进程监督、安装权限和更新行为与 macOS / Linux 不同。 | PyInstaller + NSIS；Windows build / installer smoke / install / upgrade / rollback 必须有 Windows runner / 实机证据。 | Windows runner 构建和安装器 smoke；升级与回滚在目标 Windows 版本实机验证；Mac CI 不可替代。 |
| 第三方依赖许可证 / 供应链 | 🟡 Needs validation | 许可证义务、恶意包或未锁定依赖可能阻断发布或引入供应链风险。 | 固定依赖并提交 Lockfile；记录版本、License、来源和修改状态。 | CI 许可证 / 漏洞扫描、例外审批与 SBOM；每次 Release 检查未批准项。 |
| 模型 fallback | 🔴 Not recommended | 无 Policy 的 fallback 可能把 LOCAL_ONLY / 敏感数据发送到远端，绕开预算或健康策略。 | ShuLiu Policy / capability 层包住 LiteLLM；fallback 每次重新过 Policy；Skill 不能写 Provider；LOCAL_ONLY 不得远端 fallback。 | Fake Provider 故障注入验证 provider health、隐私、预算、fallback 顺序；真实 Provider 配置前审查。 |
| Knowledge ACL | 🟡 Needs validation | 向量排名、缓存或删除传播错误可能泄漏用户无权访问的内容。 | PostgreSQL + pgvector；先 ACL / organization / scope / classification 过滤再向量检索；权限撤销和删除必须使索引 / 缓存失效。 | 数据库层查询计划 / 权限谓词、越权搜索、并发撤权、缓存失效和删除传播测试。 |
| Identity / Organization 边界 | 🟡 Needs validation | 错误成员关系或权限判断可能跨组织访问数据。 | V0.1 单公司；保留 `organization_id`；User + Organization Membership + Role + Permission；Provider 抽象可替换。 | 服务端授权、跨组织拒绝、membership 撤销、长期凭据安全存储测试；真实 Provider 在生产 Cloud 前确定。 |
| Policy / Approval 绕过 | 🟡 Needs validation | Agent、Skill 或 Tool 直接执行高风险动作，或动作参数在审批后变化。 | DENY 优先、默认 Fail Closed；HIGH 默认 Approval、CRITICAL 强制 Approval；Agent 不得自批；审批绑定动作上下文。 | 每类风险、拒绝优先级、无匹配规则、审批过期 / 参数变化和 Executor 未触发的集成测试。 |
| Evidence retention / 隐私 | 🟡 Needs validation | Evidence 可能携带截图、DOM、账号或其他敏感数据，造成过度留存和未授权访问。 | Retention 是可配置策略而非协议固定天数；按风险采集、脱敏、访问控制；Audit 不得被普通清理静默删除。 | Release 前定义 retention / capacity / export / deletion 配置，验证权限、过期清理、清理审计和脱敏。 |

## 使用规则

状态提升依赖实现证据，不能因为 Architecture Spec 或 Implementation Plan 写明控制措施就标为已验证。对任何不满足安全不变量的 OS / Provider / Worker 环境采用 Fail Closed：暂停能力、拒绝激活或阻止 Release。
