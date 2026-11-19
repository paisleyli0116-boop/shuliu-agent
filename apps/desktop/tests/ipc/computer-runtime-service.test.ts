import { describe, expect, it, vi } from "vitest";
import { RuntimeComputerService } from "../../src/main/ipc/computer.js";
import { createRendererApi, DESKTOP_IPC_CHANNELS } from "../../src/preload/api.js";

describe("RuntimeComputerService", () => {
  it("queries the Sidecar capability and returns only safe status fields", async () => {
    const request = vi.fn(async (_method: string, params: Record<string, unknown>) => ({
      status: "succeeded",
      trace_id: params.trace_id,
      output: {
        status: "AVAILABLE",
        os: "windows",
        reason_code: null,
        machine: "private-machine-name",
        evidence_ref: "computer-evidence:private-reference",
      },
    }));
    const service = new RuntimeComputerService(request);

    const result = await service.capability();

    expect(result.status).toBe("available");
    expect(result.safe_error_code).toBeNull();
    expect(result.trace_id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(result).not.toHaveProperty("machine");
    expect(result).not.toHaveProperty("evidence_ref");
    expect(request).toHaveBeenCalledWith(
      "computer.capability",
      expect.objectContaining({ trace_id: result.trace_id }),
      expect.objectContaining({ traceId: result.trace_id }),
    );
  });

  it("never forwards an arbitrary Sidecar error message to Renderer", async () => {
    const service = new RuntimeComputerService(async () => {
      throw new Error("sensitive exception text");
    });

    const result = await service.capability();

    expect(result.status).toBe("unknown");
    expect(result.safe_error_code).toBe("COMPUTER_RUNTIME_UNAVAILABLE");
    expect(JSON.stringify(result)).not.toContain("sensitive exception text");
  });

  it("fails closed when the Sidecar capability response is malformed", async () => {
    const service = new RuntimeComputerService(async () => ({
      status: "succeeded",
      output: { status: "AVAILABLE", machine: "should-not-escape" },
    }));

    const result = await service.capability();

    expect(result.status).toBe("unknown");
    expect(result.safe_error_code).toBe("COMPUTER_RESPONSE_INVALID");
    expect(result).not.toHaveProperty("machine");
  });

  it("requests only the current foreground window and returns a bounded local observation", async () => {
    const request = vi.fn(async (_method: string, params: Record<string, unknown>) => ({
      status: "succeeded",
      trace_id: params.trace_id,
      output: {
        status: "OBSERVED",
        title: "Calculator",
        observation_id: "obs-1",
        elements: [{ element_ref: "el-1", role: "Text", name: "Result", value: "42", sensitive: false, enabled: true }],
        evidence_ref: "private-evidence-reference",
      },
    }));
    const service = new RuntimeComputerService(request);

    const result = await service.observeForeground();

    expect(result.status).toBe("observed");
    expect(result.title).toBe("Calculator");
    expect(result.elements).toHaveLength(1);
    expect(result).not.toHaveProperty("evidence_ref");
    expect(request).toHaveBeenCalledWith(
      "computer.observe_foreground",
      expect.objectContaining({ trace_id: result.trace_id }),
      expect.objectContaining({ traceId: result.trace_id }),
    );
    expect(request.mock.calls[0]?.[1]).not.toHaveProperty("window_ref");
  });

  it("fails closed when foreground observation contains an unbounded or malformed tree", async () => {
    const service = new RuntimeComputerService(async (_method, params) => ({
      status: "succeeded",
      trace_id: params.trace_id,
      output: { status: "OBSERVED", title: "x", elements: Array(301).fill({}) },
    }));

    await expect(service.observeForeground()).resolves.toMatchObject({
      status: "blocked",
      safe_error_code: "COMPUTER_RESPONSE_INVALID",
    });
  });

  it("exposes only the typed capability query through Preload", async () => {
    const invoke = vi.fn(async () => ({
      status: "available" as const,
      safe_error_code: null,
      trace_id: "00000000-0000-4000-8000-000000000001",
    }));
    const api = createRendererApi(invoke);

    await expect(api.computer.capability()).resolves.toMatchObject({ status: "available" });
    expect(invoke).toHaveBeenCalledWith(DESKTOP_IPC_CHANNELS.computerCapabilityGet);
  });

  it("exposes a no-argument foreground observation through Preload", async () => {
    const invoke = vi.fn(async () => ({
      status: "observed" as const,
      title: "Calculator",
      elements: [],
      safe_error_code: null,
      trace_id: "00000000-0000-4000-8000-000000000001",
    }));
    const api = createRendererApi(invoke);

    await expect(api.computer.observeForeground()).resolves.toMatchObject({ status: "observed" });
    expect(invoke).toHaveBeenCalledWith(DESKTOP_IPC_CHANNELS.computerObserveForeground);
  });
});
