import { PassThrough } from "node:stream";
import { describe, expect, it } from "vitest";
import { NdjsonChannel, RuntimeIpcError } from "../../src/main/runtime/ndjson-channel";
import { registerRuntimeIpc } from "../../src/main/runtime/ipc";
import { createRendererApi } from "../../src/preload/api";

const message = (requestId: string, payload: Record<string, unknown>, messageType = "response", traceId = "trace-test") => ({
  protocol_name: "runtime-ipc",
  protocol_version: 1,
  message_id: `message-${requestId}`,
  request_id: requestId,
  trace_id: traceId,
  message_type: messageType,
  payload,
});

describe("Electron/Python NDJSON IPC", () => {
  it("frames partial stdout lines and correlates concurrent responses by request_id", async () => {
    const stdout = new PassThrough();
    const stdin = new PassThrough();
    const channel = new NdjsonChannel(stdout, stdin);

    const first = channel.request("runtime.health", {});
    const second = channel.request("runtime.health", {});
    const requests = stdin.read()?.toString("utf8").trim().split("\n").map(JSON.parse) ?? [];
    expect(requests).toHaveLength(2);

    const later = JSON.stringify(message(requests[1].request_id, { result: { status: "健康" } }, "response", requests[1].trace_id));
    const earlier = JSON.stringify(message(requests[0].request_id, { result: { status: "ready" } }, "response", requests[0].trace_id));
    const encoded = Buffer.from(later, "utf8");
    const splitAt = encoded.indexOf(Buffer.from("健康")) + 1;
    stdout.write(encoded.subarray(0, splitAt));
    stdout.write(Buffer.concat([encoded.subarray(splitAt), Buffer.from(`\n${earlier}\n`)]));

    await expect(first).resolves.toEqual({ status: "ready" });
    await expect(second).resolves.toEqual({ status: "健康" });
    channel.close();
  });

  it("times out and cancels a request without retrying it", async () => {
    const stdout = new PassThrough();
    const stdin = new PassThrough();
    const channel = new NdjsonChannel(stdout, stdin);
    const request = channel.request("runtime.health", {}, { timeoutMs: 10 });

    await expect(request).rejects.toMatchObject({ code: "IPC_REQUEST_TIMEOUT" });
    const written = stdin.read()?.toString("utf8").trim().split("\n").map(JSON.parse) ?? [];
    expect(written.map((item: { message_type: string }) => item.message_type)).toEqual(["request", "cancel"]);
    expect(written[0].request_id).toBe(written[1].request_id);
    channel.close();
  });

  it("fails closed on malformed JSON, invalid schema, and protocol incompatibility", async () => {
    const stdout = new PassThrough();
    const stdin = new PassThrough();
    const channel = new NdjsonChannel(stdout, stdin);
    const pending = channel.request("runtime.health", {});
    stdin.read();
    stdout.write("not-json\n");
    await expect(pending).rejects.toBeInstanceOf(RuntimeIpcError);
    await expect(channel.request("runtime.health", {})).rejects.toMatchObject({ code: "IPC_CHANNEL_CLOSED" });

    const incompatibleStdout = new PassThrough();
    const incompatibleStdin = new PassThrough();
    const incompatible = new NdjsonChannel(incompatibleStdout, incompatibleStdin);
    const handshake = incompatible.request("runtime.handshake", {});
    const outbound = JSON.parse(incompatibleStdin.read().toString("utf8"));
    incompatibleStdout.write(`${JSON.stringify({ ...message(outbound.request_id, { result: {} }, "response", outbound.trace_id), protocol_version: 2 })}\n`);
    await expect(handshake).rejects.toMatchObject({ code: "PROTOCOL_VERSION_UNSUPPORTED" });
  });

  it("exposes only the typed health API and rejects untrusted Main IPC senders", async () => {
    const invokeCalls: unknown[][] = [];
    const api = createRendererApi((...args: unknown[]) => {
      invokeCalls.push(args);
      return Promise.resolve({ status: "healthy" });
    });
    await expect(api.runtime.health.get()).resolves.toEqual({ status: "healthy" });
    expect(invokeCalls).toEqual([["shuliu:runtime:health:get"]]);
    expect(Object.keys(api).sort()).toEqual(["approvals", "chat", "computer", "providers", "runtime", "tasks"]);

    const handlers = new Map<string, (event: unknown, ...args: unknown[]) => unknown>();
    const ipcMain = { handle: (channel: string, handler: (event: unknown, ...args: unknown[]) => unknown) => handlers.set(channel, handler) };
    registerRuntimeIpc(ipcMain, { health: async () => ({ status: "healthy" }) }, () => false);
    expect([...handlers.keys()]).toEqual(["shuliu:runtime:health:get"]);
    await expect(handlers.get("shuliu:runtime:health:get")?.({})).rejects.toMatchObject({ code: "IPC_SENDER_FORBIDDEN" });
  });
});
