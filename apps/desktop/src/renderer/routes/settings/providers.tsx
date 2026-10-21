import type { RendererApi } from "../../../preload/api";
import { ProviderProfileForm } from "../../components/provider-profile-form";

export function ProvidersRoute({ api }: { api: RendererApi }) {
  return (
    <div className="page-stack">
      <div className="provider-intro">
        <div><span className="eyebrow">FOUNDATION SETTINGS</span><h2>模型与连接</h2><p>管理可用 Provider、模型能力与安全凭据。</p></div>
        <span className="status-badge status-badge--muted"><span className="status-dot status-dot--pending" />手动连接测试</span>
      </div>
      <ProviderProfileForm api={api} />
    </div>
  );
}
