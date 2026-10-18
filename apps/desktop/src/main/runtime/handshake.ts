import { RuntimeIpcError } from "./ndjson-channel";

export type RuntimeHandshake = {
  protocol_version: number;
  status: string;
};

export function assertRuntimeHandshake(value: Record<string, unknown>): RuntimeHandshake {
  if (value.protocol_version !== 1) {
    throw new RuntimeIpcError("PROTOCOL_VERSION_UNSUPPORTED");
  }
  if (value.status !== "ready") {
    throw new RuntimeIpcError("IPC_HANDSHAKE_FAILED");
  }
  return { protocol_version: 1, status: "ready" };
}
