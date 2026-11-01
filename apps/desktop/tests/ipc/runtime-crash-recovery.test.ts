import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { RuntimeRecoveryCoordinator } from "../../src/main/runtime/recovery-coordinator";
import type { NdjsonChannel } from "../../src/main/runtime/ndjson-channel";
import { SidecarSupervisor } from "../../src/main/runtime/supervisor";

describe("Sidecar process restart recovery policy", () => {
  it("keeps recovery diagnostics allowlisted and distinguishes invalid results", async () => {
    const channel = {} as NdjsonChannel;
    const context = {
      previous_generation: 1,
      current_generation: 2,
      previous_exit_code: 17,
      previous_exit_kind: "process_crash",
      previous_error_code: null,
    };
    const recovery = new RuntimeRecoveryCoordinator(
      { max_restarts: 1, backoff: [0], health_timeout: 1 },
      async () => ({ status: "WAIT", error_code: "APPROVAL_PENDING" }),
    );
    const outcome = await recovery.recoverAfterRestart(channel, context);
    expect(outcome).toEqual({ status: "WAIT", safeErrorCode: "APPROVAL_PENDING" });

    const invalidRecovery = new RuntimeRecoveryCoordinator(
      { max_restarts: 1, backoff: [0], health_timeout: 1 },
      async () => ({ status: "WAIT", error_code: "private-path-secret" }),
    );
    const invalidOutcome = await invalidRecovery.recoverAfterRestart(channel, context);
    expect(invalidOutcome).toEqual({
      status: "SAFE_MODE",
      safeErrorCode: "RUNTIME_RECOVERY_RESULT_INVALID",
    });
    expect(JSON.stringify(invalidOutcome)).not.toContain("private-path-secret");
  });

  it("maps a thrown recovery error to a safe code without exposing exception text", async () => {
    const recovery = new RuntimeRecoveryCoordinator(
      { max_restarts: 1, backoff: [0], health_timeout: 1 },
      async () => { throw new Error("private connection string"); },
    );
    const outcome = await recovery.recoverAfterRestart({} as NdjsonChannel, {
      previous_generation: 1,
      current_generation: 2,
      previous_exit_code: 17,
      previous_exit_kind: "process_crash",
      previous_error_code: null,
    });

    expect(outcome).toEqual({ status: "SAFE_MODE", safeErrorCode: "RUNTIME_RECOVERY_FAILED" });
    expect(JSON.stringify(outcome)).not.toContain("private connection string");
  });

  it("keeps the validated restart budget immutable after construction", () => {
    const suppliedPolicy = {
      max_restarts: 1,
      backoff: [0.5],
      health_timeout: 2,
    };
    const recovery = new RuntimeRecoveryCoordinator(
      suppliedPolicy,
      async () => ({ status: "RESUME" }),
    );

    suppliedPolicy.max_restarts = 10;
    suppliedPolicy.backoff[0] = 0;

    expect(recovery.policy.max_restarts).toBe(1);
    expect(recovery.backoffMilliseconds(1)).toBe(500);
    expect(() => recovery.backoffMilliseconds(2)).toThrow(RangeError);
  });

  it("waits for configured backoff before spawning a replacement process", async () => {
    const directory = await mkdtemp(join(tmpdir(), "shuliu-recovery-backoff-"));
    const startsPath = join(directory, "starts.json");
    const child = [
      'const fs = require("node:fs");',
      'const readline = require("node:readline");',
      `const path = ${JSON.stringify(startsPath)};`,
      'const starts = fs.existsSync(path) ? JSON.parse(fs.readFileSync(path, "utf8")) : [];',
      'starts.push(Date.now()); fs.writeFileSync(path, JSON.stringify(starts), "utf8");',
      'const input = readline.createInterface({ input: process.stdin });',
      'input.on("line", (line) => {',
      '  const request = JSON.parse(line);',
      '  if (request.payload.method === "test.crash") { process.exit(17); return; }',
      '  const result = request.payload.method === "runtime.handshake" ? { protocol_version: 1, status: "ready" } : { status: "healthy" };',
      '  process.stdout.write(JSON.stringify({ protocol_name: "runtime-ipc", protocol_version: 1, message_id: "reply", request_id: request.request_id, trace_id: request.trace_id, message_type: "response", payload: { result } }) + "\\n");',
      '});',
    ].join("");
    const sidecar = new SidecarSupervisor({
      command: process.execPath,
      args: ["-e", child],
      cwd: process.cwd(),
      maxRestarts: 1,
      recovery: new RuntimeRecoveryCoordinator(
        { max_restarts: 1, backoff: [0.5], health_timeout: 2 },
        async () => ({ status: "RESUME" }),
      ),
    });

    try {
      await sidecar.start();
      await expect(sidecar.request("test.crash")).rejects.toMatchObject({ code: "SIDECAR_EXITED" });
      const deadline = Date.now() + 5_000;
      let starts: number[] = [];
      while (Date.now() < deadline) {
        try {
          starts = JSON.parse(await readFile(startsPath, "utf8")) as number[];
        } catch {
          starts = [];
        }
        if (starts.length >= 2 && sidecar.state === "ready") break;
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      expect(starts).toHaveLength(2);
      expect(starts[1] - starts[0]).toBeGreaterThanOrEqual(450);
      expect(sidecar.state).toBe("ready");
    } finally {
      await sidecar.close();
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("keeps a restarted Sidecar waiting when recovery returns WAIT", async () => {
    const directory = await mkdtemp(join(tmpdir(), "shuliu-recovery-gate-"));
    const startsPath = join(directory, "starts.json");
    const child = [
      'const fs = require("node:fs");',
      'const readline = require("node:readline");',
      `const path = ${JSON.stringify(startsPath)};`,
      'const starts = fs.existsSync(path) ? JSON.parse(fs.readFileSync(path, "utf8")) : [];',
      'starts.push(Date.now()); fs.writeFileSync(path, JSON.stringify(starts), "utf8");',
      'const input = readline.createInterface({ input: process.stdin });',
      'input.on("line", (line) => {',
      '  const request = JSON.parse(line);',
      '  if (request.payload.method === "test.crash") { process.exit(17); return; }',
      '  const result = request.payload.method === "runtime.handshake" ? { protocol_version: 1, status: "ready" } : { status: "healthy" };',
      '  process.stdout.write(JSON.stringify({ protocol_name: "runtime-ipc", protocol_version: 1, message_id: "reply", request_id: request.request_id, trace_id: request.trace_id, message_type: "response", payload: { result } }) + "\\n");',
      '});',
    ].join("");
    let recoveryCalls = 0;
    const sidecar = new SidecarSupervisor({
      command: process.execPath,
      args: ["-e", child],
      cwd: process.cwd(),
      maxRestarts: 1,
      recovery: new RuntimeRecoveryCoordinator(
        { max_restarts: 1, backoff: [0], health_timeout: 2 },
        async () => {
          recoveryCalls += 1;
          return recoveryCalls === 1
            ? { status: "WAIT", error_code: "APPROVAL_PENDING" }
            : { status: "RESUME" };
        },
      ),
    });

    try {
      await sidecar.start();
      await expect(sidecar.request("test.crash")).rejects.toMatchObject({ code: "SIDECAR_EXITED" });
      const deadline = Date.now() + 5_000;
      while (sidecar.state === "restarting" && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      expect(sidecar.state).toBe("waiting");
      await expect(sidecar.health()).resolves.toMatchObject({
        status: "degraded",
        code: "APPROVAL_PENDING",
      });
      await expect(sidecar.request("provider.call")).rejects.toMatchObject({
        code: "APPROVAL_PENDING",
      });
      expect(JSON.parse(await readFile(startsPath, "utf8"))).toHaveLength(2);
      await expect(Promise.all([
        sidecar.resumePendingRecovery(),
        sidecar.resumePendingRecovery(),
      ])).resolves.toEqual(["ready", "ready"]);
      expect(recoveryCalls).toBe(2);
      await expect(sidecar.health()).resolves.toMatchObject({ status: "healthy" });
    } finally {
      await sidecar.close();
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("bounds a stalled recovery callback and enters Safe Mode", async () => {
    const directory = await mkdtemp(join(tmpdir(), "shuliu-recovery-timeout-"));
    const startsPath = join(directory, "starts.json");
    const child = [
      'const fs = require("node:fs");',
      'const readline = require("node:readline");',
      `const path = ${JSON.stringify(startsPath)};`,
      'const starts = fs.existsSync(path) ? JSON.parse(fs.readFileSync(path, "utf8")) : [];',
      'starts.push(Date.now()); fs.writeFileSync(path, JSON.stringify(starts), "utf8");',
      'const input = readline.createInterface({ input: process.stdin });',
      'input.on("line", (line) => {',
      '  const request = JSON.parse(line);',
      '  if (request.payload.method === "test.crash") { process.exit(17); return; }',
      '  const result = request.payload.method === "runtime.handshake" ? { protocol_version: 1, status: "ready" } : { status: "healthy" };',
      '  process.stdout.write(JSON.stringify({ protocol_name: "runtime-ipc", protocol_version: 1, message_id: "reply", request_id: request.request_id, trace_id: request.trace_id, message_type: "response", payload: { result } }) + "\\n");',
      '});',
    ].join("");
    let recoveryAborted = false;
    let lateRecoveryCompleted = false;
    const sidecar = new SidecarSupervisor({
      command: process.execPath,
      args: ["-e", child],
      cwd: process.cwd(),
      handshakeTimeoutMs: 2_000,
      recovery: new RuntimeRecoveryCoordinator(
        { max_restarts: 1, backoff: [0], health_timeout: 0.05 },
        async (_channel, _context, signal) => new Promise((resolve) => {
          signal.addEventListener("abort", () => {
            recoveryAborted = true;
          }, { once: true });
          setTimeout(() => {
            lateRecoveryCompleted = true;
            resolve({ status: "RESUME" });
          }, 150);
        }),
      ),
    });

    try {
      await sidecar.start();
      await expect(sidecar.request("test.crash")).rejects.toMatchObject({ code: "SIDECAR_EXITED" });
      const deadline = Date.now() + 1_000;
      while (sidecar.state !== "safe_mode" && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      expect(sidecar.state).toBe("safe_mode");
      expect(sidecar.diagnostics.startupStage).toBe("recovery");
      expect(sidecar.diagnostics.handshakeStatus).toBe("succeeded");
      expect(recoveryAborted).toBe(true);
      expect(sidecar.diagnostics.safeErrorCode).toBe("RUNTIME_RECOVERY_TIMEOUT");
      expect(JSON.parse(await readFile(startsPath, "utf8"))).toHaveLength(2);
      const lateResultDeadline = Date.now() + 500;
      while (!lateRecoveryCompleted && Date.now() < lateResultDeadline) {
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      expect(lateRecoveryCompleted).toBe(true);
      expect(sidecar.state).toBe("safe_mode");
    } finally {
      await sidecar.close();
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("keeps an explicit stop when recovery resolves after close", async () => {
    const directory = await mkdtemp(join(tmpdir(), "shuliu-recovery-close-race-"));
    const startsPath = join(directory, "starts.json");
    const child = [
      'const fs = require("node:fs");',
      'const readline = require("node:readline");',
      `const path = ${JSON.stringify(startsPath)};`,
      'const starts = fs.existsSync(path) ? JSON.parse(fs.readFileSync(path, "utf8")) : [];',
      'starts.push(Date.now()); fs.writeFileSync(path, JSON.stringify(starts), "utf8");',
      'const input = readline.createInterface({ input: process.stdin });',
      'input.on("line", (line) => {',
      '  const request = JSON.parse(line);',
      '  if (request.payload.method === "test.crash") { process.exit(17); return; }',
      '  const result = request.payload.method === "runtime.handshake" ? { protocol_version: 1, status: "ready" } : { status: "healthy" };',
      '  process.stdout.write(JSON.stringify({ protocol_name: "runtime-ipc", protocol_version: 1, message_id: "reply", request_id: request.request_id, trace_id: request.trace_id, message_type: "response", payload: { result } }) + "\\n");',
      '});',
    ].join("");
    let resolveRecoveryStarted!: () => void;
    let resolveRecovery!: (result: unknown) => void;
    const recoveryStarted = new Promise<void>((resolve) => { resolveRecoveryStarted = resolve; });
    const pendingRecovery = new Promise<unknown>((resolve) => { resolveRecovery = resolve; });
    const sidecar = new SidecarSupervisor({
      command: process.execPath,
      args: ["-e", child],
      cwd: process.cwd(),
      recovery: new RuntimeRecoveryCoordinator(
        { max_restarts: 1, backoff: [0], health_timeout: 2 },
        async () => {
          resolveRecoveryStarted();
          return pendingRecovery;
        },
      ),
    });

    try {
      await sidecar.start();
      await expect(sidecar.request("test.crash")).rejects.toMatchObject({ code: "SIDECAR_EXITED" });
      await recoveryStarted;
      const closing = sidecar.close();
      resolveRecovery({ status: "WAIT" });
      await closing;
      await new Promise((resolve) => setTimeout(resolve, 10));
      expect(sidecar.state).toBe("stopped");
    } finally {
      await sidecar.close();
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("enters Safe Mode after the configured restart budget is exhausted", async () => {
    const directory = await mkdtemp(join(tmpdir(), "shuliu-recovery-budget-"));
    const startsPath = join(directory, "starts.json");
    const child = [
      'const fs = require("node:fs");',
      'const readline = require("node:readline");',
      `const path = ${JSON.stringify(startsPath)};`,
      'const starts = fs.existsSync(path) ? JSON.parse(fs.readFileSync(path, "utf8")) : [];',
      'starts.push(Date.now()); fs.writeFileSync(path, JSON.stringify(starts), "utf8");',
      'const input = readline.createInterface({ input: process.stdin });',
      'input.on("line", (line) => {',
      '  const request = JSON.parse(line);',
      '  if (request.payload.method === "test.crash") { process.exit(17); return; }',
      '  const result = request.payload.method === "runtime.handshake" ? { protocol_version: 1, status: "ready" } : { status: "healthy" };',
      '  process.stdout.write(JSON.stringify({ protocol_name: "runtime-ipc", protocol_version: 1, message_id: "reply", request_id: request.request_id, trace_id: request.trace_id, message_type: "response", payload: { result } }) + "\\n");',
      '});',
    ].join("");
    const sidecar = new SidecarSupervisor({
      command: process.execPath,
      args: ["-e", child],
      cwd: process.cwd(),
      recovery: new RuntimeRecoveryCoordinator(
        { max_restarts: 1, backoff: [0], health_timeout: 2 },
        async () => ({ status: "RESUME" }),
      ),
    });

    try {
      await sidecar.start();
      await expect(sidecar.request("test.crash")).rejects.toMatchObject({ code: "SIDECAR_EXITED" });
      const resumedDeadline = Date.now() + 5_000;
      while (sidecar.state === "restarting" && Date.now() < resumedDeadline) {
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      expect(sidecar.state).toBe("ready");

      await expect(sidecar.request("test.crash")).rejects.toMatchObject({ code: "SIDECAR_EXITED" });
      const safeModeDeadline = Date.now() + 5_000;
      while (sidecar.state !== "safe_mode" && Date.now() < safeModeDeadline) {
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      expect(sidecar.state).toBe("safe_mode");
      expect(JSON.parse(await readFile(startsPath, "utf8"))).toHaveLength(2);
    } finally {
      await sidecar.close();
      await rm(directory, { recursive: true, force: true });
    }
  });
});
