import { RuntimeIpcError } from "../runtime/ndjson-channel";
import type { ChatCompletionInput, ChatCompletionResult, ChatMessage, ProviderFailureStage, ProviderRequestStatus } from "../../preload/api";
import type { DesktopChatService } from "./desktop-ipc";

type SidecarRequest = (
  method: string,
  params: Record<string, unknown>,
  options?: { timeoutMs?: number; traceId?: string; signal?: AbortSignal },
) => Promise<Record<string, unknown>>;

const identifier = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/;
const modelIdentifier = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
const uuid = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const safeCodes = new Set([
  "CHAT_COMPLETION_UNKNOWN", "CHAT_EXTERNAL_PROCESSING_NOT_CONFIRMED", "CHAT_REQUEST_IN_PROGRESS",
  "CHAT_REQUEST_INVALID", "CHAT_SERVICE_UNAVAILABLE", "MODEL_BALANCE_INSUFFICIENT", "MODEL_BUDGET_EXHAUSTED",
  "MODEL_BUDGET_UNAVAILABLE", "MODEL_CAPABILITY_UNSUPPORTED", "MODEL_CREDENTIAL_REJECTED",
  "MODEL_CREDENTIAL_UNAVAILABLE", "MODEL_NETWORK_ERROR", "MODEL_NO_ELIGIBLE_PROVIDER", "MODEL_NOT_FOUND",
  "MODEL_POLICY_DENIED", "MODEL_POLICY_UNAVAILABLE", "MODEL_PROVIDER_ERROR", "MODEL_PROVIDER_NOT_CONFIGURED",
  "MODEL_PROVIDER_RESPONSE_INVALID", "MODEL_PROVIDER_SERVER_ERROR", "MODEL_RATE_LIMITED", "MODEL_REQUEST_INVALID",
  "MODEL_REQUEST_REJECTED", "MODEL_ROUTE_MISMATCH", "MODEL_TIMEOUT", "MODEL_CHAT_FAILED", "RUNTIME_SAFE_MODE", "SIDECAR_EXITED",
  "SIDECAR_IO_FAILED", "SIDECAR_SPAWN_FAILED", "IPC_CHANNEL_CLOSED", "IPC_REQUEST_CANCELLED",
  "IPC_REQUEST_FAILED", "IPC_REQUEST_TIMEOUT", "IPC_WRITE_FAILED",
]);
const requestStatuses = new Set<ProviderRequestStatus>(["not_sent", "http_response_received", "unknown"]);
const failureStages = new Set<ProviderFailureStage>([
  "none", "preflight", "canary_gate", "provider_profile", "secret_resolution", "gateway", "policy", "budget",
  "provider_setup", "provider_transport", "provider_http", "provider_response", "sidecar_ipc", "unknown",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function normalizeResult(value: unknown, expected: ChatCompletionInput): ChatCompletionResult {
  if (!isRecord(value) ||
    !["succeeded", "blocked", "failed"].includes(String(value.status)) ||
    value.provider_id !== expected.providerId || value.model !== expected.modelId || value.trace_id !== expected.traceId ||
    typeof value.request_status !== "string" || !requestStatuses.has(value.request_status as ProviderRequestStatus) ||
    typeof value.failure_stage !== "string" || !failureStages.has(value.failure_stage as ProviderFailureStage) ||
    !(value.safe_error_code === null || (typeof value.safe_error_code === "string" && safeCodes.has(value.safe_error_code))) ||
    !(value.http_status === null || (typeof value.http_status === "number" && Number.isInteger(value.http_status) && value.http_status >= 100 && value.http_status <= 599)) ||
    !(value.response === null || (typeof value.response === "string" && value.response.length <= 32_000)) ||
    !isRecord(value.usage) ||
    Object.keys(value.usage).some((key) => !["prompt_tokens", "completion_tokens", "total_tokens"].includes(key)) ||
    Object.values(value.usage).some((count) => typeof count !== "number" || !Number.isInteger(count) || count < 0) ||
    !(value.cost === null || safeNumber(value.cost)) || !safeNumber(value.latency_ms)) {
    throw new Error("CHAT_RESPONSE_INVALID");
  }
  const status = value.status as ChatCompletionResult["status"];
  if ((status === "succeeded" && (typeof value.response !== "string" || value.safe_error_code !== null ||
      value.request_status !== "http_response_received" || value.failure_stage !== "none" ||
      (typeof value.http_status === "number" && (value.http_status < 200 || value.http_status > 299)))) ||
    (status !== "succeeded" && (value.response !== null || typeof value.safe_error_code !== "string"))) {
    throw new Error("CHAT_RESPONSE_INVALID");
  }
  return {
    status,
    provider_id: expected.providerId,
    model: expected.modelId,
    trace_id: expected.traceId,
    safe_error_code: value.safe_error_code as string | null,
    request_status: value.request_status as ProviderRequestStatus,
    failure_stage: value.failure_stage as ProviderFailureStage,
    http_status: value.http_status as number | null,
    response: value.response as string | null,
    usage: { ...(value.usage as ChatCompletionResult["usage"]) },
    cost: value.cost as number | null,
    latency_ms: value.latency_ms as number,
  };
}

function errorResult(input: ChatCompletionInput, code: string, requestStatus: ProviderRequestStatus, failureStage: ProviderFailureStage): ChatCompletionResult {
  return {
    status: "failed",
    provider_id: input.providerId,
    model: input.modelId,
    trace_id: input.traceId,
    safe_error_code: safeCodes.has(code) ? code : "IPC_REQUEST_FAILED",
    request_status: requestStatus,
    failure_stage: failureStage,
    http_status: null,
    response: null,
    usage: {},
    cost: null,
    latency_ms: 0,
  };
}

export class RuntimeChatService implements DesktopChatService {
  constructor(private readonly request: SidecarRequest) {}

  async complete(input: ChatCompletionInput): Promise<ChatCompletionResult> {
    if (!validInput(input)) throw new Error("CHAT_REQUEST_INVALID");
    try {
      const raw = await this.request(
        "chat.complete",
        {
          provider_id: input.providerId,
          model_id: input.modelId,
          messages: input.messages.map((message) => ({ role: message.role, content: message.content })),
          request_id: input.requestId,
          trace_id: input.traceId,
          external_processing_confirmed: input.externalProcessingConfirmed,
        },
        { timeoutMs: 60_000, traceId: input.traceId },
      );
      return normalizeResult(raw, input);
    } catch (error) {
      const runtimeError = error instanceof RuntimeIpcError ? error : undefined;
      const requestStatus = runtimeError?.requestStatus && requestStatuses.has(runtimeError.requestStatus)
        ? runtimeError.requestStatus
        : "unknown";
      const failureStage = runtimeError?.failureStage && failureStages.has(runtimeError.failureStage)
        ? runtimeError.failureStage
        : "sidecar_ipc";
      return errorResult(input, runtimeError?.code ?? "IPC_REQUEST_FAILED", requestStatus, failureStage);
    }
  }
}

function validInput(input: ChatCompletionInput): input is ChatCompletionInput & { messages: ChatMessage[] } {
  if (!isRecord(input) || Object.keys(input).length !== 6 || typeof input.providerId !== "string" ||
    typeof input.modelId !== "string" || typeof input.requestId !== "string" || typeof input.traceId !== "string" ||
    !identifier.test(input.providerId) || !modelIdentifier.test(input.modelId) ||
    !uuid.test(input.requestId) || !uuid.test(input.traceId) || input.externalProcessingConfirmed !== true ||
    !Array.isArray(input.messages) || input.messages.length < 1 || input.messages.length > 20) return false;
  let total = 0;
  for (let index = 0; index < input.messages.length; index += 1) {
    const message = input.messages[index];
    if (!isRecord(message) || Object.keys(message).length !== 2 ||
      message.role !== (index % 2 === 0 ? "user" : "assistant") || typeof message.content !== "string" ||
      message.content.trim().length === 0 || message.content.length > 4_000) return false;
    total += message.content.length;
    if (total > 16_000) return false;
  }
  return input.messages[input.messages.length - 1]?.role === "user";
}
