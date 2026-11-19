import { describe, expect, it, vi } from "vitest";
import { registerDesktopIpc } from "../../src/main/ipc/desktop-ipc";
import { createRendererApi } from "../../src/preload/api";

describe("chat desktop IPC", () => {
  function setup() {
    const handlers = new Map<string, (event: unknown, ...args: unknown[]) => unknown>();
    const chat = { complete: vi.fn().mockResolvedValue({ status: "succeeded" }) };
    const services = {
      tasks: { list: vi.fn(), get: vi.fn(), create: vi.fn() },
      approvals: { list: vi.fn(), decide: vi.fn() },
      providers: { list: vi.fn(), testConnection: vi.fn(), saveProfile: vi.fn(), replaceCredential: vi.fn() },
      chat,
    };
    registerDesktopIpc({ handle: vi.fn((channel, listener) => handlers.set(channel, listener)) }, services, (event) => event === "trusted");
    return { handlers, chat };
  }

  it("exposes a single manual chat completion method through Preload", async () => {
    const calls: unknown[][] = [];
    const api = createRendererApi((...args) => {
      calls.push(args);
      return Promise.resolve({ status: "succeeded" });
    });
  expect(Object.keys(api).sort()).toEqual(["approvals", "chat", "computer", "providers", "runtime", "tasks"]);
    await api.chat.complete({
      providerId: "deepseek",
      modelId: "deepseek/deepseek-flash",
      messages: [{ role: "user", content: "hello" }],
      requestId: "11111111-2222-4333-8444-555555555555",
      traceId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      externalProcessingConfirmed: true,
    });
    expect(calls).toEqual([["shuliu:chat:complete", {
      providerId: "deepseek",
      modelId: "deepseek/deepseek-flash",
      messages: [{ role: "user", content: "hello" }],
      requestId: "11111111-2222-4333-8444-555555555555",
      traceId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      externalProcessingConfirmed: true,
    }]]);
  });

  it("validates the exact payload before invoking Sidecar and preserves structured results", async () => {
    const { handlers, chat } = setup();
    const invoke = handlers.get("shuliu:chat:complete");
    const payload = {
      providerId: "deepseek",
      modelId: "deepseek/deepseek-flash",
      messages: [{ role: "user", content: "hello" }],
      requestId: "11111111-2222-4333-8444-555555555555",
      traceId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      externalProcessingConfirmed: true,
    };
    await expect(invoke?.("trusted", { ...payload, permissions: ["admin"] })).rejects.toMatchObject({
      code: "DESKTOP_REQUEST_INVALID",
    });
    expect(chat.complete).not.toHaveBeenCalled();
    expect(await invoke?.("trusted", payload)).toEqual({ status: "succeeded" });
    expect(chat.complete).toHaveBeenCalledWith({
      providerId: "deepseek",
      modelId: "deepseek/deepseek-flash",
      messages: [{ role: "user", content: "hello" }],
      requestId: payload.requestId,
      traceId: payload.traceId,
      externalProcessingConfirmed: true,
    });
  });

  it("rejects untrusted senders and invalid or unconfirmed chat requests", async () => {
    const { handlers, chat } = setup();
    const invoke = handlers.get("shuliu:chat:complete");
    const payload = {
      providerId: "deepseek",
      modelId: "deepseek/deepseek-flash",
      messages: [{ role: "user", content: "hello" }],
      requestId: "11111111-2222-4333-8444-555555555555",
      traceId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      externalProcessingConfirmed: false,
    };
    await expect(invoke?.("untrusted", payload)).rejects.toMatchObject({ code: "IPC_SENDER_FORBIDDEN" });
    await expect(invoke?.("trusted", payload)).rejects.toMatchObject({ code: "DESKTOP_REQUEST_INVALID" });
    await expect(invoke?.("trusted", { ...payload, messages: [{ role: "system", content: "x" }] })).rejects.toMatchObject({
      code: "DESKTOP_REQUEST_INVALID",
    });
    expect(chat.complete).not.toHaveBeenCalled();
  });
});
