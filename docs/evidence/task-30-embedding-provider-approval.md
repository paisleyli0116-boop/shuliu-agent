# Task 30 Embedding Provider 选型与审批材料

**状态：用户已选定阿里云百炼 `text-embedding-v3` 用于接入准备；业务/数据、安全/法务与预算批准、区域配置和凭据仍待完成。**  
**核对日期：2026-10-25。**  
**本材料未创建账号、未保存凭据、未发送 Provider 请求，也不构成法务、安全或预算批准。**

## 1. Task 30 所需能力

Task 30 要求真实 Embedding Provider 经 Model Gateway、Policy、预算和 SecretRef 完成 Knowledge 入库与查询，并把响应向量写入 pgvector；真实测试需要两次外部调用（入库一次、查询一次）。DeepSeek 当前配置只声明 `fast_chat`，没有可用的 embedding profile；其聊天连接审批不延伸到其他 Provider 或 Embedding 数据处理。

当前代码的 `EmbeddingProfile` 已支持 provider、model、dimensions、privacy levels 和 index version。当前锁定 `litellm-core==1.106.0`。本轮已为 DashScope `text-embedding-v3` 增加经过 Gateway 的受限 `api_base` 与维度传递、配置校验、响应维度核验和无重试路由，且已通过本地定向测试；这只是离线集成证据，不代表 Provider 实际可用或已获准发送数据。SiliconFlow 的兼容 endpoint 仍未接线。不得从 Renderer 或知识库调用方绕开 Gateway 直连。

用户已选择 DashScope `text-embedding-v3` 作为接入准备目标；该选择不是组织、数据、安全、法务或预算批准。当前官方文档建议新建纯文本搜索/RAG 索引使用 `text-embedding-v4`，并将 `text-embedding-v3` 定位于兼容既有 v3 向量索引的迁移场景（[官方型号选择说明](https://help.aliyun.com/en/model-studio/embedding-rerank-model/)）。除非项目确实要兼容现存 v3 向量，负责人应在创建索引前复核是否保留 v3。当前先按用户选择完成 v3 的安全路由接线，未更改现有索引或默认模型。

当前知识库向量列为维度无关的 pgvector 类型，维度由 profile 标记和校验；选择不同维度不会自动重建已有索引。首个正式 profile 与索引版本必须固定，并用真实返回维度验证后才可启用。

## 2. 候选比较

| 候选 | 官方 API / 模型 | 维度与输入上限 | 可核实的价格 | 官方公开的数据处理要点 | 当前接入状态 |
|---|---|---|---|---|---|
| 阿里云百炼（DashScope） | `text-embedding-v3`；官方提供 OpenAI-compatible Embeddings endpoint | 默认 1024 维，可选 768/512/256/128/64；单条最多 8,192 tokens；支持中文等 50+ 语言 | 北京：文本输入 CNY 0.5 / 1M tokens；新加坡：CNY 0.514 / 1M tokens；页面注明不含限时优惠，账户、区域和批量计费需下单前复核 | 官方称不将数据用于模型训练，同时明确 Model Studio 会存储模型与应用调用产生的数据；保存期限、删除窗口、区域及协议细节需负责人/法务继续审阅 | 限定 endpoint、维度和 LiteLLM 路由已离线接线；区域、账户、价格口径、合规与真实 Provider 响应仍待核实 |
| SiliconFlow | `BAAI/bge-m3`；`POST https://api.siliconflow.cn/v1/embeddings` | 官方接口文档给出每项输入上限 8,192 tokens；当前文档未在同一 API 页面固定此模型输出维度，必须从权威模型目录并以真实响应核对 | 本次查阅的静态 API 文档未给出该模型固定单价；需在官方模型广场核对所选账户和服务的实时价，不能填猜测价格 | 官方隐私政策称 API 输入/输出仅为当次推理短暂处理、不长期存储且不用于训练；同一政策也披露内容安全审核会自动分析输入和输出，并记录 IP、模型名、Token、时间及状态等元数据；个人信息存储位置与交互内容承诺不能相互混为一谈 | OpenAI 兼容格式有官方示例；Gateway 仍缺可审计的 API Base 与维度传递；模型许可、实时价格、账号/服务条款待复核 |
| OpenAI | `text-embedding-3-small`；`/v1/embeddings` | 默认 1536 维、支持请求指定维度；单条最多 8,192 tokens | USD 0.02 / 1M 输入 tokens（当前官方模型页；批量/合同价格另行核实） | API 数据默认不用于训练，除非明确选择共享；默认 abuse-monitoring logs 最长保留 30 天。Zero Data Retention / Modified Abuse Monitoring 需符合条件并获批准；不能假设当前账号已经具备 | LiteLLM 的默认 OpenAI 路由最接近现有实现；但仍需确认数据区域、组织控制、SecretRef 和实际价格路由 |

### 官方依据

- 阿里云：[text-embedding-v3 规格与区域价格](https://help.aliyun.com/en/model-studio/text-embedding-v3)、[Embedding 模型目录与规格](https://help.aliyun.com/en/model-studio/embedding)、[Embedding 同步 API](https://help.aliyun.com/en/model-studio/text-embedding-synchronous-api)、[隐私与安全说明](https://help.aliyun.com/en/model-studio/privacy-notice)、[Model Studio 服务条款](https://help.aliyun.com/en/model-studio/bailian-service-notes)。
- SiliconFlow：[Embedding API](https://api-docs.siliconflow.cn/docs/api/embeddings-post)、[隐私政策](https://docs.siliconflow.cn/docs/legals/privacy-policy)、[平台使用协议](https://docs.siliconflow.cn/docs/legals/terms-of-service)。
- OpenAI：[Embedding 模型指南](https://developers.openai.com/api/docs/guides/embeddings)、[text-embedding-3-small 模型与价格](https://developers.openai.com/api/docs/models/text-embedding-3-small)、[API 数据控制与保留](https://developers.openai.com/api/docs/guides/your-data)。
- LiteLLM：[Python SDK embedding 接口](https://docs.litellm.ai/docs/python_sdk)。项目依赖锁定版本由本仓库 `uv.lock` 记录；供应商兼容性的实际行为仍以 ShuLiu Gateway 定向测试为准。

## 3. 评估建议（不是批准）

首轮验收候选按用户选择为阿里云百炼 `text-embedding-v3`，默认维度固定为 1024。Gateway 已实现对区域主机和精确 `/compatible-mode/v1` 路径的 allowlist 校验。官方兼容 API 使用区域专属 workspace endpoint：北京 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`，新加坡 `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`；API Key 亦按区域区分。仍须由账户负责人提供区域和 workspace endpoint，密钥只通过本机 SecretRef 配置。启用任何组织文档前，必须先由负责人确认调用数据会被存储、所在区域、服务条款及适用的删除/保留规则。

若负责人优先考虑供应商对输入/输出的短暂处理承诺，可把 SiliconFlow `BAAI/bge-m3` 作为并列候选；但自动内容安全审核、平台元数据日志、实时价格、确切输出维度及模型许可需要在选定具体账户/模型后核实。OpenAI `text-embedding-3-small` 可作兼容性和成本基准，但其默认最长 30 天滥用监测内容保留需要负责人接受，或先取得符合资格的数据保留控制批准。

这些建议不代表任何一个 Provider 已通过合规审查。最终选择应由业务/数据负责人和安全/法务负责人确认；供应商页面的声明不代替适用于具体账户、区域和合同的法律审查。

## 4. 预算硬上限边界

当前持久预算适配器在调用前预留账户配置的固定金额，在响应后按实际费用结算。新增本地回归覆盖表明：

- 预留 USD 0.06、预算上限 USD 0.10，实际结算 USD 0.08 时，系统应如实记录 USD 0.08，并因剩余预算不足而拒绝下一次 USD 0.06 预留。
- 如果单次外部 Provider 实际扣费为 USD 0.12，系统必须如实记录 USD 0.12 并阻止后续调用；但这并不能阻止已经发出的这一次请求超过 USD 0.10。

所以当前实现能限制**新调用的预算预留**并在结算后阻止后续调用，但不能承诺单次调用的真实 Provider 账单永远不超过账户上限。要做单次硬上限，必须在发请求前有可信的模型单价、区域/计费模式、最大输入 Token 和批次数上限，按保守最高费用预留，并在超出预算时 fail closed。固定预留额本身不是最大账单保证；事后拒绝结算会丢失真实费用账本，不能作为修复。

## 5. 负责人需要批准的事项

以下项目均为待决定，不可由本材料自动勾选：

1. 复核已选定的供应商与精确模型、账户区域、workspace endpoint 和向量维度；确认新索引是否应采用官方推荐的 `text-embedding-v4`，或确实需要 `text-embedding-v3` 兼容性。
2. 确认允许发送的数据级别。建议首轮只使用公开固定 fixture，生产/组织知识内容保持禁用，直到数据负责人明确批准。
3. 审阅供应商条款、存储与删除窗口、处理区域、子处理方、自动安全审核、模型/Provider 许可及适用法律；指定负责审批的人员和日期。
4. 批准测试费用上限、预留策略以及对单次不可预测费用超限的处理方式。不得把 CI 预算设定描述成 Provider 的报价或硬限额。
5. 在上述决定完成后，再单独授权真实验收。当前 Task 30 gated test 固定使用同一公开文本发两次请求（入库一次、查询一次）；真实调用每次均须明确授权，不可从 Task 28 的 DeepSeek 批准推定。

## 6. 离线接入实现与验证（2026-10-25）

- 已对 Gateway 做最小扩展：严格的 Provider profile 解析现在只允许 DashScope `text-embedding-v3` embedding profile 使用北京/新加坡 workspace OpenAI-compatible URL，并只允许官方维度集合；任意 host、scheme、path、端口、userinfo、query 及不兼容 Provider/model/capability 仍拒绝。endpoint 字段在 profile/config 的 repr 中隐藏。
- Knowledge embedding adapter 将 profile 的向量维度传进 Model Gateway 请求；Gateway 校验维度并在预算预留前拒绝不匹配；LiteLLM adapter 对 DashScope 使用 `openai/text-embedding-v3`，传入受 allowlist 保护的 `api_base`、维度和 `num_retries=0`。其他 Provider 的 LiteLLM 参数保持原行为。输出向量维度与配置不符时按已收到 Provider 响应的结构化错误处理。
- RED：最初配置/路由用例因 profile 不接受受限 endpoint/dimensions、ModelProviderConfig 无此字段而失败（3 failed，66 passed）。为保护现有 DashScope chat 配置增加回归后，初始实现错误拒绝该 profile（1 failed，43 passed）；规则收窄到 embedding profile 后该用例通过。随后新增的 Gateway 用例发现漏传维度时会继续进入凭据解析（实际错误变成 `MODEL_CREDENTIAL_UNAVAILABLE`），而不是预算前置拒绝；该测试先失败，再由 Gateway 预检补丁修复。
- GREEN：独立审查后的最终 Python Worker `tests/models` **139 passed**；Cloud API Knowledge 全目录 **12 passed, 2 skipped**；直接相关的 Embedding Gateway 集成测试此前 **5 passed**。2 项跳过需要 PostgreSQL，尚未通过。
- 独立审查曾发现 endpoint 正则的全局忽略大小写也接受了错误路径大小写。新增两个错误路径用例先得到 **2 failed, 6 passed**；将忽略大小写限定到 scheme 和主机名后，相关 allowlist 用例 **9 passed**。API 路径现要求精确匹配 `/compatible-mode/v1`。
- 锁定依赖确认：`uv.lock` 为 `litellm-core==1.106.0`。离线路由解析返回 LiteLLM provider `openai` 和模型 `text-embedding-v3`，且 SDK signature 接受 `api_base`、`dimensions`。这验证的是路由解析与本地契约，不是 HTTP 请求。
- 同版本 LiteLLM `model_cost` 中没有 `text-embedding-v3` 或 `openai/text-embedding-v3` 的计价项。当前 PostgreSQL 持久预算配置只允许 `USD`，而阿里官方当前文档按地区列价为 CNY；成功 ModelCall 若 cost 为 null 会被拒绝结算。因此在获得受批准的区域、结算币种/转换口径和可审计价格策略之前，不进行真实调用，不把估算写入成本账本。
- 当前未配置 workspace ID/endpoint、SecretRef，也没有调用 Provider；未验证组织数据合规审批。不能把上述本地 Fake/route tests 标记成真实响应证据。

## 7. 当前判定

- Provider selection: **用户已选择阿里云百炼 `text-embedding-v3` 用于技术准备；不等同于合规、预算或真实调用批准。**
- Data processing / privacy / license / security review: **PENDING**。
- Provider 区域、workspace endpoint、SecretRef 和生产预算配置：**NOT CONFIGURED**。
- 真实 embedding 调用及 pgvector / ModelCall / 预算闭环：**NOT RUN**。
- Task 30：**BLOCKED**；Task 31：**NOT STARTED**。
