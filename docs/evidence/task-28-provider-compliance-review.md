# Task 28 Provider 接入合规审批材料

**状态：待负责人批准（PENDING_OWNER_APPROVAL）。本材料仅为审批摘要，不构成审查通过。**
**审批对象：DeepSeek（deepseek/deepseek-flash）作为外部模型 Provider 的接入。**

材料基于当前分支代码与已验证的运行时行为整理；代码引用为事实来源，未验证事项明确标注。

---

## 一、数据发送范围（已验证，代码为证）

| 项 | 事实 | 代码依据 |
|---|---|---|
| 连接测试（canary）发送内容 | 固定字符串 `"Reply with exactly OK."`，不含任何用户数据、文件或系统信息 | `runtimes/python-worker/shuliu_runtime/models/connection.py`（`_CANARY_INPUT`） |
| 请求参数 | `model="deepseek-flash"`、`max_tokens=8`、`num_retries=0`、单次调用、无 fallback | `litellm_adapter.py`（L82/L86-87） |
| 目标端点 | `https://api.deepseek.com`（TLS 加密，适配器强制覆盖，修正 LiteLLM 默认 `/beta` 路径） | `litellm_adapter.py`（`_DEEPSEEK_API_BASE`） |
| 认证方式 | `Authorization: Bearer <key>`，仅存在于该请求头 | 同上 |
| 常规对话（非 canary） | 用户输入经 Gateway → Policy → 预算层后原样发送给 Provider | `gateway.py`（complete_for_provider 链路） |

**需负责人知情**：连接测试仅发送上述固定文本；但一旦批准接入，常规对话功能将把用户输入内容发送至 DeepSeek，其数据处理适用 DeepSeek 的服务条款与隐私政策（见第四节待验证项）。

## 二、凭据保护（已验证）

- **存储**：Windows 凭据管理器（`keyring` 的 `WinVaultKeyring`，经白名单校验的本地后端），服务名 `shuliu-agent`，条目 `providers/deepseek/credential`。凭据不落应用数据目录、不入库、不入日志。依据：`shuliu_runtime/security/os_secret_store.py`。
- **使用**：仅在请求时从系统存储读取，传递给 LiteLLM 调用参数；不缓存、不转发、不写入任何诊断记录。
- **日志安全**：诊断日志只记录 `trace_id`、安全错误码、请求状态、失败阶段、HTTP 状态码与耗时；异常文本被刻意隐藏；litellm 自身的凭据脱敏（secret redaction）保持启用（`LITELLM_DISABLE_REDACT_SECRETS` 未设置）。相关遮蔽行为有测试覆盖。
- **残留风险（需负责人接受）**：Windows 凭据管理器为本机保护，本机管理员权限或恶意软件理论上可读取；这是系统级约束而非应用可消除的风险。

## 三、数据保留（已验证）

- 本地诊断日志（`runtime-diagnostics.jsonl`、`desktop-startup.jsonl`）仅含元数据（时间戳、Trace ID、安全错误码等），**不含**请求/响应内容、不含凭据。
- Task 28 链路不持久化模型响应正文、token 用量或费用记录。
- 成功 canary 的 Trace ID `cf526ffb-1e41-4332-bf34-c16e9946865e`（2026-10-25T02:34:33Z）留存在上述本地诊断日志中。

## 四、第三方服务条款（部分待验证）

| 第三方 | 角色 | 状态 |
|---|---|---|
| DeepSeek API（api.deepseek.com） | 模型推理服务 | **待验证**：其服务条款、隐私政策、数据保留与是否用于训练，需负责人或法务确认后方可视为已审查 |
| LiteLLM（litellm-core，本地 SDK） | 请求构造与传输 | 本地运行，MIT 类开源；已验证其不在本地存储凭据 |
| tiktoken + 编码文件 | 本地 token 计数 | 完全离线（编码文件已随安装包内置，无运行时下载） |
| Windows 凭据管理器 | 凭据存储 | 操作系统组件 |

## 五、安全控制（已验证，均有测试）

1. **一次性 canary 门禁**：真实连接测试必须由启动时显式注入的授权开启；每次启动仅一次、锁内消费、并发二次请求被拒（`MODEL_CONNECTION_TEST_DISABLED` / `MODEL_BUDGET_EXHAUSTED`）。
2. **请求次数上限**：一次点击最多一个 HTTP 请求——Electron 侧同步消费授权、Sidecar 锁内消费、`num_retries=0`、fallback 关闭、无重试/重放代码；有并发与重放定向测试。
3. **预算与策略链**：Gateway → Policy → 预算 → SecretResolver 顺序未改动。
4. **协议通道隔离**：Sidecar 的 NDJSON 协议流与 stdout 物理隔离，第三方库输出无法污染（`server.py::_isolate_protocol_output`，有测试）。
5. **失败即关闭**：任何异常均转为安全错误码，不泄漏异常文本与凭据。

## 六、已知风险

| 风险 | 等级 | 说明 |
|---|---|---|
| DeepSeek 侧数据处理政策未审查 | 高 | 服务条款/隐私政策/训练用途未确认，本材料不能替代该审查 |
| 本机凭据可被管理员/恶意软件读取 | 中 | 系统级约束，见第二节 |
| Provider 可用性 | 低 | 已观察到偶发 HTTP 500（DeepSeek 服务器侧）；`num_retries=0` 下直接失败，不会重试扣费 |
| 常规使用费用 | 中 | 连接测试有进程级 probe 上限；常规对话依赖预算层配置，上限值需运营侧确定 |

## 七、需要负责人决定的事项

1. 是否批准 DeepSeek 作为外部模型 Provider（含其服务条款与数据处理安排）。
2. 是否接受常规对话将把用户输入发送至 DeepSeek（连接测试仅发送固定文本，不涉及此问题）。
3. 是否接受凭据存储于本机 Windows 凭据管理器。
4. 是否批准当前诊断日志的保留策略（仅本地、仅元数据、无自动上报）。

**批准方式**：请负责人对以上四项逐项明确批准或提出条件；口头/书面确认后由记录者在本文件标注批准人与日期。材料准备完成不等于审批通过。

## 八、已验证事实 / 未验证事项清单

**已验证**（代码 + 本地/实测证据）：数据发送范围、凭据存储与日志安全、请求次数上限、协议通道隔离、真实连接成功（Trace `cf526ffb-1e41-4332-bf34-c16e9946865e`，用户界面截图 + 运行时日志双重佐证）。

**未验证**：DeepSeek 服务条款与隐私政策细节；成功响应的正文/用量/费用（运行时设计不记录，截图亦未包含）；常规对话在真实负载下的策略与预算行为。

**待批准**：第七节四项。
