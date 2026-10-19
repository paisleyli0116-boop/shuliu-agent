# Task 24 Browser Runtime

Browser Runtime 将 `browser-use` 的本地 actor API 封装为 `browser.*` Tool。它不创建 browser-use Agent、不调用模型、不接收 Provider Secret。网页内容和 DOM 标签始终标记为 `UNTRUSTED_PAGE_CONTENT`，不能成为权限或审批依据。

## 安装与验证

使用仓库声明的 Python 3.11、uv、Node 和 pnpm 版本。在仓库根目录安装两个独立锁定的 Python 环境：

```powershell
uv sync --locked --all-packages
uv sync --locked --project runtimes/browser-driver --python 3.11
$env:SHULIU_REQUIRE_REAL_BROWSER = '1'
uv run --locked --all-packages pytest runtimes/python-worker/tests/browser tests/integration/test_browser_tool_policy.py -q
```

必须安装真实 Chrome。测试自动查找 Windows、macOS、Linux 的常见 Chrome 路径，也可通过 `SHULIU_BROWSER_EXECUTABLE` 指定测试二进制。`SHULIU_REQUIRE_REAL_BROWSER=1` 会把缺少浏览器或 driver 环境作为失败，不会跳过。测试使用独立临时 profile 和本机 HTTP fixture，不登录真实账号、不提交外部业务。

driver 使用 `browser-use==0.13.11`。它的 websockets 依赖与现有 Worker 不兼容，因此保持独立 `pyproject.toml` / `uv.lock`，不降低 Worker 的依赖约束。Task 26 需要把 driver 环境纳入 Release Unit；本任务不交付安装包。

## 受控接口

可信 host 构造 `BrowserRuntime`，注入现有 Identity、Policy、Approval、幂等 store、Event sink、Evidence sink、专用 profile 根目录及允许的 origin。默认 backend 使用独立 driver；host 可以通过 `BrowserUseBackend(python_executable=..., executable_path=...)` 明确指定受信任安装路径。路径和 allowlist 不是网页或模型提供的参数。

- `call("launch" | "observe" | "close", ...)`：`browser.read`，LOW 风险。
- `act(action, policy_context, actor=..., idempotency_key=...)`：navigate / click / type / scroll，`browser.act`，CRITICAL 风险。通过同一个 Tool Broker 执行 Schema → Identity → Policy → Approval → Executor → Verify → Event/Audit 流程。
- click/type 使用当前 observation 的不透明 element reference；引用失效、账号或页面变化会拒绝执行，调用方应重新观察并重新审批。
- type 只支持可编辑的普通文本输入和 textarea，使用一次 `Input.insertText`；不使用自动清空重输、字符回退或动作重试。password、file、hidden、敏感元素不可操作。
- 结果使用共享 `browser-tool-v1.schema.json`，返回 `VERIFIED` / `UNVERIFIED` / `FAILED`、Trace 和 Evidence 引用；未知结果不可静默标记为成功。

Evidence sink 必须返回非空引用。副作用派发前先记录脱敏 BEFORE 证据，结束时记录 RESULT；Broker 继续产生既有 Event/Audit。会话按已认证用户与组织隔离，调用者不能伪造 Executor 的 principal。

## 当前安全边界与限制

**生产副作用执行默认关闭。** Task 33 durable Tool idempotency 尚未完成，未注入受验证执行门时 navigate/click/type/scroll 返回 `BROWSER_EXECUTION_GATE_CLOSED`。测试中的内存 store 和 loopback gate 仅验证本机 fixture，不代表生产可激活。模型/网页不能创建或更换执行门。

浏览器请求受 origin allowlist 限制；新 tab/worker target 暂停并关闭；下载禁用；未验证动作会隔离会话。取消、超时、driver 断连、Chrome 崩溃不会重发可能已生效的动作，Windows Job Object 和受控目录清理负责结束本次会话的资源。

进程/依赖隔离及 CDP 请求拦截**不是完整 OS sandbox 或 OS 网络隔离**。Task 36 未完成前，现有 executable Skill 继续 fail closed。Browser Runtime 没有新增 Renderer 原始浏览器 IPC，也不绕过 SkillCapabilityProxy 的已验证结果要求。

观察结果会删除输入值、敏感标记节点并脱敏常见凭据模式；这不能保证识别任意网页中的所有个人或敏感信息。接入真实站点前仍需 host 的数据权限与站点范围策略。文本 postcondition 只验证观察到的页面变化，不是支付、订单或消息送达的权威业务回执；这些能力不在本任务范围。

当前只支持单页受限动作，不提供任意 JS、任意 CSS、上传、下载、账号登录、弹窗操作或跨 session profile 复用。TTL 在访问时检查，host 退出时必须调用 `runtime.close()`。后续持久化恢复与应用生命周期整合按既有计划验收。

## 证据

执行记录见 [Task 24 ledger](evidence/task-24-progress.md)。Python 与 TypeScript FakeBrowser 共用结果 contract；只有 `test_real_browser.py` 标记的测试是真实 Chrome 集成证据。CI 对 Windows、Linux、macOS 强制执行真实浏览器测试并保存 JUnit artifact。当前本地验证不代替 CI、PostgreSQL 或安装包验收。
