import { describe, expect, it } from "vitest";
import { RuntimeIpcError } from "../../src/main/runtime/ndjson-channel";

async function loadRuntimeProviderService() {
  const module = await import("../../src/main/ipc/providers").catch(() => null);
  expect(module?.RuntimeProviderService).toBeTypeOf("function");
  return module!.RuntimeProviderService;
}

describe("runtime provider settings bridge", () => {
  it("forwards typed profile operations to the Python Sidecar", async () => {
    const RuntimeProviderService = await loadRuntimeProviderService();
    const calls: Array<{ method: string; params: Record<string, unknown>; timeoutMs?: number }> = [];
    const service = new RuntimeProviderService(async (method, params, options) => {
      calls.push({ method, params, timeoutMs: options?.timeoutMs });
      if (method === "providers.list") {
        return { providers: [{
          id: "provider-a",
          name: "Provider A",
          models: [{ id: "openai/gpt-4.1-mini", name: "Model A", capabilities: ["fast_chat"] }],
          selected_model_id: "openai/gpt-4.1-mini",
        }] };
      }
      if (method === "providers.test_connection") {
        return {
          status: "connected",
          provider_id: "provider-a",
          model: "openai/gpt-4.1-mini",
          capability: "fast_chat",
          latency_ms: 42,
          trace_id: params.trace_id,
          safe_error_code: null,
          request_status: "http_response_received",
          failure_stage: "none",
          http_status: 200,
        };
      }
      return { profile_id: "provider-a", configured: true, storage: "OS_SECURE_STORAGE" };
    }, undefined, { canaryEnabled: true });

    const catalog = await service.list();
    const connection = await service.testConnection({ providerId: "provider-a", capability: "fast_chat" });
    const saved = await service.saveProfile({
      providerId: "provider-a",
      modelId: "openai/gpt-4.1-mini",
      apiKey: "sk-provider-key-marker",
    });
    const replaced = await service.replaceCredential({ profileId: "provider-a", apiKey: "sk-replacement-marker" });

    expect(catalog.providers[0].id).toBe("provider-a");
    expect(connection).toMatchObject({ status: "connected", provider_id: "provider-a", request_status: "http_response_received", http_status: 200 });
    expect(saved).toEqual({ profile_id: "provider-a", configured: true, storage: "OS_SECURE_STORAGE" });
    expect(replaced).toEqual({ profile_id: "provider-a", configured: true, storage: "OS_SECURE_STORAGE" });
    expect(calls.map(({ method }) => method)).toEqual([
      "providers.list",
      "providers.test_connection",
      "providers.save_profile",
      "providers.replace_credential",
    ]);
    expect(calls[1].params).toMatchObject({ provider_id: "provider-a", capability: "fast_chat" });
    expect(calls[1].params.trace_id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(calls[2].params).toEqual({
      provider_id: "provider-a",
      model_id: "openai/gpt-4.1-mini",
      api_key: "sk-provider-key-marker",
    });
    expect(calls[3].params).toEqual({
      profile_id: "provider-a",
      api_key: "sk-replacement-marker",
    });
    expect(calls[1].timeoutMs).toBeGreaterThanOrEqual(20_000);
  });

  it("preserves a blocked canary result with no model so the UI can show its safe code and trace", async () => {
    const RuntimeProviderService = await loadRuntimeProviderService();
    const service = new RuntimeProviderService(async (_method, params) => ({
      status: "blocked",
      provider_id: "deepseek",
      model: "",
      capability: "fast_chat",
      latency_ms: 0,
      trace_id: params.trace_id,
      safe_error_code: "MODEL_CONNECTION_TEST_DISABLED",
      request_status: "not_sent",
      failure_stage: "canary_gate",
      http_status: null,
    }), undefined, { canaryEnabled: true });

    await expect(service.testConnection({ providerId: "deepseek", capability: "fast_chat" }))
      .resolves.toEqual({
        status: "blocked",
        provider_id: "deepseek",
        model: "",
        capability: "fast_chat",
        latency_ms: 0,
        trace_id: expect.stringMatching(/^[0-9a-f-]{36}$/i),
        safe_error_code: "MODEL_CONNECTION_TEST_DISABLED",
        request_status: "not_sent",
        failure_stage: "canary_gate",
        http_status: null,
      });
  });
  it("rejects unrecognized provider error codes at the IPC boundary", async () => {
    const RuntimeProviderService = await loadRuntimeProviderService();
    const service = new RuntimeProviderService(async (_method, params) => ({
      status: "failed",
      provider_id: "provider-a",
      model: "openai/gpt-4.1-mini",
      capability: "fast_chat",
      latency_ms: 42,
      trace_id: params.trace_id,
      safe_error_code: "INTERNAL_SECRET_MARKER",
      request_status: "http_response_received",
      failure_stage: "provider_http",
      http_status: 401,
    }), undefined, { canaryEnabled: true });

    const result = await service.testConnection({ providerId: "provider-a", capability: "fast_chat" });

    expect(result).toMatchObject({
      status: "failed",
      safe_error_code: "MODEL_PROVIDER_RESPONSE_INVALID",
      request_status: "unknown",
      failure_stage: "provider_response",
      http_status: null,
    });
    expect(JSON.stringify(result)).not.toContain("INTERNAL_SECRET_MARKER");
  });
});

describe("provider canary session authorization", () => {
  it("fails closed by default without forwarding a test request to the Sidecar", async () => {
    const RuntimeProviderService = await loadRuntimeProviderService();
    let requests = 0;
    const service = new RuntimeProviderService(async () => {
      requests += 1;
      return {};
    });

    const result = await service.testConnection({ providerId: "deepseek", capability: "fast_chat" });

    expect(requests).toBe(0);
    expect(result).toMatchObject({
      status: "blocked",
      safe_error_code: "MODEL_CONNECTION_TEST_DISABLED",
      request_status: "not_sent",
      failure_stage: "canary_gate",
    });
    expect(result.trace_id).toMatch(/^[0-9a-f-]{36}$/i);
  });

  it("consumes an enabled session before IPC so concurrent clicks and retries cannot make a second request", async () => {
    const RuntimeProviderService = await loadRuntimeProviderService();
    let requests = 0;
    let releaseRequest: ((value: Record<string, unknown>) => void) | undefined;
    let markStarted: (() => void) | undefined;
    const started = new Promise<void>((resolve) => { markStarted = resolve; });
    const pendingResponse = new Promise<Record<string, unknown>>((resolve) => { releaseRequest = resolve; });
    const service = new RuntimeProviderService(async (_method, params) => {
      requests += 1;
      markStarted?.();
      return pendingResponse.then((response) => ({ ...response, trace_id: params.trace_id }));
    }, undefined, { canaryEnabled: true });

    const first = service.testConnection({ providerId: "deepseek", capability: "fast_chat" });
    await started;
    const replayPending = service.testConnection({ providerId: "deepseek", capability: "fast_chat" });
    releaseRequest?.({
      status: "connected",
      provider_id: "deepseek",
      model: "deepseek/deepseek-flash",
      capability: "fast_chat",
      latency_ms: 1,
      safe_error_code: null,
      request_status: "http_response_received",
      failure_stage: "none",
      http_status: 200,
    });
    const [firstResult, replay] = await Promise.all([first, replayPending]);

    expect(requests).toBe(1);
    expect(firstResult.status).toBe("connected");
    expect(replay).toMatchObject({
      status: "blocked",
      safe_error_code: "MODEL_CONNECTION_TEST_DISABLED",
      request_status: "not_sent",
      failure_stage: "canary_gate",
    });
  });
});

  it.each([
    [401, "MODEL_CREDENTIAL_REJECTED"],
    [402, "MODEL_BALANCE_INSUFFICIENT"],
    [429, "MODEL_RATE_LIMITED"],
    [503, "MODEL_PROVIDER_SERVER_ERROR"],
  ])("preserves received HTTP status %i and safe code through Main IPC", async (httpStatus, safeErrorCode) => {
    const RuntimeProviderService = await loadRuntimeProviderService();
    const service = new RuntimeProviderService(async (_method, params) => ({
      status: "failed",
      provider_id: "deepseek",
      model: "deepseek/deepseek-flash",
      capability: "fast_chat",
      latency_ms: 12,
      trace_id: params.trace_id,
      safe_error_code: safeErrorCode,
      request_status: "http_response_received",
      failure_stage: "provider_http",
      http_status: httpStatus,
    }), undefined, { canaryEnabled: true });

    const result = await service.testConnection({ providerId: "deepseek", capability: "fast_chat" });
    expect(result).toMatchObject({
      status: "failed",
      safe_error_code: safeErrorCode,
      request_status: "http_response_received",
      failure_stage: "provider_http",
      http_status: httpStatus,
    });
    expect(result.trace_id).toMatch(/^[0-9a-f-]{36}$/i);
  });

  it("marks network and timeout failures as unknown when no HTTP response exists", async () => {
    const RuntimeProviderService = await loadRuntimeProviderService();
    for (const safeErrorCode of ["MODEL_NETWORK_ERROR", "MODEL_TIMEOUT"]) {
      const service = new RuntimeProviderService(async (_method, params) => ({
        status: "failed",
        provider_id: "deepseek",
        model: "deepseek/deepseek-flash",
        capability: "fast_chat",
        latency_ms: 0,
        trace_id: params.trace_id,
        safe_error_code: safeErrorCode,
        request_status: "unknown",
        failure_stage: "provider_transport",
        http_status: null,
      }), undefined, { canaryEnabled: true });
      const result = await service.testConnection({ providerId: "deepseek", capability: "fast_chat" });
      expect(result).toMatchObject({
        safe_error_code: safeErrorCode,
        request_status: "unknown",
        failure_stage: "provider_transport",
        http_status: null,
      });
    }
  });

  it("preserves the safe IPC_JSON_INVALID code and trace when Sidecar output is malformed", async () => {
    const RuntimeProviderService = await loadRuntimeProviderService();
    const reported: unknown[] = [];
    const service = new RuntimeProviderService(async (_method, _params, options) => {
      throw new RuntimeIpcError("IPC_JSON_INVALID", undefined, {
        traceId: options?.traceId,
        requestStatus: "unknown",
        failureStage: "sidecar_ipc",
        httpStatus: null,
      });
    }, (result) => reported.push(result), { canaryEnabled: true });

    const result = await service.testConnection({ providerId: "deepseek", capability: "fast_chat" });

    expect(result).toMatchObject({
      status: "failed",
      safe_error_code: "IPC_JSON_INVALID",
      request_status: "unknown",
      failure_stage: "sidecar_ipc",
      http_status: null,
    });
    expect(result.trace_id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(reported).toEqual([result]);
  });
  it("returns a trace-linked unknown result when the Sidecar exits during a provider request", async () => {
    const RuntimeProviderService = await loadRuntimeProviderService();
    let requestTraceId: string | undefined;
    let requests = 0;
    const service = new RuntimeProviderService(async (_method, _params, options) => {
      requests += 1;
      requestTraceId = options?.traceId;
      throw new RuntimeIpcError("SIDECAR_EXITED", undefined, {
        traceId: options?.traceId,
        requestStatus: "unknown",
        failureStage: "sidecar_ipc",
        httpStatus: null,
      });
    }, undefined, { canaryEnabled: true });

    const result = await service.testConnection({ providerId: "deepseek", capability: "fast_chat" });
    const replay = await service.testConnection({ providerId: "deepseek", capability: "fast_chat" });
    expect(result).toMatchObject({
      status: "failed",
      provider_id: "deepseek",
      model: "",
      capability: "fast_chat",
      latency_ms: 0,
      safe_error_code: "SIDECAR_EXITED",
      request_status: "unknown",
      failure_stage: "sidecar_ipc",
      http_status: null,
    });
    expect(requestTraceId).toMatch(/^[0-9a-f-]{36}$/i);
    expect(result.trace_id).toBe(requestTraceId);
    expect(requests).toBe(1);
    expect(replay).toMatchObject({
      status: "blocked",
      safe_error_code: "MODEL_CONNECTION_TEST_DISABLED",
      request_status: "not_sent",
      failure_stage: "canary_gate",
    });
  });

  it("uses one trace ID from Main IPC through the Sidecar request envelope", async () => {
    const RuntimeProviderService = await loadRuntimeProviderService();
    let requestParams: Record<string, unknown> | undefined;
    let requestOptions: Record<string, unknown> | undefined;
    const service = new RuntimeProviderService(async (_method, params, options) => {
      requestParams = params;
      requestOptions = options as unknown as Record<string, unknown>;
      return {
        status: "blocked",
        provider_id: "deepseek",
        model: "deepseek/deepseek-flash",
        capability: "fast_chat",
        latency_ms: 0,
        trace_id: params.trace_id,
        safe_error_code: "MODEL_CONNECTION_TEST_DISABLED",
        request_status: "not_sent",
        failure_stage: "canary_gate",
        http_status: null,
      };
    }, undefined, { canaryEnabled: true });

    const result = await service.testConnection({ providerId: "deepseek", capability: "fast_chat" });

    expect(requestParams?.trace_id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(requestOptions?.traceId).toBe(requestParams?.trace_id);
    expect(result.trace_id).toBe(requestParams?.trace_id);
  });
