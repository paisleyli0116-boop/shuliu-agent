import { describe, expect, it } from "vitest";

describe("provider secret redaction", () => {
  it("whitelists fields from Sidecar responses and never returns credentials", async () => {
    const module = await import("../../src/main/ipc/providers").catch(() => null);
    expect(module?.RuntimeProviderService).toBeTypeOf("function");
    const service = new module!.RuntimeProviderService(async (method) => {
      if (method === "providers.list") {
        return {
          providers: [{
            id: "provider-a",
            name: "Provider A",
            models: [{ id: "openai/gpt-4.1-mini", name: "Model A", capabilities: ["fast_chat"] }],
            selected_model_id: "openai/gpt-4.1-mini",
            api_key: "sk-leak-from-runtime",
          }],
          api_key: "sk-leak-from-runtime",
        };
      }
      return {
        profile_id: "provider-a",
        configured: true,
        storage: "OS_SECURE_STORAGE",
        api_key: "sk-leak-from-runtime",
      };
    });

    const catalog = await service.list();
    const saved = await service.saveProfile({
      providerId: "provider-a",
      modelId: "openai/gpt-4.1-mini",
      apiKey: "sk-input-marker",
    });

    expect(JSON.stringify(catalog)).not.toContain("sk-leak-from-runtime");
    expect(JSON.stringify(saved)).not.toContain("sk-leak-from-runtime");
    expect(JSON.stringify(saved)).not.toContain("sk-input-marker");
    expect(Object.keys(saved).sort()).toEqual(["configured", "profile_id", "storage"]);
  });

  it("masks Sidecar exception messages that could contain credential material", async () => {
    const module = await import("../../src/main/ipc/providers").catch(() => null);
    expect(module?.RuntimeProviderService).toBeTypeOf("function");
    const service = new module!.RuntimeProviderService(async () => {
      throw new Error("provider error sk-sensitive-marker");
    });

    await expect(service.list()).rejects.toMatchObject({ message: "PROVIDER_OPERATION_FAILED" });
  });
});
