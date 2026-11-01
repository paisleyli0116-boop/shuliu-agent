import { useEffect, useState } from "react";
import type { ProviderCatalog, RendererApi } from "../../../preload/api";
import { Card, DataNotice, DataTable, EmptyState, StatusIndicator } from "../../components/design-system";
import { ProviderProfileForm } from "../../components/provider-profile-form";

export function ProvidersRoute({ api }: { api: RendererApi }) {
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    void api.providers.list().then((value) => {
      if (active) setCatalog(value);
    }).catch(() => {
      if (active) setFailed(true);
    });
    return () => { active = false; };
  }, [api]);

  const rows = catalog?.providers.flatMap((provider) => provider.models.map((model) => [
    <div className="model-identity"><strong>{provider.name}</strong><small>{provider.id}</small></div>,
    <code>{model.id}</code>,
    <div className="capability-list">{model.capabilities.map((capability) => <span className="capability-chip" key={capability}>{capability}</span>)}</div>,
    <StatusIndicator tone={provider.configured ? "success" : "warning"} label={provider.configured ? "已配置" : "未配置"} />,
  ])) ?? [];

  return (
    <div className="page-stack">
      <div className="provider-intro">
        <div><span className="eyebrow">MODEL GATEWAY</span><h2>模型与连接</h2><p>目录来自当前 Python Runtime Profile；真实模型调用仍由 Model Gateway 路由。</p></div>
        <StatusIndicator tone="info" label="手动连接测试" />
      </div>
      <Card className="model-catalog">
        <div className="panel-heading"><div><span className="eyebrow">RUNTIME CATALOG</span><h2>运行时模型目录</h2><p>只列出 Sidecar 当前实际加载的 Provider Profile 与模型。</p></div></div>
        {failed ? <div className="error-state" role="alert">运行时模型目录暂时无法读取；未使用本地静态模型清单替代。</div> : !catalog ? <div className="loading-state" aria-live="polite">正在读取 Python Runtime…</div> : catalog.providers.length === 0 ? (
          <EmptyState title="没有已加载的模型 Profile" description="请通过受控的运行时配置加载 Provider Profile；此页面不会生成示例模型。" icon="◇" />
        ) : <DataTable caption="Python Runtime 当前加载的模型 Profile" columns={["Provider", "模型标识", "声明能力", "SecretRef 状态"]} rows={rows} />}
        <DataNotice title="模型配置边界">Profile 由 Python 启动配置 SHULIU_MODEL_PROFILES_JSON 加载；最终模型路由由 Python Model Gateway 决定。当前 Desktop 没有新增、启停、系统默认模型或持久化选型 IPC；配置的模型能力不代表 Policy 已授权或预算充足。</DataNotice>
      </Card>
      <ProviderProfileForm api={api} />
    </div>
  );
}
