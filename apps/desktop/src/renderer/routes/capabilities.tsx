import { useEffect, useState } from "react";
import type { RendererApi } from "../../preload/api";
import { Button, Card, DataNotice, EmptyState, StatusIndicator } from "../components/design-system";
import { ChatRoute } from "./chat";

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

export function CapabilityRoute({ page, api, runtimeReady = false, onOpenModels }: {
  page: CapabilityPage;
  api: RendererApi;
  runtimeReady?: boolean;
  onOpenModels?: () => void;
}) {
  if (page === "chat") {
    return <ChatRoute api={api} runtimeReady={runtimeReady} onOpenModels={onOpenModels} />;
  }
  const capability = capabilities[page];

  return (
    <div className="page-stack">
      <Card className="capability-card">
        <div className="capability-card__header">
          <div><span className="eyebrow">CAPABILITY STATUS</span><h2>{capability.title}</h2><p>{capability.summary}</p></div>
          <StatusIndicator tone="warning" label="尚未接入" />
        </div>
        <DataNotice tone="warning" title="当前限制">{capability.reason}</DataNotice>
        {page === "agents" && (
          <EmptyState
            title="没有已接入的 Agent 目录"
            description="当前 Foundation 不会用静态岗位卡片伪装 Agent 已配置或在线。"
            icon="◇"
          />
        )}
      </Card>

      {page === "tools" && (
        <ComputerRuntimeStatus api={api} runtimeReady={runtimeReady} />
      )}

      <Card className="contract-card">
        <span className="eyebrow">REQUIRED DESKTOP CONTRACT</span>
        <h3>开放前需要的真实接口</h3>
        <ul>{capability.contract.map((item) => <li key={item}>{item}</li>)}</ul>
        <p className="muted">接口未接通前，本页面只呈现交付状态，不执行、保存或模拟任何业务操作。</p>
      </Card>
    </div>
  );
}

function ComputerRuntimeStatus({ api, runtimeReady }: { api: RendererApi; runtimeReady: boolean }) {
  const [result, setResult] = useState<Awaited<ReturnType<RendererApi["computer"]["capability"]>> | null>(null);
  const [observation, setObservation] = useState<Awaited<ReturnType<RendererApi["computer"]["observeForeground"]>> | null>(null);
  const [observing, setObserving] = useState(false);

  useEffect(() => {
    if (!runtimeReady) return;
    let active = true;
    void api.computer.capability().then(
      (value) => { if (active) setResult(value); },
      () => {
        if (active) setResult({
          status: "unknown",
          safe_error_code: "COMPUTER_RUNTIME_UNAVAILABLE",
          trace_id: "",
        });
      },
    );
    return () => { active = false; };
  }, [api, runtimeReady]);

  async function observeForeground(): Promise<void> {
    if (!runtimeReady || observing) return;
    setObserving(true);
    setObservation(null);
    try {
      setObservation(await api.computer.observeForeground());
    } catch {
      setObservation({
        status: "blocked",
        title: null,
        elements: [],
        safe_error_code: "COMPUTER_RUNTIME_UNAVAILABLE",
        trace_id: "",
      });
    } finally {
      setObserving(false);
    }
  }

  const tone = result?.status === "available" ? "success" : result?.status === "unknown" ? "warning" : "neutral";
  const label = !runtimeReady
    ? "Sidecar 尚未就绪"
    : result?.status === "available"
      ? "桌面环境可用"
      : result?.status === "unavailable"
        ? "当前桌面不可用"
        : result
          ? "状态未确认"
          : "正在检测桌面环境";

  return (
    <Card className="capability-card">
      <div className="capability-card__header">
        <div>
          <span className="eyebrow">WINDOWS COMPUTER RUNTIME</span>
          <h2>桌面自动化（Windows）</h2>
          <p>显示 Sidecar 对当前交互桌面的真实检测结果。</p>
        </div>
        <StatusIndicator tone={tone} label={label} />
      </div>
      <DataNotice tone="warning" title="当前能力边界">
        {result?.status === "available"
          ? "Computer Runtime 已接入 Sidecar，但 AI 对话尚未接入桌面操作闭环；本页不会点击、输入或滚动，也不会自动操作电脑。"
          : "当前没有确认可用的交互桌面；本页不会点击、输入或滚动，也不会自动操作电脑。"}
        {result?.safe_error_code ? ` 安全状态码：${result.safe_error_code}。` : ""}
      </DataNotice>
      <div className="computer-observation">
        <Button onClick={() => void observeForeground()} disabled={!runtimeReady || observing || result?.status !== "available"}>
          {observing ? "正在读取当前窗口…" : "观察当前前台窗口"}
        </Button>
        <p className="muted">仅在你点击后读取当前前台窗口；内容只在本机显示，不会自动发送给 AI。</p>
        {observation?.status === "blocked" && (
          <div className="inline-error" role="alert">观察未完成：{observation.safe_error_code ?? "COMPUTER_RUNTIME_UNAVAILABLE"}</div>
        )}
        {observation?.status === "observed" && (
          <section aria-label="窗口观察结果">
            <h3>当前窗口：{observation.title}</h3>
            {observation.elements.length === 0 ? <p>没有读取到可访问的界面控件。</p> : (
              <ul>{observation.elements.map((element, index) => (
                <li key={`${element.role}-${index}`}>
                  <strong>{element.role}</strong> · {element.name}{element.sensitive ? " · 已隐藏" : ""}
                </li>
              ))}</ul>
            )}
          </section>
        )}
      </div>
    </Card>
  );
}
