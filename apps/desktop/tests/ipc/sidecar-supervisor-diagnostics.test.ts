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
});
