# 生产组合根与 Worker 服务部署配置

本文说明 Foundation V0.1 中，可信 Workflow→ToolBroker 组合根与 Worker 服务如何由部署配置启用。它描述代码已实现的行为，不改变任何 Architecture Spec 结论。

## 1. 可信边界

`RuntimeIdentityProvider` 是抽象接口（ADR-018 要求身份 Provider 经抽象接口接入）。当前唯一实现是 `EnvironmentIdentityProvider`：它读取 Desktop Main 进程在启动 Sidecar 时写入的子进程环境。

- Renderer 无法设置父进程环境，因此不能自报身份。
- Sidecar 与 Worker 都**不存在**默认身份。缺少任一变量即视为未配置，Workflow / Tool 执行保持关闭，health 报 `not_configured` 并给出具体 `error_code`。
- `SHULIU_ORGANIZATION_ID` 在 `models/connection.py` 中另有 `"local"` 默认值，那是 Provider 连接上下文，**不用于授权**，两者不可混用。

具体身份 Provider（如 OIDC / Cloud 下发令牌）仍属上线前配置项，尚未确定；本实现不假定其形态。

## 2. Sidecar（Desktop 内置运行时）

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `SHULIU_ACTOR_ID` | 是 | 已认证主体 ID |
| `SHULIU_ORGANIZATION_ID` | 是 | 组织范围；与 actor 绑定 |
| `SHULIU_ACTOR_PERMISSIONS` | 是 | 逗号分隔的权限代码，空值不等于"全部权限" |
| `SHULIU_CONTEXT_PRIVACY` | 是 | 数据分级标记（ToolBroker 强制要求） |
| `SHULIU_POLICY_BUNDLE` | 是 | Policy 规则 JSON 文件路径 |
| `SHULIU_WORKFLOW_CATALOG` | 是 | 已发布 Workflow 定义目录 |
| `SHULIU_APPROVAL_TTL_SECONDS` | 否 | 审批有效期，默认 900，取值范围 1–86400 |

以上均需加入 `apps/desktop/src/main/runtime/supervisor.ts` 的 `SIDECAR_ENV_KEYS` 白名单，否则会被过滤（已完成）。

装配顺序：身份 → 审批窗口 → Policy bundle → Workflow 目录。任一步失败都不装配，且 `health.components.workflow` 返回 `not_configured` 与对应错误码：

- `RUNTIME_IDENTITY_UNAVAILABLE` / `RUNTIME_IDENTITY_INVALID`
- `APPROVAL_TTL_INVALID`
- `POLICY_BUNDLE_UNAVAILABLE` / `POLICY_BUNDLE_INVALID`
- `WORKFLOW_CATALOG_UNAVAILABLE` / `WORKFLOW_CATALOG_INVALID`
- `WORKFLOW_COMPOSITION_FAILED`

### Policy bundle 格式

未知字段会**拒绝加载**而不是忽略，避免拼写错误造成"看起来已配置但规则没生效"。

```json
{
  "policy_version": "foundation-v0.1",
  "rules": [
    {
      "rule_id": "allow-task-create",
      "action": "foundation.task.create",
      "risk": "LOW",
      "decision": "ALLOW",
      "permission": "foundation.task.create"
    }
  ]
}
```

`rules` 为空是合法配置，效果是拒绝一切动作（fail-closed）。

### Workflow 目录格式

目录内每个 `*.json` 是一个定义，按 `workflow_id` + `workflow_version_id` 精确索引；重复版本、非法定义、空目录都会使加载失败。请求不会命中未发布的版本。

```json
{
  "workflow_id": "foundation.noop",
  "workflow_version_id": "1.0.0",
  "input_schema": {"type": "object"},
  "steps": [
    {"id": "execute", "type": "tool", "tool_id": "core.noop", "arguments": {}}
  ]
}
```

### 工具注册

`compose_tool_registries` 合并各 Tool provider 的注册表，同名工具视为冲突并拒绝。没有部署任何 provider 时注册表为空，任何工具调用都会被拒绝——这是安全默认，不是"配置成功"。

## 3. Worker 服务

Worker 以独立进程连接 Cloud，通过 `python -m shuliu_runtime.worker.service` 启动（`package = false`，无 console script）。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `SHULIU_CLOUD_API_URL` | 是 | 必须 `https://` |
| `SHULIU_CLOUD_WSS_URL` | 否 | 省略时按 Cloud 固定路由推导为 `wss://<host>/v1/workers/ws` |
| `SHULIU_WORKER_VERSION` | 是 | 上报给 Cloud 的版本 |
| `SHULIU_WORKER_CAPABILITIES` | 否 | 逗号分隔，重复即拒绝 |
| `SHULIU_WORKER_TOOL_PERMISSIONS` | 否 | 部署授予的工具权限；默认空表示不能执行任何工具 |
| `SHULIU_WORKER_REGISTRATION_TOKEN` | 首次 | 一次性注册令牌，注册成功后应移除 |
| `SHULIU_WORKER_IDLE_POLL_SECONDS` | 否 | 空转轮询间隔，默认 1.0，范围 0.01–60 |
| `SHULIU_POLICY_BUNDLE` | 是 | Worker 侧同样需要 Policy 决策 |
| `SHULIU_DATA_DIR` | 否 | 状态与数据库目录 |

注册状态保存在 `<data_dir>/worker-registration.json`，只含 `worker_id` 与 `device_key_ref`；会话凭据仅驻留内存，不落盘。后续启动走设备密钥挑战应答续期，不再使用注册令牌。

Worker 侧的身份来自**已认证 WebSocket 会话**：actor 只允许 `subject_id` 与 `organization_id`，任务载荷自带 `permissions`、`actor`、`organization_id` 一律拒绝。权限由 `SHULIU_WORKER_TOOL_PERMISSIONS` 提供。

## 4. 仍未完成的部分

- Sidecar 与 Worker 都还没有部署任何真实 Tool provider（Browser / Computer / MCP 需按各自任务接入）。
- 具体身份 Provider、凭据 TTL / 轮换、Worker 设备密钥后端仍是上线前配置项。
- 生产 Worker 服务的进程守护、日志采集与滚动升级不属于本仓库代码范围。
