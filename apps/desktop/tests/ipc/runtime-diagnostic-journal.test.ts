import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { appendProviderConnectionDiagnostic, appendRuntimeDiagnostic } from "../../src/main/runtime/diagnostic-journal";
import { sanitizeRuntimeDiagnostics } from "../../src/main/runtime/diagnostics";

const directories: string[] = [];

afterEach(async () => {
  await Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

describe("safe runtime diagnostic journal", () => {
  it("preserves the recovery waiting state and stage through diagnostic sanitization", () => {
    expect(sanitizeRuntimeDiagnostics({
      runtimeState: "waiting",
      startupStage: "recovery",
      handshakeStatus: "succeeded",
      failureStage: "none",
      exitKind: "process_crash",
      sidecarExitCode: 17,
      sidecarSignal: null,
      safeErrorCode: "RUNTIME_RECOVERY_WAITING",
      sidecarLogEvent: null,
      sidecarLogCode: null,
    })).toMatchObject({
      runtime_state: "waiting",
      startup_stage: "recovery",
      safe_error_code: "RUNTIME_RECOVERY_WAITING",
    });
    expect(sanitizeRuntimeDiagnostics({
      runtimeState: "waiting",
      startupStage: "recovery",
      handshakeStatus: "succeeded",
      failureStage: "none",
      exitKind: "process_crash",
      sidecarExitCode: 17,
      sidecarSignal: null,
      safeErrorCode: "APPROVAL_PENDING",
      sidecarLogEvent: null,
      sidecarLogCode: null,
    }).safe_error_code).toBe("APPROVAL_PENDING");
  });

  it("persists only allowlisted Sidecar exit and provider request evidence", async () => {
    const directory = await mkdtemp(join(tmpdir(), "shuliu-runtime-journal-"));
    directories.push(directory);
    await appendRuntimeDiagnostic(directory, {
      runtimeState: "safe_mode",
      startupStage: "sidecar_exit",
      handshakeStatus: "succeeded",
      failureStage: "sidecar_exit",
      exitKind: "unhandled_exception",
      sidecarExitCode: 1,
      sidecarSignal: null,
      safeErrorCode: "SIDECAR_EXITED",
      sidecarLogEvent: "runtime_unhandled_exception",
      sidecarLogCode: "RUNTIME_UNHANDLED_EXCEPTION",
      exceptionText: "Bearer sk-secret-marker C:\\Users\\private\\profile",
    });
    await appendProviderConnectionDiagnostic(directory, {
      trace_id: "11111111-2222-4333-8444-555555555555",
      safe_error_code: "SIDECAR_EXITED",
      request_status: "unknown",
      failure_stage: "sidecar_ipc",
      http_status: null,
      model: "sensitive private model value",
      response_body: "private response",
    });

    const lines = (await readFile(join(directory, "logs", "runtime-diagnostics.jsonl"), "utf8"))
      .trim().split("\n").map((line) => JSON.parse(line) as Record<string, unknown>);
    expect(lines).toHaveLength(2);
    expect(lines[0]).toMatchObject({
      event: "runtime",
      exit_kind: "unhandled_exception",
      sidecar_exit_code: 1,
      safe_error_code: "SIDECAR_EXITED",
      sidecar_log_code: "RUNTIME_UNHANDLED_EXCEPTION",
    });
    expect(lines[1]).toMatchObject({
      event: "provider_connection",
      trace_id: "11111111-2222-4333-8444-555555555555",
      safe_error_code: "SIDECAR_EXITED",
      request_status: "unknown",
      failure_stage: "sidecar_ipc",
      http_status: null,
    });
    expect(JSON.stringify(lines)).not.toMatch(/sk-secret-marker|Users|private model|private response|exceptionText/i);
  });
});
