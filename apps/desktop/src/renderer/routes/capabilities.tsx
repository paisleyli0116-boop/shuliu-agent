import { useEffect, useState } from "react";
import type { ProviderCatalog, ProviderOption, RendererApi } from "../../preload/api";
import { Card, DataNotice, EmptyState, SelectField, StatusIndicator } from "../components/design-system";

export type CapabilityPage = "chat" | "agents" | "tasks" | "workflows" | "knowledge" | "tools" | "runs";

const capabilities: Record<CapabilityPage, {
  title: string;
  summary: string;
  reason: string;
  contract: string[];
}> = {
  chat: {
    title: "AI 对话",
    summary: "此版本尚无从会话到 Model Gateway 的桌面调用链。",
    reason: "Preload 目前只暴露 Provider 连接 canary，不暴露会话列表、消息发送、流式响应或取消接口。当前不能创建真实对话，也不能把会话模型选择传入 Model Gateway。",
    contract: ["会话创建、列表与历史读取", "带会话 model_id 和 Knowledge 选择的受控发送请求", "流式消息、Tool / Approval 状态事件与取消", "经 Gateway、Policy、预算和 Audit 的执行记录"],
  },
  agents: {
    title: "岗位 Agent",
    summary: "Foundation 尚未提供可配置 Agent 目录的 Desktop API。",
    reason: "当前版本不含岗位 Agent 定义、模型默认值、Knowledge 绑定或工具权限的 UI 数据源。产品定位中的采购、仓储等角色仍属于 Foundation 之外的后续业务层。",
    contract: ["Agent Definition 列表与版本读取", "Agent 默认模型和已绑定能力的受控配置", "运行状态、最近任务和关联记录查询", "每次执行仍由 Foundation Policy 与 Approval 决策"],
  },
  tasks: {
    title: "任务中心",
    summary: "当前没有接入真实任务持久化和 Agent 执行服务。",
    reason: "桌面任务 API 目前只操作进程内演示队列。为避免把演示条目误认为真实执行任务，此页面不展示示例记录，也不提供创建或审批按钮。",
    contract: ["持久化任务列表、详情和真实创建 API", "由 AgentRun 驱动的状态、进度、结果与安全错误摘要", "有授权约束的取消或恢复操作", "从 Task 到 Trace、PolicyDecision 和 Audit 的查询链路"],
  },
  workflows: {
    title: "工作流",
    summary: "Python 有通用 Workflow Engine；Desktop 尚未暴露管理与运行接口。",
    reason: "当前 Renderer IPC 没有工作流定义、启动、暂停、恢复或运行记录查询入口。",
    contract: ["工作流定义和不可变版本列表", "经授权创建与取消 WorkflowRun", "Checkpoint、审批等待和恢复状态查询", "带 Trace 的步骤事件与错误摘要"],
  },
  knowledge: {
    title: "知识库",
    summary: "服务端 Knowledge 能力正在建设；Desktop 当前没有可用管理入口。",
    reason: "Renderer 没有通过受控身份和 ACL 的 Knowledge IPC。不能从页面直接访问 Cloud API，也不能显示未真实索引的文件或检索结果。",
    contract: ["按用户权限过滤的知识库和文档列表", "文件上传与真实解析 / 索引状态", "ACL 约束的检索测试及引用来源", "Agent 绑定与权限变更审计"],
  },
  tools: {
    title: "工具与 Skills",
    summary: "Foundation 有 Tool / Skill 执行边界；Desktop 尚未暴露目录和权限管理 API。",
    reason: "当前没有真实可查询的安装项、版本、权限声明或 OS 沙箱状态入口，因此此页不会展示示例 Skill 卡片或伪造运行状态。",
    contract: ["已安装 Tool / Skill 的清单和版本", "权限声明、Policy 决策和隔离状态", "启停 / 生命周期操作及其审批要求", "执行 Trace、Evidence 与失败原因"],
  },
  runs: {
    title: "审批与运行记录",
    summary: "审批页当前数据来自进程内演示服务，Audit / AgentRun 查询接口尚未接入。",
    reason: "桌面审批 API 目前只操作进程内演示状态。为避免混淆，页面不展示演示审批，也不提供批准或拒绝按钮；正式执行证据必须来自持久化 Task、AgentRun、PolicyDecision 和 Audit 查询。",
    contract: ["按权限查询真实待审批请求并提交决定", "按任务 / AgentRun / Trace 查询执行历史", "读取安全摘要、Policy 决策与结果验证状态", "所有决定保留审计证据并保持当前审批门禁"],
  },
};

export function CapabilityRoute({ page, api }: { page: CapabilityPage; api: RendererApi }) {
  const capability = capabilities[page];
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [catalogFailed, setCatalogFailed] = useState(false);

  useEffect(() => {
    if (page !== "chat") return;
    let active = true;
    void api.providers.list().then((value) => {
      if (active) setCatalog(value);
    }).catch(() => {
      if (active) setCatalogFailed(true);
    });
    return () => { active = false; };
  }, [api, page]);

  const configuredModels = catalog?.providers.flatMap((provider: ProviderOption) =>
    provider.models.map((model) => ({
      value: `${provider.id}:${model.id}`,
      label: `${provider.name} · ${model.name}${provider.configured ? " · 凭据已配置" : " · 未配置凭据"}`,
    })),
  ) ?? [];

  return (
    <div className="page-stack">
      <Card className="capability-card">
        <div className="capability-card__header">
          <div><span className="eyebrow">CAPABILITY STATUS</span><h2>{capability.title}</h2><p>{capability.summary}</p></div>
          <StatusIndicator tone="warning" label="尚未接入" />
        </div>
        <DataNotice tone="warning" title="当前限制">{capability.reason}</DataNotice>
        {page === "chat" && (
          <div className="chat-unavailable-preview">
            <div className="chat-unavailable-preview__toolbar">
              <SelectField
                label="会话模型"
                aria-label="发送消息的模型"
                disabled
                value=""
                hint="当前只读取实际 Sidecar Profile；由于对话发送接口未提供，模型不能切换并执行。"
              >
                <option value="">{catalogFailed ? "模型目录读取失败" : catalog ? (configuredModels.length ? "请选择已加载模型" : "没有已加载模型") : "正在读取运行时模型…"}</option>
                {configuredModels.map((model) => <option key={model.value} value={model.value}>{model.label}</option>)}
              </SelectField>
            </div>
            {catalogFailed ? <div className="inline-error" role="alert">无法读取 Python Runtime 的模型目录；请到“模型与连接”检查 Runtime。</div> : null}
            <EmptyState title="尚无会话" description="真实会话存储和消息执行接口尚未开放；此处不会显示模拟对话。" icon="✦" />
            <label className="field chat-composer">
              <span>消息内容</span>
              <textarea aria-label="发送消息" disabled rows={3} placeholder="等待会话服务接口接入后开放" />
              <small>发送、停止、Markdown 消息和 Tool / Approval 状态尚未接入。</small>
            </label>
          </div>
        )}
        {page === "agents" && (
          <EmptyState
            title="没有已接入的 Agent 目录"
            description="当前 Foundation 不会用静态岗位卡片伪装 Agent 已配置或在线。"
            icon="◇"
          />
        )}
      </Card>

      <Card className="contract-card">
        <span className="eyebrow">REQUIRED DESKTOP CONTRACT</span>
        <h3>开放前需要的真实接口</h3>
        <ul>{capability.contract.map((item) => <li key={item}>{item}</li>)}</ul>
        <p className="muted">接口未接通前，本页面只呈现交付状态，不执行、保存或模拟任何业务操作。</p>
      </Card>
    </div>
  );
}
