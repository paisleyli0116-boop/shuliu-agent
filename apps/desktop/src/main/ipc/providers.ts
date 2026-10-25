import { randomUUID } from "node:crypto";
import { RuntimeIpcError } from "../runtime/ndjson-channel";
import type {
  ProviderCatalog,
  ProviderCredentialResult,
  ProviderOption,
  ProviderTestResult,
} from "../../preload/api";
import type { DesktopProviderService } from "./desktop-ipc";

type SidecarRequest = (
  method: string,
  params: Record<string, unknown>,
  options?: { timeoutMs?: number; traceId?: string; signal?: AbortSignal },
) => Promise<Record<string, unknown>>;

export type RuntimeProviderServiceOptions = {
  canaryEnabled?: boolean;
};

const identifier = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/;
const modelIdentifier = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
const traceIdentifier = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const safeErrorCodes = new Set([
  "MODEL_BALANCE_INSUFFICIENT",
  "MODEL_BUDGET_EXHAUSTED",
  "MODEL_BUDGET_SETTLEMENT_FAILED",
  "MODEL_BUDGET_UNAVAILABLE",
  "MODEL_CAPABILITY_UNSUPPORTED",
  "MODEL_CONNECTION_FAILED",
  "MODEL_CONNECTION_TEST_DISABLED",
  "MODEL_CREDENTIAL_REJECTED",
  "MODEL_CREDENTIAL_UNAVAILABLE",
  "MODEL_INPUT_INVALID",
  "MODEL_NETWORK_ERROR",
  "MODEL_NO_ELIGIBLE_PROVIDER",
  "MODEL_NOT_FOUND",
  "MODEL_POLICY_DENIED",
  "MODEL_POLICY_UNAVAILABLE",
  "MODEL_PROVIDER_ERROR",
  "MODEL_PROVIDER_NOT_CONFIGURED",
  "MODEL_PROVIDER_RESPONSE_INVALID",
  "MODEL_PROVIDER_SERVER_ERROR",
  "MODEL_PROVIDER_UNAVAILABLE",
  "MODEL_RATE_LIMITED",
  "MODEL_REQUEST_INVALID",
  "MODEL_REQUEST_REJECTED",
  "MODEL_ROUTE_MISMATCH",
  "MODEL_TELEMETRY_UNAVAILABLE",
  "MODEL_TIMEOUT",
  "IPC_CHANNEL_CLOSED",
  "IPC_JSON_INVALID",
  "IPC_REQUEST_CANCELLED",
  "IPC_REQUEST_FAILED",
  "IPC_REQUEST_TIMEOUT",
  "IPC_WRITE_FAILED",
  "RUNTIME_SAFE_MODE",
  "SIDECAR_EXITED",
  "SIDECAR_IO_FAILED",
  "SIDECAR_SPAWN_FAILED",
]);
const requestStatuses = new Set(["not_sent", "http_response_received", "unknown"]);
const failureStages = new Set([
  "none", "preflight", "canary_gate", "provider_profile", "secret_resolution", "gateway", "policy", "budget",
  "provider_setup", "provider_transport", "provider_http", "provider_response", "sidecar_ipc", "unknown",
]);
const capabilities = new Set(["fast_chat", "reasoning_high", "vision", "embedding"]);
const credentialStorages = new Set(["OS_SECURE_STORAGE", "CLOUD_SECRET_MANAGER"]);

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeText(value: unknown, pattern: RegExp): value is string {
  return typeof value === "string" && pattern.test(value);
}

function normalizeCatalog(value: unknown): ProviderCatalog {
  if (!record(value) || !Array.isArray(value.providers)) throw new Error("PROVIDER_RESPONSE_INVALID");
  const providers: ProviderOption[] = value.providers.map((item) => {
    if (
      !record(item) ||
      !safeText(item.id, identifier) ||
      typeof item.name !== "string" ||
      !Array.isArray(item.models)
    ) throw new Error("PROVIDER_RESPONSE_INVALID");
    const models = item.models.map((model) => {
      if (
        !record(model) ||
        !safeText(model.id, modelIdentifier) ||
        typeof model.name !== "string" ||
        !Array.isArray(model.capabilities) ||
        model.capabilities.length === 0 ||
        model.capabilities.some((capability) => typeof capability !== "string" || !capabilities.has(capability))
      ) throw new Error("PROVIDER_RESPONSE_INVALID");
      return {
        id: model.id,
        name: model.name,
        capabilities: [...model.capabilities] as string[],
      };
    });
    const selectedModelId =
      typeof item.selected_model_id === "string" && models.some((model) => model.id === item.selected_model_id)
        ? item.selected_model_id
        : models[0]?.id;
    return {
      id: item.id,
      name: item.name,
      models,
      ...(typeof item.configured === "boolean" ? { configured: item.configured } : {}),
      ...(selectedModelId ? { selected_model_id: selectedModelId } : {}),
    };
  });
  return { providers };
}

function normalizeConnection(value: unknown): ProviderTestResult {
  if (
    !record(value) ||
    (value.status !== "connected" && value.status !== "blocked" && value.status !== "failed") ||
    !safeText(value.provider_id, identifier) ||
    !(safeText(value.model, modelIdentifier) ||
      (value.model === "" && value.status === "blocked" && value.safe_error_code === "MODEL_CONNECTION_TEST_DISABLED")) ||
    typeof value.capability !== "string" ||
    !capabilities.has(value.capability) ||
    typeof value.latency_ms !== "number" ||
    !Number.isFinite(value.latency_ms) ||
    value.latency_ms < 0 ||
    !safeText(value.trace_id, traceIdentifier) ||
    !(value.safe_error_code === null ||
      (typeof value.safe_error_code === "string" && safeErrorCodes.has(value.safe_error_code))) ||
    !(typeof value.request_status === "string" && requestStatuses.has(value.request_status)) ||
    !(typeof value.failure_stage === "string" && failureStages.has(value.failure_stage)) ||
    !(value.http_status === null ||
      (typeof value.http_status === "number" && Number.isInteger(value.http_status) && value.http_status >= 100 && value.http_status <= 599)) ||
    (value.status === "connected" &&
      (value.safe_error_code !== null || value.request_status !== "http_response_received" || value.failure_stage !== "none")) ||
    (value.status !== "connected" && value.safe_error_code === null)
  ) throw new Error("PROVIDER_RESPONSE_INVALID");
  return {
    status: value.status,
    provider_id: value.provider_id,
    model: value.model,
    capability: value.capability,
    latency_ms: value.latency_ms,
    trace_id: value.trace_id,
    safe_error_code: value.safe_error_code,
    request_status: value.request_status as ProviderTestResult["request_status"],
    failure_stage: value.failure_stage as ProviderTestResult["failure_stage"],
    http_status: value.http_status as number | null,
  };
}

function normalizeCredential(value: unknown, expectedProfileId: string): ProviderCredentialResult {
  if (
    !record(value) ||
    value.profile_id !== expectedProfileId ||
    value.configured !== true ||
    typeof value.storage !== "string" ||
    !credentialStorages.has(value.storage)
  ) throw new Error("PROVIDER_RESPONSE_INVALID");
  return {
    profile_id: expectedProfileId,
    configured: true,
    storage: value.storage as ProviderCredentialResult["storage"],
  };
}

export class RuntimeProviderService implements DesktopProviderService {
  private readonly configuredInSession = new Set<string>();
  private canaryConsumed = false;
  private readonly canaryEnabled: boolean;

  constructor(
    private readonly request: SidecarRequest,
    private readonly onConnectionDiagnostic?: (result: ProviderTestResult) => void,
    options: RuntimeProviderServiceOptions = {},
  ) {
    this.canaryEnabled = options.canaryEnabled === true;
  }

  private reportConnection(result: ProviderTestResult): ProviderTestResult {
    try {
      this.onConnectionDiagnostic?.(result);
    } catch {
      // Diagnostic persistence must never change the connection result.
    }
    return result;
  }

  async list(): Promise<ProviderCatalog> {
    const result = normalizeCatalog(await this.safeRequest("providers.list", {}));
    return {
      providers: result.providers.map((provider) => this.configuredInSession.has(provider.id)
        ? { ...provider, configured: true }
        : provider),
    };
  }

  async testConnection(input: { providerId: string; capability: string; traceId?: string }): Promise<ProviderTestResult> {
    if (!safeText(input.providerId, identifier) || !capabilities.has(input.capability) ||
      (input.traceId !== undefined && !safeText(input.traceId, traceIdentifier))) {
      throw new Error("PROVIDER_REQUEST_INVALID");
    }
    const traceId = input.traceId?.toLowerCase() ?? randomUUID();
    if (!this.canaryEnabled || this.canaryConsumed) {
      return this.reportConnection({
        status: "blocked",
        provider_id: input.providerId,
        model: "",
        capability: input.capability,
        latency_ms: 0,
        trace_id: traceId,
        safe_error_code: "MODEL_CONNECTION_TEST_DISABLED",
        request_status: "not_sent",
        failure_stage: "canary_gate",
        http_status: null,
      });
    }
    // Consume synchronously before the first await: concurrent clicks and
    // retries remain blocked even if the Sidecar response becomes unknown.
    this.canaryConsumed = true;
    let raw: Record<string, unknown>;
    try {
      raw = await this.request(
        "providers.test_connection",
        { provider_id: input.providerId, capability: input.capability, trace_id: traceId },
        { timeoutMs: 30_000, traceId },
      );
    } catch (error) {
      return this.reportConnection(this.sidecarFailure(input.providerId, input.capability, traceId, error));
    }

    let result: ProviderTestResult;
    try {
      result = normalizeConnection(raw);
    } catch {
      return this.reportConnection({
        status: "failed",
        provider_id: input.providerId,
        model: "",
        capability: input.capability,
        latency_ms: 0,
        trace_id: traceId,
        safe_error_code: "MODEL_PROVIDER_RESPONSE_INVALID",
        request_status: "unknown",
        failure_stage: "provider_response",
        http_status: null,
      });
    }
    if (result.provider_id !== input.providerId || result.capability !== input.capability || result.trace_id !== traceId) {
      return this.reportConnection({
        status: "failed",
        provider_id: input.providerId,
        model: "",
        capability: input.capability,
        latency_ms: result.latency_ms,
        trace_id: traceId,
        safe_error_code: "MODEL_PROVIDER_RESPONSE_INVALID",
        request_status: "unknown",
        failure_stage: "provider_response",
        http_status: null,
      });
    }
    return this.reportConnection(result);
  }

  async saveProfile(input: {
    providerId: string;
    modelId: string;
    apiKey: string;
  }): Promise<ProviderCredentialResult> {
    if (
      !safeText(input.providerId, identifier) ||
      !safeText(input.modelId, modelIdentifier) ||
      typeof input.apiKey !== "string" ||
      input.apiKey.length < 8 ||
      input.apiKey.length > 4096 ||
      input.apiKey !== input.apiKey.trim()
    ) throw new Error("PROVIDER_REQUEST_INVALID");
    const result = normalizeCredential(await this.safeRequest("providers.save_profile", {
      provider_id: input.providerId,
      model_id: input.modelId,
      api_key: input.apiKey,
    }), input.providerId);
    this.configuredInSession.add(input.providerId);
    return result;
  }

  async replaceCredential(input: { profileId: string; apiKey: string }): Promise<ProviderCredentialResult> {
    if (
      !safeText(input.profileId, identifier) ||
      typeof input.apiKey !== "string" ||
      input.apiKey.length < 8 ||
      input.apiKey.length > 4096 ||
      input.apiKey !== input.apiKey.trim()
    ) throw new Error("PROVIDER_REQUEST_INVALID");
    const result = normalizeCredential(await this.safeRequest("providers.replace_credential", {
      profile_id: input.profileId,
      api_key: input.apiKey,
    }), input.profileId);
    this.configuredInSession.add(input.profileId);
    return result;
  }

  private sidecarFailure(
    providerId: string,
    capability: string,
    traceId: string,
    error: unknown,
  ): ProviderTestResult {
    const runtimeError = error instanceof RuntimeIpcError ? error : undefined;
    const code = runtimeError && safeErrorCodes.has(runtimeError.code)
      ? runtimeError.code
      : "IPC_REQUEST_FAILED";
    const requestStatus = runtimeError?.requestStatus && requestStatuses.has(runtimeError.requestStatus)
      ? runtimeError.requestStatus
      : "unknown";
    const failureStage = runtimeError?.failureStage && failureStages.has(runtimeError.failureStage)
      ? runtimeError.failureStage
      : "sidecar_ipc";
    return {
      status: "failed",
      provider_id: providerId,
      model: "",
      capability,
      latency_ms: 0,
      trace_id: traceId,
      safe_error_code: code,
      request_status: requestStatus as ProviderTestResult["request_status"],
      failure_stage: failureStage as ProviderTestResult["failure_stage"],
      http_status: runtimeError?.httpStatus ?? null,
    };
  }

  private async safeRequest(
    method: string,
    params: Record<string, unknown>,
    options?: { timeoutMs?: number },
  ): Promise<Record<string, unknown>> {
    try {
      return await this.request(method, params, options);
    } catch {
      throw new Error("PROVIDER_OPERATION_FAILED");
    }
  }
}
