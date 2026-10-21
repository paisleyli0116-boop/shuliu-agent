import { describe, expect, it, vi } from "vitest";
import { registerDesktopIpc } from "../../src/main/ipc/desktop-ipc";
import { createRendererApi } from "../../src/preload/api";

describe("desktop IPC allowlist", () => {
  it("exposes only fixed task, approval, health and provider methods", async () => {
    const calls: unknown[][] = [];
    const api = createRendererApi((...args: unknown[]) => {
      calls.push(args);
      return Promise.resolve({ configured: true });
    });

    await api.providers.saveProfile({ providerId: "openai-compatible", modelId: "fast-chat-demo", apiKey: "key-value" });

    expect(Object.keys(api).sort()).toEqual(["approvals", "providers", "runtime", "tasks"]);
    expect("execute" in api).toBe(false);
    expect("shell" in api).toBe(false);
    expect(calls).toEqual([[
      "shuliu:providers:save-profile",
      { providerId: "openai-compatible", modelId: "fast-chat-demo", apiKey: "key-value" },
    ]]);
  });

  it("rejects untrusted senders and forwards credentials only to the secret-store service", async () => {
    const handlers = new Map<string, (event: unknown, ...args: unknown[]) => unknown>();
    const ipcMain = {
      handle: vi.fn((channel, listener) => handlers.set(channel, listener)),
      removeHandler: vi.fn((channel: string) => handlers.delete(channel)),
    };
    const putCredential = vi.fn().mockResolvedValue({ configured: true });
    const services = {
      tasks: { list: vi.fn(), get: vi.fn(), create: vi.fn() },
      approvals: { list: vi.fn(), decide: vi.fn() },
      providers: {
        list: vi.fn(),
        testConnection: vi.fn(),
        saveProfile: putCredential,
        replaceCredential: vi.fn(),
      },
    };
    const unregister = registerDesktopIpc(ipcMain, services, (event) => event === "trusted");

    expect([...handlers.keys()].sort()).toEqual([
      "shuliu:approvals:decide",
      "shuliu:approvals:list",
      "shuliu:providers:list",
      "shuliu:providers:replace-credential",
      "shuliu:providers:save-profile",
      "shuliu:providers:test-connection",
      "shuliu:tasks:create",
      "shuliu:tasks:get",
      "shuliu:tasks:list",
    ]);
    await expect(handlers.get("shuliu:providers:save-profile")?.("untrusted", {
      providerId: "openai-compatible",
      modelId: "fast-chat-demo",
      apiKey: "secret-marker",
    })).rejects.toMatchObject({ code: "IPC_SENDER_FORBIDDEN" });
    await handlers.get("shuliu:providers:save-profile")?.("trusted", {
      providerId: "openai-compatible",
      modelId: "fast-chat-demo",
      apiKey: "secret-marker",
    });
    expect(putCredential).toHaveBeenCalledOnce();
    expect(putCredential.mock.calls[0][0]).toEqual({
      providerId: "openai-compatible",
      modelId: "fast-chat-demo",
      apiKey: "secret-marker",
    });
    unregister();
    expect(handlers.size).toBe(0);
  });
});
it("accepts a capability-scoped provider canary request", async () => {
  const handlers = new Map<string, (event: unknown, ...args: unknown[]) => unknown>();
  const providers = {
    list: vi.fn(),
    testConnection: vi.fn().mockResolvedValue({ status: "connected" }),
    saveProfile: vi.fn(),
    replaceCredential: vi.fn(),
  };
  const services = {
    tasks: { list: vi.fn(), get: vi.fn(), create: vi.fn() },
    approvals: { list: vi.fn(), decide: vi.fn() },
    providers,
  };
  registerDesktopIpc(
    { handle: vi.fn((channel, listener) => handlers.set(channel, listener)) },
    services,
    () => true,
  );

  await handlers.get("shuliu:providers:test-connection")?.("trusted", {
    providerId: "openai-compatible",
    capability: "fast_chat",
  });

  expect(providers.testConnection).toHaveBeenCalledWith({
    providerId: "openai-compatible",
    capability: "fast_chat",
  });
});
