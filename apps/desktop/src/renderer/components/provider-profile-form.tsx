import { useEffect, useMemo, useState } from "react";
import type { ProviderCatalog, RendererApi } from "../../preload/api";
import { Button, InputField, SelectField, Toast } from "./design-system";
import { safeUiError } from "../api/client";
import { createProviderSettingsClient } from "../api/providers";

type Feedback = { kind: "success" | "error"; message: string } | null;

const connectionErrorMessages: Record<string, string> = {
  MODEL_BALANCE_INSUFFICIENT: "DeepSeek 账户余额不足，请到服务商平台确认。",
  MODEL_BUDGET_EXHAUSTED: "连接测试预算不足，请联系管理员确认。",
  MODEL_BUDGET_SETTLEMENT_FAILED: "测试额度结算未完成，请凭 Trace ID 联系管理员。",
  MODEL_BUDGET_UNAVAILABLE: "预算校验暂时不可用，连接测试已停止。",
  MODEL_CAPABILITY_UNSUPPORTED: "当前模型不支持此连接测试。",
  MODEL_CONNECTION_FAILED: "原因待确认",
  MODEL_CONNECTION_TEST_DISABLED: "系统尚未启用真实 Provider 连接测试。",
  MODEL_CREDENTIAL_REJECTED: "API Key 或凭据未通过验证，请检查密钥和服务权限。",
  MODEL_CREDENTIAL_UNAVAILABLE: "没有找到已保存的 API Key，请先保存凭据。",
  MODEL_INPUT_INVALID: "连接测试输入无效，请检查模型配置。",
  MODEL_NETWORK_ERROR: "网络连接失败，请检查网络后重试。",
  MODEL_NO_ELIGIBLE_PROVIDER: "当前安全策略下没有可用的模型服务商。",
  MODEL_NOT_FOUND: "服务商未识别该模型，请确认模型 ID。",
  MODEL_POLICY_DENIED: "当前安全策略不允许执行连接测试。",
  MODEL_POLICY_UNAVAILABLE: "安全策略校验暂时不可用，连接测试已停止。",
  MODEL_PROVIDER_ERROR: "原因待确认",
  MODEL_PROVIDER_NOT_CONFIGURED: "尚未配置此服务商。",
  MODEL_PROVIDER_RESPONSE_INVALID: "服务商返回了无法识别的结果。",
  MODEL_PROVIDER_SERVER_ERROR: "服务商暂时异常，请稍后再试。",
  MODEL_PROVIDER_UNAVAILABLE: "模型连接组件暂时不可用。",
  MODEL_RATE_LIMITED: "请求过于频繁；本次没有自动重试。",
  MODEL_REQUEST_INVALID: "连接测试请求无效，请检查配置。",
  MODEL_REQUEST_REJECTED: "服务商拒绝了请求参数，请检查 Provider 和模型配置。",
  MODEL_ROUTE_MISMATCH: "返回的模型路由与当前配置不一致。",
  MODEL_TELEMETRY_UNAVAILABLE: "安全审计暂时不可用，连接测试已停止。",
  MODEL_TIMEOUT: "连接超时；本次没有自动重试。",
  IPC_CHANNEL_CLOSED: "Sidecar 通道已关闭。",
  IPC_JSON_INVALID: "Sidecar 返回了无效协议数据，已进入安全停止状态。",
  IPC_REQUEST_CANCELLED: "连接测试已取消。",
  IPC_REQUEST_FAILED: "Sidecar 未能返回安全结果。",
  IPC_REQUEST_TIMEOUT: "等待 Sidecar 响应超时。",
  IPC_WRITE_FAILED: "连接请求写入 Sidecar 失败。",
  RUNTIME_SAFE_MODE: "运行时处于安全停止状态。",
  SIDECAR_EXITED: "Python Sidecar 意外退出。",
  SIDECAR_IO_FAILED: "Python Sidecar 通信中断。",
  SIDECAR_SPAWN_FAILED: "Python Sidecar 未能启动。",
};

const requestStatusMessages = {
  not_sent: "请求未发出",
  http_response_received: "已收到 HTTP 响应",
  unknown: "请求状态未知，无法确认服务端是否收到",
} as const;

const failureStageMessages: Record<string, string> = {
  none: "完成",
  preflight: "请求预检",
  canary_gate: "连接测试门禁",
  provider_profile: "Provider 配置",
  secret_resolution: "安全凭据读取",
  gateway: "Model Gateway",
  policy: "Policy 安全策略",
  budget: "预算检查",
  provider_setup: "Provider 初始化",
  provider_transport: "网络传输",
  provider_http: "Provider HTTP 响应",
  provider_response: "响应解析",
  sidecar_ipc: "Sidecar 通信",
  unknown: "原因待确认",
};

function connectionFailureMessage(code: string | null): string {
  return code ? connectionErrorMessages[code] ?? "原因待确认" : "原因待确认";
}

function connectionStatusDetails(result: {
  safe_error_code: string | null,
  request_status: "not_sent" | "http_response_received" | "unknown";
  failure_stage: string;
  http_status: number | null;
  trace_id: string;
}): string {
  const safeCode = result.safe_error_code && Object.prototype.hasOwnProperty.call(connectionErrorMessages, result.safe_error_code)
    ? `错误码：${result.safe_error_code}；` : "";
  const http = result.http_status === null ? "" : `；HTTP ${result.http_status}`;
  return `（${safeCode}${requestStatusMessages[result.request_status]}；阶段：${failureStageMessages[result.failure_stage] ?? "原因待确认"}${http}；Trace ID: ${result.trace_id}）`;
}

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
    const capability = selectedModel.capabilities[0];
    if (!capability) {
      setFeedback({ kind: "error", message: "当前模型没有可测试的能力。" });
      return;
    }
    const traceId = globalThis.crypto.randomUUID();
    setBusy(true);
    setFeedback(null);
    try {
      const result = await providers.testConnection({ providerId, capability, traceId });
      if (result.status === "connected") {
        setFeedback({
          kind: "success",
          message: "真实 Provider 连接成功（" + result.latency_ms + " ms；Trace " + result.trace_id + "）。",
        });
      } else {
        setFeedback({
          kind: "error",
          message: "连接测试未通过：" + connectionFailureMessage(result.safe_error_code) +
            connectionStatusDetails(result) + "。",
        });
      }
    } catch {
      setFeedback({ kind: "error", message: "连接结果未返回：请求状态未知，无法确认服务端是否收到。（Trace ID: " + traceId + "）请勿盲目重复测试。" });
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
      setFeedback({ kind: "success", message: "凭据已写入系统安全存储。连接状态需通过手动 canary 测试确认。" });
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
  if (!catalog) {
    return <section className="panel loading-state" aria-live="polite">正在读取可用模型配置…</section>;
  }
  if (catalog.providers.length === 0) {
    return (
      <section className="panel empty-state" aria-live="polite">
        <h3>尚未配置可用模型</h3>
        <p>请先配置 Provider 和模型。配置完成后，可在此保存密钥并手动测试连接。</p>
        <button className="button button--secondary" onClick={() => void loadCatalog()}>重新加载</button>
      </section>
    );
  }
  if (!selectedProvider || !selectedModel) {
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

        </div>

        <SelectField label="Provider" value={providerId} onChange={(event) => chooseProvider(event.currentTarget.value)} disabled={busy}>
            {catalog.providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
        </SelectField>

        <div className="field">
          <span>当前 Profile 模型</span>
          <output className="field-readonly">{selectedModel.id}</output>
          <small>模型标识来自 Python Runtime 配置；此处不会更改系统默认路由。</small>
        </div>

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
          <InputField
              label="API Key"
              type="password"
              autoComplete="new-password"
              value={apiKey}
              onChange={(event) => setApiKey(event.currentTarget.value)}
              placeholder="仅在此处输入，不会回显已保存凭据"
              spellCheck={false}
              disabled={busy}
              hint="密钥通过受控 IPC 写入系统安全存储；页面不会读取或回显已保存密钥。"
          />
        )}

        <div className="button-row">
          <Button variant="secondary" onClick={() => void testConnection()} disabled={busy || selectedModel.capabilities.length === 0}>
            {busy ? "处理中…" : "测试连接"}
          </Button>
          {showCredentialInput && (
            <Button variant="primary" onClick={() => void saveCredential()} disabled={busy || apiKey.trim().length < 8}>
              {selectedProvider.configured ? "保存新密钥" : "保存配置"}
            </Button>
          )}
        </div>

        {feedback && <Toast tone={feedback.kind}>{feedback.message}</Toast>}
      </section>

      <aside className="panel provider-notice">
        <span className="notice-icon" aria-hidden="true">i</span>
        <div>
          <h3>连接状态说明</h3>
          <p>连接测试只会在用户手动触发时发送固定的公开 canary，不会使用任务内容。请求通过 Model Gateway 的 Policy、预算、健康检查与 SecretResolver。</p>
          <p>API Key 不会进入普通配置、日志或页面持久状态；连接结果只返回安全状态码、Trace ID 与延迟。</p>
        </div>
      </aside>
    </div>
  );
}
