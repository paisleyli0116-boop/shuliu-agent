import { describe, expect, it, vi } from "vitest";
import { DesktopIpcError, registerDesktopIpc } from "../../src/main/ipc/desktop-ipc.js";
import { DESKTOP_IPC_CHANNELS } from "../../src/preload/api.js";

describe("desktop computer capability IPC", () => {
  it("allows only the trusted Renderer and does not accept caller-controlled parameters", async () => {
    const handlers = new Map<string, (event: unknown, ...args: unknown[]) => unknown>();
    const capability = vi.fn(async () => ({
      status: "available" as const,
      safe_error_code: null,
      trace_id: "00000000-0000-4000-8000-000000000001",
    }));
    const observeForeground = vi.fn(async () => ({
      status: "observed" as const,
      title: "Calculator",
      elements: [],
      safe_error_code: null,
      trace_id: "00000000-0000-4000-8000-000000000002",
    }));
    registerDesktopIpc(
      { handle: (channel, handler) => handlers.set(channel, handler) },
      {
        tasks: { list: async () => [], get: async () => null, create: async () => { throw new Error(); } },
        approvals: { list: async () => [], decide: async () => { throw new Error(); } },
        providers: {
          list: async () => ({ providers: [] }),
          testConnection: async () => { throw new Error(); },
          saveProfile: async () => { throw new Error(); },
          replaceCredential: async () => { throw new Error(); },
        },
        chat: { complete: async () => { throw new Error(); } },
        computer: { capability, observeForeground },
      },
      (event) => event === "trusted",
    );
    const invoke = handlers.get(DESKTOP_IPC_CHANNELS.computerCapabilityGet);

    await expect(invoke?.("trusted")).resolves.toMatchObject({ status: "available" });
    expect(capability).toHaveBeenCalledOnce();
    await expect(invoke?.("trusted", { organizationId: "attacker" })).rejects.toMatchObject({
      code: "DESKTOP_REQUEST_INVALID",
    } satisfies Partial<DesktopIpcError>);
    await expect(invoke?.("untrusted")).rejects.toMatchObject({
      code: "IPC_SENDER_FORBIDDEN",
    });
    expect(capability).toHaveBeenCalledOnce();
    await expect(handlers.get(DESKTOP_IPC_CHANNELS.computerObserveForeground)?.("trusted"))
      .resolves.toMatchObject({ status: "observed" });
    expect(observeForeground).toHaveBeenCalledOnce();
    await expect(handlers.get(DESKTOP_IPC_CHANNELS.computerObserveForeground)?.("trusted", { windowRef: "0x1234" }))
      .rejects.toMatchObject({ code: "DESKTOP_REQUEST_INVALID" });
    await expect(handlers.get(DESKTOP_IPC_CHANNELS.computerObserveForeground)?.("untrusted"))
      .rejects.toMatchObject({ code: "IPC_SENDER_FORBIDDEN" });
    expect(observeForeground).toHaveBeenCalledOnce();
  });
});
