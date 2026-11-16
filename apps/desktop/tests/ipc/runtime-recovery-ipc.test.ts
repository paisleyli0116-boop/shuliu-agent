import { describe, expect, it, vi } from "vitest";
import type { NdjsonChannel } from "../../src/main/runtime/ndjson-channel";
import { createRuntimeRecoveryCoordinator } from "../../src/main/runtime/recovery-ipc";

const context = {
  previous_generation: 2,
  current_generation: 3,
  transition_id: "66666666-6666-4666-8666-666666666666",
  previous_exit_code: 17,
  previous_exit_kind: "process_crash",
  previous_error_code: null,
};

describe("production Sidecar recovery IPC bridge", () => {
  it("fences the generation, queries checkpoints, and persists each recovery decision", async () => {
    const calls: Array<[string, Record<string, unknown>]> = [];
    const channel = {
      request: vi.fn(async (method: string, params: Record<string, unknown>) => {
        calls.push([method, params]);
        if (method === "runtime.recovery.begin") return {
          status: "RESUME",
          error_code: null,
          transition_id: context.transition_id,
          previous_generation: 2,
          current_generation: 3,
        };
        if (method === "runtime.recovery.checkpoints") {
          return { checkpoints: [
            {
              run_id: "run-a",
              sequence: 4,
              has_pending_action: true,
              has_pending_approval: false,
              context_version: "workflow:v1;skill:v1",
            },
          ] };
        }
        if (method === "runtime.recovery.recover_run") return { status: "WAIT", error_code: "SIDE_EFFECT_STATE_UNKNOWN" };
        throw new Error("unexpected method");
      }),
    } as unknown as NdjsonChannel;
    const recovery = createRuntimeRecoveryCoordinator();

    const result = await recovery.recoverAfterRestart(channel, context);

    expect(result).toEqual({ status: "WAIT", safeErrorCode: "SIDE_EFFECT_STATE_UNKNOWN" });
    expect(calls.map(([method]) => method)).toEqual([
      "runtime.recovery.begin",
      "runtime.recovery.checkpoints",
      "runtime.recovery.recover_run",
    ]);
    expect(calls[0][1]).toEqual({ transition_id: context.transition_id });
    expect(calls[2][1]).toEqual({
      run_id: "run-a",
      previous_generation: 2,
      current_generation: 3,
      transition_id: context.transition_id,
    });
  });

  it("resumes without a checkpoint only after generation fencing succeeds", async () => {
    const channel = {
      request: vi.fn(async (method: string) => {
        if (method === "runtime.recovery.begin") return {
          status: "RESUME",
          error_code: null,
          transition_id: context.transition_id,
          previous_generation: 2,
          current_generation: 3,
        };
        if (method === "runtime.recovery.checkpoints") return { checkpoints: [] };
        if (method === "runtime.recovery.complete") return { status: "RESUME", error_code: null };
        throw new Error("no run should be reconciled");
      }),
    } as unknown as NdjsonChannel;
    const result = await createRuntimeRecoveryCoordinator().recoverAfterRestart(channel, context);
    expect(result).toEqual({ status: "RESUME", safeErrorCode: null });
    expect(channel.request).toHaveBeenLastCalledWith(
      "runtime.recovery.complete",
      { transition_id: context.transition_id, current_generation: context.current_generation },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("fails closed when Sidecar returns malformed checkpoint metadata", async () => {
    const channel = {
      request: vi.fn(async (method: string) => {
        if (method === "runtime.recovery.begin") return {
          status: "RESUME",
          error_code: null,
          transition_id: context.transition_id,
          previous_generation: 2,
          current_generation: 3,
        };
        return { checkpoints: [{ run_id: "../../private", sequence: 1 }] };
      }),
    } as unknown as NdjsonChannel;
    const result = await createRuntimeRecoveryCoordinator().recoverAfterRestart(channel, context);
    expect(result).toEqual({ status: "SAFE_MODE", safeErrorCode: "RUNTIME_RECOVERY_RESULT_INVALID" });
  });
});
