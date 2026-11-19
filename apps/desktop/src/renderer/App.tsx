import { useEffect, useState } from "react";
import type { RendererApi, RuntimeHealth } from "../preload/api";
import { getRendererApi } from "./api/client";
import { Button, Card, DataNotice, DetailPanel, PageHeader, StatusIndicator, Typography } from "./components/design-system";
import { RuntimeHealth as RuntimeHealthCard } from "./components/runtime-health";
import { CapabilityRoute, type CapabilityPage } from "./routes/capabilities";
import { ProvidersRoute } from "./routes/settings/providers";
import { WorkbenchRoute, type WorkbenchDestination } from "./routes/workbench";

type Page = "workbench" | CapabilityPage | "tasks" | "models" | "settings";
type NavigationItem = { id: Page; label: string; icon: string };
const navigation: Array<{ section: string; items: NavigationItem[] }> = [
  { section: "工作台", items: [{ id: "workbench", label: "智能工作台", icon: "⌂" }] },
  { section: "AI 工作空间", items: [
    { id: "chat", label: "AI 对话", icon: "✦" },
    { id: "agents", label: "岗位 Agent", icon: "◇" },
    { id: "tasks", label: "任务中心", icon: "▤" },
    { id: "workflows", label: "工作流", icon: "⑂" },
    { id: "knowledge", label: "知识库", icon: "▧" },
  ] },
  { section: "能力配置", items: [
    { id: "models", label: "模型与连接", icon: "◉" },
    { id: "tools", label: "工具与 Skills", icon: "⌘" },
  ] },
  { section: "治理", items: [
    { id: "runs", label: "审批与运行记录", icon: "◷" },
    { id: "settings", label: "系统设置", icon: "⚙" },
  ] },
];

const pageTitles: Record<Page, { title: string; description: string }> = {
  workbench: { title: "智能工作台", description: "从真实运行状态进入当前可用的 Foundation 能力。" },
  chat: { title: "AI 对话", description: "使用已配置的模型进行受控文本对话；发送前先确认数据范围。" },
  agents: { title: "岗位 Agent", description: "当前 Foundation 不包含行业岗位 Agent 的产品实现。" },
  tasks: { title: "任务中心", description: "当前没有连接真实的持久化任务与 Agent 执行服务。" },
  workflows: { title: "工作流", description: "通用 Workflow Engine 已存在，Desktop 管理和执行接口尚未接入。" },
  knowledge: { title: "知识库", description: "Knowledge 服务与 Desktop 身份、ACL 入口尚未闭环。" },
  models: { title: "模型与连接", description: "查看 Python Runtime 当前 Provider Profile，并通过受控流程管理凭据。" },
  tools: { title: "工具与 Skills", description: "当前没有 Desktop Skill 目录、权限配置或运行证据查询接口。" },
  runs: { title: "审批与运行记录", description: "真实审批服务与持久化运行审计尚未接入桌面。" },
  settings: { title: "系统设置", description: "查看真实 Desktop 与 Python Sidecar 健康状态。" },
};

export function App({ api: providedApi }: { api?: RendererApi }) {
  const api = providedApi ?? getRendererApi();
  const [page, setPage] = useState<Page>("workbench");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [health, setHealth] = useState<RuntimeHealth | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const refreshHealth = async () => {
    setHealthLoading(true);
    try {
      setHealth(await api.runtime.health.get());
    } catch {
      setHealth({ status: "safe_mode", code: "RUNTIME_HEALTH_UNAVAILABLE" });
    } finally {
      setHealthLoading(false);
    }
  };

  useEffect(() => { void refreshHealth(); }, []);
  const safeMode = health?.status === "safe_mode" || health?.status === "unhealthy";
  const activePage = pageTitles[page];
  const runtimeTone = health?.status === "healthy" ? "success" : health?.status === "degraded" ? "warning" : health ? "danger" : "neutral";

  const navigate = (destination: WorkbenchDestination) => setPage(destination);

  return (
    <div className="desktop-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <span><strong>ShuLiu</strong><small>AGENT FOUNDATION</small></span>
        </div>
        <div className="workspace-chip"><span className="workspace-avatar">S</span><span><strong>Foundation Workspace</strong><small>V0.1 · 本地工作区</small></span><span className="chevron" aria-hidden="true">⌄</span></div>
        <nav aria-label="主导航" className="sidebar-nav">
          {navigation.map((group) => (
            <div className="nav-group" key={group.section}>
              <span className="nav-group__label">{group.section}</span>
              {group.items.map((item) => (
                <button key={item.id} type="button" aria-label={item.label} title={item.label} className={`nav-item ${page === item.id ? "nav-item--active" : ""}`} aria-current={page === item.id ? "page" : undefined} onClick={() => setPage(item.id)}>
                  <span className={`nav-item__icon ${item.id === "chat" || item.id === "models" ? "nav-item__icon--ai" : ""}`} aria-hidden="true">{item.icon}</span><span className="nav-item__label">{item.label}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="workspace-footnote"><span className="workspace-footnote__mark">SL</span><span><strong>Foundation V0.1</strong><small>安全边界受控运行</small></span></div>
        </div>
      </aside>

      <main className={`main-area${detailsOpen ? " main-area--details-open" : ""}`}>
        <header className="topbar">
          <div className="breadcrumb"><span>ShuLiu Agent</span><span className="breadcrumb__slash">/</span><strong>{activePage.title}</strong></div>
          <div className="topbar-actions">
            <span className="workspace-context">本地工作区</span>
            <Button variant="quiet" className="detail-toggle" aria-label={detailsOpen ? "隐藏页面详情" : "显示页面详情"} aria-expanded={detailsOpen} aria-controls="page-details" onClick={() => setDetailsOpen((open) => !open)}>
              <span aria-hidden="true">▤</span><span>页面详情</span>
            </Button>
            <StatusIndicator tone={runtimeTone} label={healthLoading ? "检查中" : health?.status === "healthy" ? "Sidecar 在线" : safeMode ? "Safe Mode" : "状态待检查"} />
          </div>
        </header>

        {safeMode && (
          <div className="safe-mode-banner" role="alert">
            <span className="safe-mode-icon" aria-hidden="true">!</span>
            <div><strong>Safe Mode 已启用</strong><span>Python Runtime 不可用或状态不安全。任务执行和审批决策已暂停；可继续查看状态与模型设置。</span></div>
            <Button variant="quiet" onClick={() => setPage("settings")}>查看状态</Button>
          </div>
        )}

        <section className="page-content">
          <PageHeader eyebrow="SHULIU FOUNDATION · V0.1" title={activePage.title} description={activePage.description} />

          {detailsOpen && (
            <DetailPanel title={activePage.title} onClose={() => setDetailsOpen(false)}>
              <section className="detail-section" aria-labelledby="detail-current-page">
                <Typography variant="label" as="h3" id="detail-current-page">当前页面</Typography>
                <Typography variant="body">{activePage.description}</Typography>
              </section>
              <section className="detail-section" aria-labelledby="detail-runtime-status">
                <Typography variant="label" as="h3" id="detail-runtime-status">运行状态</Typography>
                <StatusIndicator
                  tone={runtimeTone}
                  label={healthLoading ? "正在检查" : health?.status === "healthy" ? "Sidecar 运行正常" : safeMode ? "Safe Mode" : health?.status === "degraded" ? "Sidecar 状态降级" : "状态未知"}
                />
                {health?.code && <Typography variant="caption">状态码：<code>{health.code}</code></Typography>}
              </section>
              <DataNotice title="数据范围">这里只显示当前页面说明和 Desktop / Sidecar 健康状态；未接入的数据不会显示为真实任务、Agent 或审批。</DataNotice>
            </DetailPanel>
          )}
          {page === "workbench" && <WorkbenchRoute api={api} health={health} onNavigate={navigate} />}
          {page === "chat" && <CapabilityRoute page="chat" api={api} runtimeReady={!healthLoading && health?.status === "healthy"} onOpenModels={() => setPage("models")} />}
          {page === "agents" && <CapabilityRoute page="agents" api={api} />}
          {page === "tasks" && <CapabilityRoute page="tasks" api={api} />}
          {page === "workflows" && <CapabilityRoute page="workflows" api={api} />}
          {page === "knowledge" && <CapabilityRoute page="knowledge" api={api} />}
          {page === "models" && <ProvidersRoute api={api} />}
          {page === "tools" && <CapabilityRoute page="tools" api={api} runtimeReady={!healthLoading && health?.status === "healthy"} />}
          {page === "runs" && <CapabilityRoute page="runs" api={api} />}
          {page === "settings" && <RuntimeRoute health={health} loading={healthLoading} refresh={refreshHealth} />}

          <footer className="content-footer"><span>ShuLiu Agent Foundation</span><span>Protocol v1 <i /> 安全策略由 Runtime 执行</span></footer>
        </section>
      </main>
    </div>
  );
}

function RuntimeRoute({ health, loading, refresh }: {
  health: RuntimeHealth | null;
  loading: boolean;
  refresh(): Promise<void>;
}) {
  return (
    <div className="page-stack">
      <Card className="runtime-panel">
        <div className="panel-heading">
          <div><span className="eyebrow">RUNTIME HEALTH</span><h2>桌面运行环境</h2></div>
          <Button variant="secondary" onClick={() => void refresh()} disabled={loading}>{loading ? "检查中…" : "重新检查"}</Button>
        </div>
        <div className="runtime-primary">
          <RuntimeHealthCard health={health} />
          <div><strong>Electron ↔ Python Sidecar</strong><span>stdio + NDJSON · protocol v1</span><small>状态由真实 Python 子进程握手和 health 请求返回。</small></div>
        </div>
        {health?.status === "safe_mode" || health?.status === "unhealthy" ? <div className="runtime-error" role="alert"><strong>Safe Mode</strong><span>新任务已暂停。检查 Python 环境或协议版本后重新检查运行状态。</span><code>{health.code ?? "RUNTIME_UNHEALTHY"}</code></div> : health?.status === "healthy" ? <div className="runtime-ok"><StatusIndicator tone="success" label="健康检查通过" /><span>允许继续查看工作台。</span></div> : null}
      </Card>
      <Card className="runtime-boundary"><span className="eyebrow">SETTINGS STATUS</span><h3>其他设置入口</h3><p>系统偏好、用户身份、Agent 目录、知识库权限和审计记录当前没有 Desktop API。页面不会提供无法保存的设置控件。</p><DataNotice title="真实健康状态">运行状态来自 Desktop 与 Python Sidecar 的 IPC；Cloud、Knowledge、Workflow 和 Worker 的状态还没有接入桌面。</DataNotice></Card>
    </div>
  );
}
