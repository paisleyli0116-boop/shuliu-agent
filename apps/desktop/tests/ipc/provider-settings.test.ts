import { describe, expect, it } from "vitest";

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
          trace_id: "trace-test",
          safe_error_code: null,
        };
      }
      return { profile_id: "provider-a", configured: true, storage: "OS_SECURE_STORAGE" };
    });

    const catalog = await service.list();
    const connection = await service.testConnection({ providerId: "provider-a", capability: "fast_chat" });
    const saved = await service.saveProfile({
      providerId: "provider-a",
      modelId: "openai/gpt-4.1-mini",
      apiKey: "sk-provider-key-marker",
    });
    const replaced = await service.replaceCredential({ profileId: "provider-a", apiKey: "sk-replacement-marker" });

    expect(catalog.providers[0].id).toBe("provider-a");
    expect(connection).toMatchObject({ status: "connected", provider_id: "provider-a", trace_id: "trace-test" });
    expect(saved).toEqual({ profile_id: "provider-a", configured: true, storage: "OS_SECURE_STORAGE" });
    expect(replaced).toEqual({ profile_id: "provider-a", configured: true, storage: "OS_SECURE_STORAGE" });
    expect(calls.map(({ method }) => method)).toEqual([
      "providers.list",
      "providers.test_connection",
      "providers.save_profile",
      "providers.replace_credential",
    ]);
    expect(calls[1].params).toEqual({ provider_id: "provider-a", capability: "fast_chat" });
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

  it("rejects unrecognized provider error codes at the IPC boundary", async () => {
    const RuntimeProviderService = await loadRuntimeProviderService();
    const service = new RuntimeProviderService(async () => ({
      status: "failed",
      provider_id: "provider-a",
      model: "openai/gpt-4.1-mini",
      capability: "fast_chat",
      latency_ms: 42,
      trace_id: "trace-test",
      safe_error_code: "INTERNAL_SECRET_MARKER",
    }));

    const error = await service
      .testConnection({ providerId: "provider-a", capability: "fast_chat" })
      .catch((value: unknown) => value);

    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toBe("PROVIDER_RESPONSE_INVALID");
    expect((error as Error).message).not.toContain("INTERNAL_SECRET_MARKER");
  });
});
