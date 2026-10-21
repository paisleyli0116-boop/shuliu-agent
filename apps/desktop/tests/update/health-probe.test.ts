import { describe, expect, it, vi } from "vitest";
import { runHealthProbe } from "../../src/main/health-probe";

describe("installed desktop health probe", () => {
  const versions = { appVersion: "0.1.1", electronVersion: "44.9.0", nodeVersion: "24.22.0" };

  it("reports packed Electron, Node, app version, preload, and database health", async () => {
    const write = vi.fn();
    const exitCode = await runHealthProbe({
      ...versions,
      startRuntime: vi.fn(async () => undefined),
      runtimeDiagnostics: () => ({
        runtimeState: "ready",
        startupStage: "ready",
        handshakeStatus: "succeeded",
        failureStage: "none",
        sidecarExitCode: null,
        sidecarSignal: null,
        safeErrorCode: null,
        sidecarLogEvent: null,
        sidecarLogCode: null,
      }),
      inspectRenderer: vi.fn(async () => ({
        preloadAvailable: true,
        health: { status: "healthy" as const, components: { database: { schema_version: "1.0.0" } } },
      })),
      write,
    });

    expect(exitCode).toBe(0);
    expect(JSON.parse(write.mock.calls[0]?.[0] ?? "{}")).toEqual({
      status: "healthy" as const,
      app_version: "0.1.1",
      electron_version: "44.9.0",
      node_version: "24.22.0",
      preload_available: true,
      database_schema: "1.0.0",
      runtime_diagnostics: {
        runtime_state: "ready",
        startup_stage: "ready",
        ipc_handshake_status: "succeeded",
        failure_stage: "none",
        sidecar_exit_code: null,
        sidecar_signal: null,
        safe_error_code: null,
        sidecar_log_event: null,
        sidecar_log_code: null,
      },
    });
  });

  it("reports Safe Mode after Sidecar startup failure without exposing exception text", async () => {
    const write = vi.fn();
    const exitCode = await runHealthProbe({
      ...versions,
      startRuntime: async () => { throw new Error("secret path or credential"); },
      runtimeDiagnostics: () => ({
        runtimeState: "safe_mode",
        startupStage: "sidecar_exit",
        handshakeStatus: "failed",
        failureStage: "ipc_handshake",
        sidecarExitCode: 1,
        sidecarSignal: null,
        safeErrorCode: "SIDECAR_EXITED",
        sidecarLogEvent: "provider_configuration_failed",
        sidecarLogCode: "MODEL_PROVIDER_CONFIGURATION_INVALID",
      }),
      inspectRenderer: async () => ({ preloadAvailable: true, health: { status: "safe_mode" as const } }),
      write,
    });

    expect(exitCode).toBe(2);
    expect(JSON.parse(write.mock.calls[0]?.[0] ?? "{}")).toMatchObject({
      status: "safe_mode",
      app_version: "0.1.1",
      code: "RUNTIME_SAFE_MODE",
      preload_available: true,
      runtime_diagnostics: {
        runtime_state: "safe_mode",
        startup_stage: "sidecar_exit",
        ipc_handshake_status: "failed",
        failure_stage: "ipc_handshake",
        sidecar_exit_code: 1,
        sidecar_signal: null,
        safe_error_code: "SIDECAR_EXITED",
        sidecar_log_event: "provider_configuration_failed",
        sidecar_log_code: "MODEL_PROVIDER_CONFIGURATION_INVALID",
      },
    });
    expect(write.mock.calls[0]?.[0]).not.toContain("secret path or credential");
  });

  it("captures Sidecar diagnostics after renderer health inspection completes", async () => {
    const write = vi.fn();
    const diagnostics = {
      runtimeState: "ready" as const,
      startupStage: "ready" as const,
      handshakeStatus: "succeeded" as const,
      failureStage: "none" as const,
      sidecarExitCode: null as number | null,
      sidecarSignal: null as string | null,
      safeErrorCode: null as string | null,
      sidecarLogEvent: null as string | null,
      sidecarLogCode: null as string | null,
    };
    await runHealthProbe({
      ...versions,
      startRuntime: async () => undefined,
      runtimeDiagnostics: () => ({ ...diagnostics }),
      inspectRenderer: async () => {
        Object.assign(diagnostics, {
          runtimeState: "safe_mode",
          startupStage: "sidecar_exit",
          handshakeStatus: "failed",
          failureStage: "sidecar_exit",
          sidecarExitCode: 9,
          safeErrorCode: "SIDECAR_EXITED",
        });
        return { preloadAvailable: true, health: { status: "safe_mode" as const } };
      },
      write,
    });

    expect(JSON.parse(write.mock.calls[0]?.[0] ?? "{}").runtime_diagnostics).toEqual({
      runtime_state: "safe_mode",
      startup_stage: "sidecar_exit",
      ipc_handshake_status: "failed",
      failure_stage: "sidecar_exit",
      sidecar_exit_code: 9,
      sidecar_signal: null,
      safe_error_code: "SIDECAR_EXITED",
      sidecar_log_event: null,
      sidecar_log_code: null,
    });
  });

  it("replaces unrecognized runtime diagnostic values before writing the health report", async () => {
    const write = vi.fn();
    await runHealthProbe({
      ...versions,
      startRuntime: async () => { throw new Error("secret exception"); },
      runtimeDiagnostics: () => ({
        runtimeState: "token=secret",
        startupStage: "C:\\Users\\private",
        handshakeStatus: "Bearer secret",
        failureStage: "/private/path",
        sidecarExitCode: "sk-test-api-key",
        sidecarSignal: "secret signal",
        safeErrorCode: "sk-test-error",
        sidecarLogEvent: "credential=value",
        sidecarLogCode: "secret value",
      }),
      inspectRenderer: async () => ({ preloadAvailable: true, health: { status: "safe_mode" as const } }),
      write,
    });

    const output = write.mock.calls[0]?.[0] ?? "";
    expect(JSON.parse(output).runtime_diagnostics).toEqual({
      runtime_state: "unknown",
      startup_stage: "unknown",
      ipc_handshake_status: "unknown",
      failure_stage: "unknown",
      sidecar_exit_code: null,
      sidecar_signal: null,
      safe_error_code: "UNKNOWN",
      sidecar_log_event: "UNKNOWN",
      sidecar_log_code: "UNKNOWN",
    });
    expect(output).not.toMatch(/token=secret|private|Bearer|sk-test|credential=value|secret value/);
  });

  it("fails if preload is unavailable and emits only a stable code", async () => {
    const write = vi.fn();
    const exitCode = await runHealthProbe({
      ...versions,
      startRuntime: vi.fn(async () => undefined),
      runtimeDiagnostics: () => ({
        runtimeState: "ready",
        startupStage: "ready",
        handshakeStatus: "succeeded",
        failureStage: "none",
        sidecarExitCode: null,
        sidecarSignal: null,
        safeErrorCode: null,
        sidecarLogEvent: null,
        sidecarLogCode: null,
      }),
      inspectRenderer: vi.fn(async () => ({ preloadAvailable: false, health: { status: "healthy" as const } })),
      write,
    });

    expect(exitCode).toBe(3);
    expect(JSON.parse(write.mock.calls[0]?.[0] ?? "{}")).toMatchObject({
      status: "unhealthy",
      app_version: "0.1.1",
      code: "PRELOAD_UNAVAILABLE",
    });
  });
});
