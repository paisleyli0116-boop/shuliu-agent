import { useEffect, useState } from "react";
import type { RendererApi, RuntimeHealth } from "../preload/api";
import { getRendererApi } from "./api/client";
import { RuntimeHealth as RuntimeHealthCard } from "./components/runtime-health";
import { ApprovalsRoute } from "./routes/approvals";
import { ProvidersRoute } from "./routes/settings/providers";
import { TasksRoute } from "./routes/tasks";

type Page = "tasks" | "approvals" | "runtime" | "providers";
const navigation: Array<{ id: Page; label: string; icon: string; section: "工作台" | "设置" }> = [
  { id: "tasks", label: "任务", icon: "▤", section: "工作台" },
  { id: "approvals", label: "审批", icon: "◷", section: "工作台" },
  { id: "runtime", label: "运行状态", icon: "◉", section: "工作台" },
  { id: "providers", label: "模型与连接", icon: "⌘", section: "设置" },
];
const pageTitles: Record<Page, { title: string; description: string }> = {
  tasks: { title: "任务工作台", description: "创建和跟踪通用 Agent 任务。" },
  approvals: { title: "人工审批", description: "查看需要人工判断的操作请求。" },
  runtime: { title: "运行状态", description: "检查 Desktop 与 Python Runtime 的连接状态。" },
  providers: { title: "模型与连接", description: "管理模型能力与凭据。Provider 请求目前处于模拟模式。" },
};

export function App({ api: providedApi }: { api?: RendererApi }) {
  const api = providedApi ?? getRendererApi();
  const [page, setPage] = useState<Page>("tasks");
  const [health, setHealth] = useState<RuntimeHealth | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [pendingApprovalCount, setPendingApprovalCount] = useState(0);

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
  useEffect(() => {
    let active = true;
    void api.approvals.list().then((approvals) => {
      if (active) setPendingApprovalCount(approvals.filter((approval) => approval.status === "PENDING").length);
    }).catch(() => undefined);
    return () => { active = false; };
  }, [api]);
  const safeMode = health?.status === "safe_mode" || health?.status === "unhealthy";
  const activePage = pageTitles[page];

  return (
    <div className="desktop-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <span><strong>ShuLiu</strong><small>AGENT FOUNDATION</small></span>
        </div>
        <div className="workspace-chip"><span className="workspace-avatar">S</span><span><strong>Foundation Workspace</strong><small>V0.1 · 本地工作区</small></span><span className="chevron">⌄</span></div>
        <nav aria-label="主导航" className="sidebar-nav">
          {(["工作台", "设置"] as const).map((section) => (
            <div className="nav-group" key={section}>
              <span className="nav-group__label">{section}</span>
              {navigation.filter((item) => item.section === section).map((item) => (
                <button key={item.id} type="button" aria-label={item.label} className={`nav-item ${page === item.id ? "nav-item--active" : ""}`} aria-current={page === item.id ? "page" : undefined} onClick={() => setPage(item.id)}>
                  <span className="nav-item__icon" aria-hidden="true">{item.icon}</span><span>{item.label}</span>
                  {item.id === "approvals" && pendingApprovalCount > 0 && <span className="nav-item__count">{pendingApprovalCount}</span>}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-health"><RuntimeHealthCard health={health} compact /></div>
          <div className="user-row"><span className="user-avatar">星</span><span><strong>本地管理员</strong><small>单公司内部版</small></span><button className="icon-button" aria-label="账户菜单" title="账户菜单" onClick={() => setPage("runtime")}>···</button></div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb"><span>ShuLiu Agent</span><span className="breadcrumb__slash">/</span><strong>{activePage.title}</strong></div>
          <div className="topbar-actions">
            <span className="demo-chip"><span className="demo-dot" />LOCAL DEMO</span>
            <span className={`runtime-indicator ${health?.status === "healthy" ? "runtime-indicator--healthy" : ""}`}>
              <span className="status-dot status-dot--healthy" />{healthLoading ? "检查中" : health?.status === "healthy" ? "Sidecar 在线" : safeMode ? "Safe Mode" : "状态待检查"}
            </span>
          </div>
        </header>

        {safeMode && (
          <div className="safe-mode-banner" role="alert">
            <span className="safe-mode-icon" aria-hidden="true">!</span>
            <div><strong>Safe Mode 已启用</strong><span>Python Runtime 不可用或状态不安全。任务执行和审批决策已暂停；你仍可查看状态并调整模型设置。</span></div>
            <button className="text-button" onClick={() => setPage("runtime")}>查看状态</button>
          </div>
        )}

        <section className="page-content">
          <div className="page-heading">
            <div><span className="eyebrow">SHULIU FOUNDATION · V0.1</span><h1>{activePage.title}</h1><p>{activePage.description}</p></div>
            <div className="page-heading__status"><RuntimeHealthCard health={health} compact /></div>
          </div>
          <div className="demo-banner"><span className="demo-banner__icon" aria-hidden="true">i</span><span><strong>演示工作区</strong>　任务与审批数据为当前进程内的演示数据；真实 Provider 连接尚未接入。API Key 保存时会交由主进程的系统安全存储处理。</span></div>

          {page === "tasks" && <TasksRoute api={api} safeMode={safeMode} />}
          {page === "approvals" && <ApprovalsRoute api={api} safeMode={safeMode} onPendingCountChange={setPendingApprovalCount} />}
          {page === "runtime" && <RuntimeRoute api={api} health={health} loading={healthLoading} refresh={refreshHealth} />}
          {page === "providers" && <ProvidersRoute api={api} />}

          <footer className="content-footer"><span>ShuLiu Agent Foundation</span><span>Protocol v1 <i /> 安全边界已启用</span></footer>
        </section>
      </main>
    </div>
  );
}

function RuntimeRoute({ api, health, loading, refresh }: {
  api: RendererApi;
  health: RuntimeHealth | null;
  loading: boolean;
  refresh(): Promise<void>;
}) {
  return (
    <div className="page-stack">
      <section className="panel runtime-panel">
        <div className="panel-heading">
          <div><span className="eyebrow">RUNTIME HEALTH</span><h2>核心组件</h2></div>
          <button className="button button--secondary" onClick={() => void refresh()} disabled={loading}>{loading ? "检查中…" : "重新检查"}</button>
        </div>
        <div className="runtime-primary">
          <RuntimeHealthCard health={health} />
          <div><strong>Electron ↔ Python Sidecar</strong><span>stdio + NDJSON · protocol v1</span><small>状态由真实 Python 子进程握手和 health 请求返回。</small></div>
        </div>
        {health?.status === "safe_mode" || health?.status === "unhealthy" ? <div className="runtime-error" role="alert"><strong>Safe Mode</strong><span>新任务已暂停。检查 Python 环境或协议版本后重新检查运行状态。</span><code>{health.code ?? "RUNTIME_UNHEALTHY"}</code></div> : health?.status === "healthy" ? <div className="runtime-ok"><span className="status-dot status-dot--healthy" /><span>健康检查通过，允许继续查看工作台。</span></div> : null}
      </section>
      <section className="panel runtime-boundary"><span className="eyebrow">INTEGRATION STATUS</span><h3>其他 Foundation 服务</h3><p>Cloud API、Knowledge、Model Gateway、Browser Runtime 与 Worker 的桌面状态接入会随相应子系统实现逐步开放。当前页面不会用演示数据伪装这些组件已连接。</p></section>
    </div>
  );
}
