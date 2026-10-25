import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { SidecarSupervisor } from "../../src/main/runtime/supervisor";

describe("SidecarSupervisor health diagnostics", () => {
  it("retains the safe Python startup code and process exit status when handshake fails", async () => {
    const sidecar = new SidecarSupervisor({
      command: process.execPath,
      args: [
        "-e",
        [
          'process.stderr.write("event=runtime_bootstrap_failed code=DATABASE_SCHEMA_INCOMPATIBLE\\n");',
          'process.stderr.write("raw exception token=sk-test-private-marker C:\\\\Users\\\\private\\\\data\\n");',
          "process.exit(7);",
        ].join(""),
      ],
      cwd: process.cwd(),
      maxRestarts: 0,
    });

    try {
      await expect(sidecar.start()).rejects.toMatchObject({ code: "SIDECAR_EXITED" });
      expect(sidecar.diagnostics).toEqual({
        runtimeState: "safe_mode",
        startupStage: "sidecar_exit",
        handshakeStatus: "failed",
        failureStage: "ipc_handshake",
        exitKind: "unexpected_exit",
        sidecarExitCode: 7,
        sidecarSignal: null,
        safeErrorCode: "SIDECAR_EXITED",
        sidecarLogEvent: "runtime_bootstrap_failed",
        sidecarLogCode: "DATABASE_SCHEMA_INCOMPATIBLE",
      });
      expect(JSON.stringify(sidecar.diagnostics)).not.toMatch(/sk-test-private-marker|Users|raw exception|token=/i);
    } finally {
      await sidecar.close();
    }
  });

  it("classifies a Windows NTSTATUS-style Sidecar crash without guessing request delivery", async () => {
    const child = [
      'const readline = require("node:readline");',
      'const input = readline.createInterface({ input: process.stdin });',
      'input.on("line", (line) => {',
      '  const request = JSON.parse(line);',
      '  if (request.payload.method === "runtime.handshake") {',
      '    process.stdout.write(JSON.stringify({ protocol_name: "runtime-ipc", protocol_version: 1, message_id: "reply", request_id: request.request_id, trace_id: request.trace_id, message_type: "response", payload: { result: { protocol_version: 1, status: "ready" } } }) + "\\n");',
      '    return;',
      '  }',
      '  process.exit(0xc0000005);',
      '});',
    ].join("");
    const sidecar = new SidecarSupervisor({
      command: process.execPath,
      args: ["-e", child],
      cwd: process.cwd(),
      maxRestarts: 0,
    });

    try {
      await sidecar.start();
      await expect(sidecar.request("providers.test_connection", {}, { traceId: "11111111-2222-4333-8444-555555555555" }))
        .rejects.toMatchObject({ code: "SIDECAR_EXITED", requestStatus: "unknown", traceId: "11111111-2222-4333-8444-555555555555" });
      const deadline = Date.now() + 1_000;
      while (sidecar.diagnostics.exitKind === "none" && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
      expect(sidecar.diagnostics).toMatchObject({
        exitKind: "process_crash",
        sidecarExitCode: 0xc0000005,
        safeErrorCode: "SIDECAR_EXITED",
      });
    } finally {
      await sidecar.close();
    }
  });

  it("ignores unrecognized Sidecar log content rather than retaining it", async () => {
    const sidecar = new SidecarSupervisor({
      command: process.execPath,
      args: [
        "-e",
        [
          'process.stderr.write("event=provider_configuration_failed code=sk-test-private-marker\\n");',
          'process.stderr.write("Bearer secret-value\\n");',
          "process.exit(8);",
        ].join(""),
      ],
      cwd: process.cwd(),
      maxRestarts: 0,
    });

    try {
      await expect(sidecar.start()).rejects.toMatchObject({ code: "SIDECAR_EXITED" });
      expect(sidecar.diagnostics.sidecarLogEvent).toBeNull();
      expect(sidecar.diagnostics.sidecarLogCode).toBeNull();
      expect(JSON.stringify(sidecar.diagnostics)).not.toMatch(/sk-test-private-marker|Bearer|secret-value/);
    } finally {
      await sidecar.close();
    }
  });

  it("clears prior-generation diagnostics after repeated restarts recover", async () => {
    const directory = await mkdtemp(join(tmpdir(), "shuliu-sidecar-generation-"));
    const countPath = join(directory, "launch-count");
    await writeFile(countPath, "0", "utf8");
    const child = [
      'const fs = require("node:fs");',
      'const readline = require("node:readline");',
      `const countPath = ${JSON.stringify(countPath)};`,
      'const count = Number(fs.readFileSync(countPath, "utf8")) + 1;',
      'fs.writeFileSync(countPath, String(count), "utf8");',
      'if (count < 3) process.stderr.write("event=runtime_bootstrap_failed code=DATABASE_SCHEMA_INCOMPATIBLE\\n");',
      'const input = readline.createInterface({ input: process.stdin });',
      'input.on("line", (line) => {',
      '  const request = JSON.parse(line);',
      '  const result = request.payload.method === "runtime.handshake" ? { protocol_version: 1, status: "ready" } : { status: "healthy" };',
      '  process.stdout.write(JSON.stringify({ protocol_name: "runtime-ipc", protocol_version: 1, message_id: "reply", request_id: request.request_id, trace_id: request.trace_id, message_type: "response", payload: { result } }) + "\\n");',
      '});',
      'if (count < 3) setTimeout(() => process.exit(count + 7), 80);',
    ].join("");
    const sidecar = new SidecarSupervisor({
      command: process.execPath,
      args: ["-e", child],
      cwd: process.cwd(),
      maxRestarts: 2,
      handshakeTimeoutMs: 2_000,
    });

    try {
      await sidecar.start();
      const deadline = Date.now() + 5_000;
      while (Date.now() < deadline) {
        const count = Number(await readFile(countPath, "utf8"));
        if (count >= 3 && sidecar.state === "ready") break;
        await new Promise((resolve) => setTimeout(resolve, 10));
      }

      expect(Number(await readFile(countPath, "utf8"))).toBe(3);
      expect(sidecar.state).toBe("ready");
      expect(sidecar.diagnostics).toEqual({
        runtimeState: "ready",
        startupStage: "ready",
        handshakeStatus: "succeeded",
        failureStage: "none",
        exitKind: "none",
        sidecarExitCode: null,
        sidecarSignal: null,
        safeErrorCode: null,
        sidecarLogEvent: null,
        sidecarLogCode: null,
      });
    } finally {
      await sidecar.close();
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("forwards the canary opt-in to the Sidecar only when explicitly present", async () => {
    const child = [
      'const readline = require("node:readline");',
      'const input = readline.createInterface({ input: process.stdin });',
      'input.on("line", (line) => {',
      '  const request = JSON.parse(line);',
      '  const result = request.payload.method === "runtime.handshake"',
      '    ? { protocol_version: 1, status: "ready" }',
      '    : { status: "healthy", canaryEnabled: process.env.SHULIU_MODEL_CONNECTION_TEST === "1" };',
      '  process.stdout.write(JSON.stringify({ protocol_name: "runtime-ipc", protocol_version: 1, message_id: "reply", request_id: request.request_id, trace_id: request.trace_id, message_type: "response", payload: { result } }) + "\\n");',
      '});',
    ].join("");
    const denied = new SidecarSupervisor({
      command: process.execPath,
      args: ["-e", child],
      cwd: process.cwd(),
      env: {},
    });
    const enabled = new SidecarSupervisor({
      command: process.execPath,
      args: ["-e", child],
      cwd: process.cwd(),
      env: { SHULIU_MODEL_CONNECTION_TEST: "1" },
    });

    try {
      await denied.start();
      await enabled.start();
      expect((await denied.health()).canaryEnabled).toBe(false);
      expect((await enabled.health()).canaryEnabled).toBe(true);
    } finally {
      await denied.close();
      await enabled.close();
    }
  });

  it("reports a spawn failure as the current startup generation", async () => {
    const sidecar = new SidecarSupervisor({
      command: join(tmpdir(), `shuliu-missing-sidecar-${Date.now()}.exe`),
      args: [],
      cwd: process.cwd(),
      maxRestarts: 0,
    });

    await expect(sidecar.start()).rejects.toMatchObject({ code: "SIDECAR_SPAWN_FAILED" });
    expect(sidecar.diagnostics).toMatchObject({
      runtimeState: "safe_mode",
      startupStage: "sidecar_spawn",
      handshakeStatus: "not_started",
      failureStage: "sidecar_spawn",
      sidecarExitCode: null,
      safeErrorCode: "SIDECAR_SPAWN_FAILED",
    });
    await sidecar.close();
  });
});

  it("records an explicitly closed Sidecar as controlled, not as a runtime failure", async () => {
    const child = [
      'const readline = require("node:readline");',
      'const input = readline.createInterface({ input: process.stdin });',
      'input.on("line", (line) => {',
      '  const request = JSON.parse(line);',
      '  const result = request.payload.method === "runtime.handshake"',
      '    ? { protocol_version: 1, status: "ready" }',
      '    : { status: "healthy" };',
      '  process.stdout.write(JSON.stringify({ protocol_name: "runtime-ipc", protocol_version: 1, message_id: "reply", request_id: request.request_id, trace_id: request.trace_id, message_type: "response", payload: { result } }) + "\\n");',
      '});',
    ].join("");
    const sidecar = new SidecarSupervisor({
      command: process.execPath,
      args: ["-e", child],
      cwd: process.cwd(),
      maxRestarts: 0,
    });

    await sidecar.start();
    await sidecar.close();

    expect(sidecar.diagnostics).toMatchObject({
      runtimeState: "stopped",
      exitKind: "controlled",
      failureStage: "none",
      safeErrorCode: null,
    });
  });

  it("distinguishes supervisor termination after an IPC protocol failure from a process crash", async () => {
    const child = [
      'const readline = require("node:readline");',
      'const input = readline.createInterface({ input: process.stdin });',
      'input.on("line", (line) => {',
      '  const request = JSON.parse(line);',
      '  if (request.payload.method === "runtime.handshake") {',
      '    process.stdout.write(JSON.stringify({ protocol_name: "runtime-ipc", protocol_version: 1, message_id: "reply", request_id: request.request_id, trace_id: request.trace_id, message_type: "response", payload: { result: { protocol_version: 1, status: "ready" } } }) + "\\n");',
      '    return;',
      '  }',
      '  process.stdout.write("not-json\\n");',
      '});',
    ].join("");
    const sidecar = new SidecarSupervisor({
      command: process.execPath,
      args: ["-e", child],
      cwd: process.cwd(),
      maxRestarts: 0,
    });

    try {
      await sidecar.start();
      await expect(sidecar.request("providers.test_connection", {}, {
        traceId: "11111111-2222-4333-8444-555555555555",
      })).rejects.toMatchObject({
        code: "IPC_JSON_INVALID",
        requestStatus: "unknown",
        traceId: "11111111-2222-4333-8444-555555555555",
      });
      const deadline = Date.now() + 1_000;
      while (sidecar.diagnostics.exitKind === "none" && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
      expect(sidecar.diagnostics).toMatchObject({
        exitKind: "controlled",
        safeErrorCode: "IPC_JSON_INVALID",
      });
    } finally {
      await sidecar.close();
    }
  });

  it("preserves the request trace and classifies an unexpected Sidecar exception safely", async () => {
    const child = [
      'const readline = require("node:readline");',
      'const input = readline.createInterface({ input: process.stdin });',
      'input.on("line", (line) => {',
      '  const request = JSON.parse(line);',
      '  if (request.payload.method === "runtime.handshake") {',
      '    process.stdout.write(JSON.stringify({ protocol_name: "runtime-ipc", protocol_version: 1, message_id: "reply", request_id: request.request_id, trace_id: request.trace_id, message_type: "response", payload: { result: { protocol_version: 1, status: "ready" } } }) + "\\n");',
      '    return;',
      '  }',
      '  process.stderr.write("event=runtime_unhandled_exception code=RUNTIME_UNHANDLED_EXCEPTION\\n");',
      '  process.exit(1);',
      '});',
    ].join("");
    const persistedDiagnostics: Array<Record<string, unknown>> = [];
    const sidecar = new SidecarSupervisor({
      command: process.execPath,
      args: ["-e", child],
      cwd: process.cwd(),
      maxRestarts: 0,
      onDiagnostic: (diagnostics) => persistedDiagnostics.push({ ...diagnostics }),
    });

    try {
      await sidecar.start();
      await expect(sidecar.request("providers.test_connection", {}, { traceId: "11111111-2222-4333-8444-555555555555" }))
        .rejects.toMatchObject({ code: "SIDECAR_EXITED", traceId: "11111111-2222-4333-8444-555555555555" });
      const deadline = Date.now() + 1_000;
      while (sidecar.state !== "safe_mode" && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
      expect(sidecar.diagnostics).toMatchObject({
        exitKind: "unhandled_exception",
        sidecarExitCode: 1,
        safeErrorCode: "SIDECAR_EXITED",
        sidecarLogEvent: "runtime_unhandled_exception",
        sidecarLogCode: "RUNTIME_UNHANDLED_EXCEPTION",
      });
      expect(JSON.stringify(sidecar.diagnostics)).not.toMatch(/secret|traceback|exception text/i);
      expect(persistedDiagnostics).toContainEqual(expect.objectContaining({
        exitKind: "unhandled_exception",
        sidecarExitCode: 1,
        sidecarLogCode: "RUNTIME_UNHANDLED_EXCEPTION",
      }));
    } finally {
      await sidecar.close();
    }
  });
