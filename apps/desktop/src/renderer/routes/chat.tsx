import { useEffect, useMemo, useRef, useState } from "react";
import type { ChatCompletionResult, ChatMessage, ProviderCatalog, RendererApi } from "../../preload/api";
import { Button, Card, DataNotice, EmptyState, SelectField } from "../components/design-system";

type ChatTurn = {
  id: string;
  role: "user" | "assistant";
  content: string;
  providerName?: string;
  modelId?: string;
  usage?: ChatCompletionResult["usage"];
  latencyMs?: number;
};
type ChatFailure = Pick<ChatCompletionResult, "safe_error_code" | "request_status" | "failure_stage" | "http_status" | "trace_id">;
type ChatModel = { key: string; providerId: string; providerName: string; modelId: string; modelName: string };

function requestStatusLabel(status: ChatFailure["request_status"]): string {
  if (status === "not_sent") return "请求未发送到模型服务";
  if (status === "http_response_received") return "请求已收到服务端响应";
  return "无法确认请求是否到达服务端";
}

export function ChatRoute({ api, runtimeReady, onOpenModels }: {
  api: RendererApi;
  runtimeReady: boolean;
  onOpenModels?: () => void;
}) {
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [catalogError, setCatalogError] = useState(false);
  const [modelKey, setModelKey] = useState("");
  const [draft, setDraft] = useState("");
  const [consent, setConsent] = useState(false);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [failure, setFailure] = useState<ChatFailure | null>(null);
  const [sending, setSending] = useState(false);
  const inFlight = useRef(false);
  const conversation = useRef<ChatMessage[]>([]);

  useEffect(() => {
    let active = true;
    void api.providers.list().then((value) => {
      if (!active) return;
      setCatalog(value);
      const first = value.providers.flatMap((provider) => provider.models
        .filter((model) => model.capabilities.includes("fast_chat") && provider.enabled !== false && provider.configured === true)
        .map((model) => ({ key: JSON.stringify([provider.id, model.id]), provider, model })))[0];
      if (first) setModelKey(first.key);
    }).catch(() => {
      if (active) setCatalogError(true);
    });
    return () => { active = false; };
  }, [api]);

  useEffect(() => {
    setConsent(false);
  }, [modelKey]);

  const models = useMemo<ChatModel[]>(() => catalog?.providers.flatMap((provider) =>
    provider.enabled === false || provider.configured !== true ? [] : provider.models
      .filter((model) => model.capabilities.includes("fast_chat"))
      .map((model) => ({
        key: JSON.stringify([provider.id, model.id]),
        providerId: provider.id,
        providerName: provider.name,
        modelId: model.id,
        modelName: model.name,
      })),
  ) ?? [], [catalog]);
  const selected = models.find((model) => model.key === modelKey);
  const trimmed = draft.trim();
  const historyLength = conversation.current.reduce((sum, message) => sum + message.content.length, 0);
  const inputValid = trimmed.length > 0 && trimmed.length <= 4_000 && historyLength + trimmed.length <= 16_000;
  const canSend = Boolean(selected && consent && runtimeReady && inputValid && !sending);

  async function send(): Promise<void> {
    if (!canSend || !selected || inFlight.current) return;
    const content = trimmed;
    const pendingMessages: ChatMessage[] = [
      ...conversation.current.map((message) => ({ role: message.role, content: message.content })),
      { role: "user", content },
    ];
    const turnId = crypto.randomUUID();
    const traceId = crypto.randomUUID();
    inFlight.current = true;
    setSending(true);
    setDraft("");
    setFailure(null);
    setTurns((current) => [...current, { id: `${turnId}-user`, role: "user", content }]);
    try {
      const result = await api.chat.complete({
        providerId: selected.providerId,
        modelId: selected.modelId,
        messages: pendingMessages,
        requestId: turnId,
        traceId,
        externalProcessingConfirmed: true,
      });
      if (result.status === "succeeded" && result.response !== null) {
        conversation.current = [...pendingMessages, { role: "assistant", content: result.response }];
        setTurns((current) => [...current, {
          id: `${turnId}-assistant`,
          role: "assistant",
          content: result.response as string,
          providerName: selected.providerName,
          modelId: selected.modelId,
          usage: result.usage,
          latencyMs: result.latency_ms,
        }]);
      } else {
        setFailure({
          safe_error_code: result.safe_error_code ?? "CHAT_RESPONSE_INVALID",
          request_status: result.request_status,
          failure_stage: result.failure_stage,
          http_status: result.http_status,
          trace_id: result.trace_id || traceId,
        });
      }
    } catch {
      setFailure({
        safe_error_code: "DESKTOP_OPERATION_FAILED",
        request_status: "unknown",
        failure_stage: "sidecar_ipc",
        http_status: null,
        trace_id: traceId,
      });
    } finally {
      inFlight.current = false;
      setSending(false);
      setConsent(false);
    }
  }

  return (
    <div className="page-stack">
      <Card className="chat-workspace">
        <div className="chat-workspace__toolbar">
          <div><span className="eyebrow">MODEL GATEWAY</span><h2>新对话</h2></div>
          <div className="chat-model-select">
            <SelectField
              label="会话模型"
              aria-label="发送消息的模型"
              value={modelKey}
              disabled={sending || models.length === 0}
              onChange={(event) => setModelKey(event.currentTarget.value)}
              hint={selected ? `${selected.providerName} · 按钮发送时才会调用模型` : "仅显示已启用、凭据已配置且支持对话的模型。"}
            >
              <option value="" disabled>{catalogError ? "模型目录读取失败" : models.length ? "选择模型" : "暂无可用对话模型"}</option>
              {models.map((model) => <option key={model.key} value={model.key}>{model.providerName} · {model.modelName}</option>)}
            </SelectField>
          </div>
        </div>

        <DataNotice tone="warning" title="发送前请确认">
          发送内容会交由所选 Provider 处理，可能产生费用；只发送公开、非敏感文本。当前版本没有持久化金额预算，只限制每次 Sidecar 运行最多 20 次请求，重启后会重置。对话暂存在当前页面，不会在重启后保留；此版本不执行工具、不搜索知识库，也不支持流式输出或取消正在进行的请求。
        </DataNotice>

        {catalogError && <div className="inline-error" role="alert">无法读取 Python Runtime 的模型目录。请确认 Sidecar 在线后重试。</div>}
        {!runtimeReady && <div className="inline-error" role="alert">Runtime 当前不可用或未完成健康检查，发送已暂停。</div>}
        {!catalogError && models.length === 0 && (
          <EmptyState
            title="没有可用的对话模型"
            description="模型必须已启用、凭据已配置并声明 fast_chat 能力。"
            action={onOpenModels ? <Button onClick={onOpenModels}>前往模型与连接</Button> : undefined}
            icon="✦"
          />
        )}

        <div className="chat-transcript" aria-live="polite" aria-label="对话消息">
          {turns.length === 0 && !failure ? (
            <EmptyState title="开始一段真实对话" description="这里显示 Model Gateway 返回的真实回复，不使用示例消息。" icon="✦" />
          ) : turns.map((turn) => (
            <article key={turn.id} className={`chat-message chat-message--${turn.role}`}>
              <span>{turn.role === "user" ? "你" : turn.providerName ?? "模型"}</span>
              <p>{turn.content}</p>
              {turn.role === "assistant" && <small className="chat-message__meta">
                {turn.modelId}{typeof turn.usage?.total_tokens === "number" ? ` · ${turn.usage.total_tokens} tokens` : ""}
                {typeof turn.latencyMs === "number" ? ` · ${turn.latencyMs} ms` : ""}
              </small>}
            </article>
          ))}
          {sending && <div className="chat-pending" role="status">模型正在处理…</div>}
          {failure && (
            <div className="chat-failure" role="alert">
              <strong>本次对话未完成：{failure.safe_error_code ?? "CHAT_REQUEST_FAILED"}</strong>
              <span>{requestStatusLabel(failure.request_status)}{failure.http_status ? ` · HTTP ${failure.http_status}` : ""} · 阶段：{failure.failure_stage}</span>
              <code>Trace ID: {failure.trace_id}</code>
              <small>不会自动重试。确认原因后，请由你决定是否重新发送。</small>
            </div>
          )}
        </div>

        <label className="chat-consent">
          <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.currentTarget.checked)} disabled={sending} />
          <span>我确认消息公开且非敏感，理解调用可能产生费用，并同意发送给所选 Provider</span>
        </label>
        <label className="field chat-composer">
          <span>消息内容</span>
          <textarea
            aria-label="发送消息"
            rows={4}
            maxLength={4_000}
            value={draft}
            onChange={(event) => setDraft(event.currentTarget.value)}
            onKeyDown={(event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void send(); } }}
            placeholder={selected ? "输入一条公开、非敏感的消息…" : "先在模型与连接中启用并配置一个对话模型"}
            disabled={!selected || !runtimeReady || sending}
          />
          <small>{draft.length}/4000 · Ctrl+Enter 发送 · 输入仅在当前页面暂存</small>
        </label>
        <div className="chat-actions">
          <span className="muted">{sending ? "等待模型返回；关闭页面不会自动重试。" : "发送只经过 Model Gateway、Policy、运行时限额和 SecretRef。"}</span>
          <Button variant="primary" aria-label="发送消息" onClick={() => void send()} disabled={!canSend}>
            {sending ? "发送中…" : "发送消息"}
          </Button>
        </div>
      </Card>
    </div>
  );
}
