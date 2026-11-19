import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { validateProtocol } from "../../packages/protocols/src/validate";

const here = dirname(fileURLToPath(import.meta.url));
const repository = resolve(here, "../..");
const require = createRequire(import.meta.url);

const ipcFrame = (messageType: string, payload: Record<string, unknown>) => ({
  protocol_name: "runtime-ipc",
  protocol_version: 1,
  message_id: "message-contract",
  request_id: "request-contract",
  trace_id: "trace-contract",
  message_type: messageType,
  payload,
});

describe("runtime IPC contract", () => {
  it("validates typed request, response, and cancel messages with the shared protocol schema", () => {
    expect(validateProtocol("runtime-ipc", ipcFrame("request", { method: "runtime.health", params: {} })).valid).toBe(true);
    expect(validateProtocol("runtime-ipc", ipcFrame("response", { result: { status: "healthy" } })).valid).toBe(true);
    expect(validateProtocol("runtime-ipc", ipcFrame("response", { error: {
      code: "IPC_METHOD_NOT_ALLOWED", category: "PERMISSION", message: "IPC method is not allowed",
      retryable: false, recoverable: false, severity: "ERROR", details: {}, trace_id: "trace-contract",
    } })).valid).toBe(true);
    expect(validateProtocol("runtime-ipc", ipcFrame("cancel", {})).valid).toBe(true);
    expect(validateProtocol("runtime-ipc", ipcFrame("request", { method: "runtime.health" })).valid).toBe(false);
    expect(validateProtocol("runtime-ipc", ipcFrame("execute", { command: "arbitrary" })).valid).toBe(false);
  });

  it("proves a real Electron Main process exchanges NDJSON with a real Python sidecar", () => {
    const electronPath = require("electron") as string;
    const electronApp = join(repository, "apps/desktop/tests/fixtures/electron-app");
    const sidecar = join(repository, "runtimes/python-worker/tests/fixtures/ipc_sidecar.py");
    const python = join(repository, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
    expect(existsSync(electronPath)).toBe(true);
    expect(existsSync(python)).toBe(true);

    const childEnv = { ...process.env };
    delete childEnv.ELECTRON_DISABLE_SANDBOX;
    const result = spawnSync(
      electronPath,
      // --disable-dev-shm-usage: /dev/shm exhaustion on Linux CI caused
      // intermittent SIGSEGV (run 43818777207, twice consecutively); the flag
      // is a no-op on Windows and macOS.
      ["--headless", "--disable-gpu", "--disable-dev-shm-usage", electronApp],
      {
        cwd: repository,
        encoding: "utf8",
        timeout: 90_000,
        env: {
          ...childEnv,
          SHULIU_TEST_PYTHON_EXECUTABLE: python,
          SHULIU_TEST_SIDECAR: sidecar,
          SHULIU_SECRET_SENTINEL: "sensitive-ipc-test-value",
          PYTHONPATH: join(repository, "runtimes/python-worker"),
        },
      },
    );
    const marker = result.stdout.split(/\r?\n/).find((line) => line.startsWith("SHULIU_IPC_RESULT:"));
    const diagnostics = `signal=${String(result.signal)} error=${String(result.error)} stderr=${result.stderr}\nstdout=${result.stdout}`;
    expect(result.status, diagnostics).toBe(0);
    expect(marker, diagnostics).toBeDefined();
    const report = JSON.parse(marker!.slice("SHULIU_IPC_RESULT:".length));
    expect(report).toMatchObject({
      health: { status: "healthy" },
      chat: {
        status: "succeeded",
        provider_id: "fixture",
        model: "fixture/chat",
        request_status: "http_response_received",
        response: "fixture reply",
      },
      concurrent: ["one", "two"],
      sidecarSecretVariablePresent: false,
      sidecarUtf8: true,
      timeoutCode: "IPC_REQUEST_TIMEOUT",
      cancellationCode: "IPC_REQUEST_CANCELLED",
      crashCode: "SIDECAR_EXITED",
      restartedHealth: { status: "healthy" },
      renderer: { nodeIntegration: false, arbitraryRequest: false, status: "healthy" },
      repeatedCrashCode: "SIDECAR_EXITED",
      repeatedCrashState: "safe_mode",
      protocolMismatchCode: "PROTOCOL_VERSION_UNSUPPORTED",
      secretAbsentFromLogs: true,
    });
    expect(report.electronNodeVersion).toBe("24.22.0");
  }, 100_000);
});
