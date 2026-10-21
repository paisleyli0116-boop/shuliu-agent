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
  options?: { timeoutMs?: number },
) => Promise<Record<string, unknown>>;

const identifier = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/;
const modelIdentifier = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
const traceIdentifier = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
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
    !safeText(value.model, modelIdentifier) ||
    typeof value.capability !== "string" ||
    !capabilities.has(value.capability) ||
    typeof value.latency_ms !== "number" ||
    !Number.isFinite(value.latency_ms) ||
    value.latency_ms < 0 ||
    !safeText(value.trace_id, traceIdentifier) ||
    !(value.safe_error_code === null ||
      (typeof value.safe_error_code === "string" && safeErrorCodes.has(value.safe_error_code)))
  ) throw new Error("PROVIDER_RESPONSE_INVALID");
  return {
    status: value.status,
    provider_id: value.provider_id,
    model: value.model,
    capability: value.capability,
    latency_ms: value.latency_ms,
    trace_id: value.trace_id,
    safe_error_code: value.safe_error_code,
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

  constructor(private readonly request: SidecarRequest) {}

  async list(): Promise<ProviderCatalog> {
    const result = normalizeCatalog(await this.safeRequest("providers.list", {}));
    return {
      providers: result.providers.map((provider) => this.configuredInSession.has(provider.id)
        ? { ...provider, configured: true }
        : provider),
    };
  }

  async testConnection(input: { providerId: string; capability: string }): Promise<ProviderTestResult> {
    if (!safeText(input.providerId, identifier) || !capabilities.has(input.capability)) {
      throw new Error("PROVIDER_REQUEST_INVALID");
    }
    const result = normalizeConnection(await this.safeRequest(
      "providers.test_connection",
      { provider_id: input.providerId, capability: input.capability },
      { timeoutMs: 30_000 },
    ));
    if (result.provider_id !== input.providerId || result.capability !== input.capability) {
      throw new Error("PROVIDER_RESPONSE_INVALID");
    }
    return result;
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
