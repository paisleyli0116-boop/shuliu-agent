import { useEffect, useMemo, useState } from "react";
import type { ProviderCatalog, RendererApi } from "../../preload/api";
import { safeUiError } from "../api/client";
import { createProviderSettingsClient } from "../api/providers";

type Feedback = { kind: "success" | "error" | "simulated"; message: string } | null;

export function ProviderProfileForm({ api }: { api: RendererApi }) {
  const providers = useMemo(() => createProviderSettingsClient(api), [api]);
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [providerId, setProviderId] = useState("");
  const [modelId, setModelId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [replaceMode, setReplaceMode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>(null);

  const loadCatalog = async () => {
    setLoadError(false);
    try {
      const next = await providers.list();
      setCatalog(next);
      const first = next.providers[0];
      if (!providerId && first) setProviderId(first.id);
      const selected = next.providers.find((provider) => provider.id === providerId) ?? first;
      if (selected && !modelId) setModelId(selected.selected_model_id ?? selected.models[0]?.id ?? "");
    } catch {
      setLoadError(true);
    }
  };

  useEffect(() => {
    void loadCatalog();
  }, []);

  const selectedProvider = catalog?.providers.find((provider) => provider.id === providerId) ?? null;
  const selectedModel = useMemo(
    () => selectedProvider?.models.find((model) => model.id === modelId) ?? selectedProvider?.models[0] ?? null,
    [modelId, selectedProvider],
  );

  const chooseProvider = (value: string) => {
    setProviderId(value);
    const provider = catalog?.providers.find((item) => item.id === value);
    setModelId(provider?.selected_model_id ?? provider?.models[0]?.id ?? "");
    setApiKey("");
    setFeedback(null);
    setReplaceMode(false);
  };

  const testConnection = async () => {
    if (!selectedProvider || !selectedModel) return;
    if (!apiKey.trim() && !selectedProvider.configured) {
      setFeedback({ kind: "error", message: "请先输入 API Key；当前测试仅为模拟流程。" });
      return;
    }
    setBusy(true);
    setFeedback(null);
    try {
      const result = await providers.testConnection({ providerId, modelId: selectedModel.id });
      setFeedback({ kind: "simulated", message: result.message });
    } catch {
      setFeedback({ kind: "error", message: "模拟连接测试未完成，请稍后重试。" });
    } finally {
      setApiKey("");
      setBusy(false);
    }
  };

  const saveCredential = async () => {
    if (!selectedProvider || !selectedModel) return;
    if (apiKey.trim().length < 8) {
      setFeedback({ kind: "error", message: "API Key 至少需要 8 个字符。" });
      return;
    }
    setBusy(true);
    setFeedback(null);
    try {
      if (selectedProvider.configured) {
        await providers.replaceCredential({ profileId: selectedProvider.id, apiKey });
      } else {
        await providers.saveProfile({ providerId, modelId: selectedModel.id, apiKey });
      }
      setApiKey("");
      setReplaceMode(false);
      setFeedback({ kind: "success", message: "凭据已安全保存；Provider 尚未连接，真实连接验证将在 Task 28 接入。" });
      await loadCatalog();
    } catch (error) {
      setApiKey("");
      setFeedback({ kind: "error", message: safeUiError(error, "凭据保存失败；请检查系统安全存储后重试。") });
    } finally {
      setBusy(false);
    }
  };

  if (loadError) {
    return (
      <section className="panel">
        <div className="error-state" role="alert">模型配置暂时无法加载。</div>
        <button className="button button--secondary" onClick={() => void loadCatalog()}>重新加载</button>
      </section>
    );
  }
  if (!catalog || !selectedProvider || !selectedModel) {
    return <section className="panel loading-state" aria-live="polite">正在读取可用模型配置…</section>;
  }

  const showCredentialInput = !selectedProvider.configured || replaceMode;
  return (
    <div className="provider-layout">
      <section className="panel provider-form-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">MODEL GATEWAY</span>
            <h2>连接配置</h2>
          </div>
          <span className="simulation-tag">演示配置</span>
        </div>

        <label className="field">
          <span>Provider</span>
          <select value={providerId} onChange={(event) => chooseProvider(event.currentTarget.value)} disabled={busy}>
            {catalog.providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
          </select>
        </label>

        <label className="field">
          <span>Model</span>
          <select value={selectedModel.id} onChange={(event) => { setModelId(event.currentTarget.value); setFeedback(null); }} disabled={busy}>
            {selectedProvider.models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
          </select>
        </label>

        <div className="capability-list" aria-label="模型能力">
          <span className="field__caption">能力</span>
          {selectedModel.capabilities.map((capability) => <span className="capability-chip" key={capability}>{capability}</span>)}
        </div>

        {selectedProvider.configured && !replaceMode && (
          <div className="credential-status">
            <span className="status-dot status-dot--healthy" aria-hidden="true" />
            <span>已配置凭据 · 仅显示状态</span>
            <button className="text-button" onClick={() => { setReplaceMode(true); setFeedback(null); }}>更换密钥</button>
          </div>
        )}

        {showCredentialInput && (
          <label className="field">
            <span>API Key</span>
            <input
              type="password"
              autoComplete="new-password"
              value={apiKey}
              onChange={(event) => setApiKey(event.currentTarget.value)}
              placeholder="仅在此处输入，不会回显已保存凭据"
              spellCheck={false}
              disabled={busy}
            />
            <small>保存时通过受控 IPC 交给主进程，并由系统安全存储加密。</small>
          </label>
        )}

        <div className="button-row">
          <button className="button button--secondary" onClick={() => void testConnection()} disabled={busy || (!apiKey.trim() && !selectedProvider.configured)}>
            {busy ? "处理中…" : "测试连接"}
          </button>
          {showCredentialInput && (
            <button className="button button--primary" onClick={() => void saveCredential()} disabled={busy || apiKey.trim().length < 8}>
              {selectedProvider.configured ? "保存新密钥" : "保存配置"}
            </button>
          )}
        </div>

        {feedback && <div className={`feedback feedback--${feedback.kind}`} role={feedback.kind === "error" ? "alert" : "status"}>{feedback.message}</div>}
      </section>

      <aside className="panel provider-notice">
        <span className="notice-icon" aria-hidden="true">i</span>
        <div>
          <h3>连接状态说明</h3>
          <p>页面操作会真实经过 Renderer → Preload → Main。Provider 目录与连接测试目前是演示实现；测试结果不会发起网络请求。</p>
          <p>API Key 只送入主进程的 OS secure storage，不会返回 Renderer。真实 Provider、预算策略和 Model Gateway 请求由 Task 28 接入。</p>
        </div>
      </aside>
    </div>
  );
}
