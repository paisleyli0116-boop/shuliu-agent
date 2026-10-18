import { RuntimeIpcError } from "./ndjson-channel";

export const RUNTIME_HEALTH_CHANNEL = "shuliu:runtime:health:get";

export type RuntimeHealth = { status: "healthy" | "degraded" | "safe_mode" } & Record<string, unknown>;
export type RuntimeHealthReader = { health(): Promise<RuntimeHealth> };
export type IpcMainLike = {
  handle(channel: string, listener: (event: unknown, ...args: unknown[]) => unknown): void;
  removeHandler?(channel: string): void;
};

export function registerRuntimeIpc(
  ipcMain: IpcMainLike,
  supervisor: RuntimeHealthReader,
  isTrustedSender: (event: unknown) => boolean,
): () => void {
  ipcMain.handle(RUNTIME_HEALTH_CHANNEL, async (event, ...args) => {
    if (!isTrustedSender(event)) throw new RuntimeIpcError("IPC_SENDER_FORBIDDEN");
    if (args.length > 0) throw new RuntimeIpcError("IPC_METHOD_NOT_ALLOWED");
    return supervisor.health();
  });
  return () => ipcMain.removeHandler?.(RUNTIME_HEALTH_CHANNEL);
}
