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
