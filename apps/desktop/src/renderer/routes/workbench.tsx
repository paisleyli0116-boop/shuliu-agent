import { useEffect, useState } from "react";
import type { ProviderCatalog, RendererApi, RuntimeHealth } from "../../preload/api";
import { Button, Card, DataNotice, StatusIndicator } from "../components/design-system";

export type WorkbenchDestination = "chat" | "agents" | "tasks" | "workflows" | "knowledge" | "models" | "tools" | "runs";

export function WorkbenchRoute({ api, health, onNavigate }: {
  api: RendererApi;
  health: RuntimeHealth | null;
  onNavigate(page: WorkbenchDestination): void;
}) {
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [catalogFailed, setCatalogFailed] = useState(false);

  useEffect(() => {
    let active = true;
    void api.providers.list().then((value) => {
      if (active) setCatalog(value);
    }).catch(() => {
      if (active) setCatalogFailed(true);
    });
    return () => { active = false; };
  }, [api]);

  const configured = catalog?.providers.filter((provider) => provider.configured).length ?? 0;
  const runtimeTone = health?.status === "healthy" ? "success" : health?.status === "degraded" ? "warning" : health ? "danger" : "neutral";
  const runtimeLabel = health?.status === "healthy" ? "运行正常" : health?.status === "degraded" ? "运行受限" : health ? "Safe Mode" : "检查中";

  return (
    <div className="page-stack">
      <div className="workbench-intro">
        <div><span className="eyebrow">FOUNDATION OVERVIEW</span><h2>从这里开始</h2><p>查看真实运行状态，找到当前版本可以使用的入口。</p></div>
        <StatusIndicator tone={runtimeTone} label={runtimeLabel} detail={health?.code} />
      </div>

      <section className="workbench-grid" aria-label="Foundation 能力状态">
        <Card className="overview-card overview-card--runtime">
          <div className="overview-card__icon">◉</div><span className="eyebrow">RUNTIME</span>
          <h3>桌面运行环境</h3>
          <StatusIndicator tone={runtimeTone} label={runtimeLabel} />
          <p>{health?.code ? `安全状态码：${health.code}` : "健康状态来自 Electron 与 Python Sidecar 的真实 health 响应。"}</p>
          <Button variant="quiet" onClick={() => onNavigate("tools")}>查看运行能力边界</Button>
        </Card>
        <Card className="overview-card overview-card--models">
          <div className="overview-card__icon overview-card__icon--ai">✦</div><span className="eyebrow">MODEL PROFILES</span>
          <h3>模型配置</h3>
          {catalogFailed ? <StatusIndicator tone="danger" label="目录读取失败" /> : catalog ? <StatusIndicator tone="info" label={`${catalog.providers.length} 个运行时 Profile`} detail={`${configured} 个已配置凭据`} /> : <StatusIndicator tone="neutral" label="读取中" />}
          <p>目录来自 Python Runtime 的 Provider Profile；路由、Policy 和预算仍由 Model Gateway 决定。</p>
          <Button variant="quiet" onClick={() => onNavigate("models")}>打开模型与连接</Button>
        </Card>
        <Card className="overview-card overview-card--chat">
          <div className="overview-card__icon overview-card__icon--ai">✧</div><span className="eyebrow">AGENT WORK</span>
          <h3>对话与 Agent</h3>
          <p>桌面端尚未接入会话执行和 Agent 目录；不展示虚构的对话、在线状态或任务成果。</p>
          <Button variant="quiet" onClick={() => onNavigate("chat")}>查看接入状态</Button>
        </Card>
      </section>

      <Card className="workbench-next-steps">
        <div className="panel-heading"><div><span className="eyebrow">AVAILABLE NOW</span><h2>当前可用功能</h2></div></div>
        <div className="quick-links">
          <button type="button" onClick={() => onNavigate("tasks")}><span className="quick-link__icon">▤</span><span><strong>任务中心</strong><small>查看真实任务与 Agent 执行接口的接入状态。</small></span><span aria-hidden="true">→</span></button>
          <button type="button" onClick={() => onNavigate("runs")}><span className="quick-link__icon">◷</span><span><strong>审批与运行记录</strong><small>查看审批服务与持久化审计的接入状态。</small></span><span aria-hidden="true">→</span></button>
          <button type="button" onClick={() => onNavigate("models")}><span className="quick-link__icon quick-link__icon--ai">✦</span><span><strong>模型与连接</strong><small>查看运行时 Profile，管理安全凭据并手动测试连接。</small></span><span aria-hidden="true">→</span></button>
        </div>
      </Card>

      <DataNotice title="数据来源说明">任务和审批的本地演示 API 不会作为真实业务记录展示；运行健康和 Provider Profile 来自真实 Sidecar IPC。</DataNotice>
    </div>
  );
}
