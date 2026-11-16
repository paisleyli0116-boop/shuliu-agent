import type { NdjsonChannel } from "./ndjson-channel";
import {
  RuntimeRecoveryCoordinator,
  type RuntimeRecoveryContext,
} from "./recovery-coordinator";

const SAFE_RUN_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const RECOVERY_STATUSES = new Set(["RESUME", "WAIT", "SAFE_MODE", "DEAD_LETTER"]);
const MAX_RECOVERY_CHECKPOINTS = 256;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isDecision(value: unknown): value is { status: string; error_code: string | null } {
  return isRecord(value) &&
    typeof value.status === "string" && RECOVERY_STATUSES.has(value.status) &&
    (value.error_code === null || typeof value.error_code === "string");
}

function isGenerationDecision(value: unknown): value is Record<string, unknown> & {
  status: string;
  error_code: string | null;
  transition_id: string;
  previous_generation: number;
  current_generation: number;
} {
  return isRecord(value) &&
    typeof value.status === "string" && RECOVERY_STATUSES.has(value.status) &&
    (value.error_code === null || typeof value.error_code === "string") &&
    typeof value.transition_id === "string" &&
    Number.isSafeInteger(value.previous_generation) &&
    Number.isSafeInteger(value.current_generation);
}

function isCheckpoint(value: unknown): value is Record<string, unknown> & { run_id: string } {
  return isRecord(value) &&
    typeof value.run_id === "string" && SAFE_RUN_ID.test(value.run_id) &&
    Number.isSafeInteger(value.sequence) && (value.sequence as number) >= 1 &&
    typeof value.has_pending_action === "boolean" &&
    typeof value.has_pending_approval === "boolean" &&
    typeof value.context_version === "string" && value.context_version.length > 0;
}

function error(code: string): { status: string; error_code: string } {
  return { status: "SAFE_MODE", error_code: code };
}

async function recoverOnSidecar(
  channel: NdjsonChannel,
  context: RuntimeRecoveryContext,
  signal: AbortSignal,
): Promise<unknown> {
  const begin = await channel.request(
    "runtime.recovery.begin",
    { transition_id: context.transition_id },
    { signal },
  );
  if (!isGenerationDecision(begin)) return error("RUNTIME_RECOVERY_RESULT_INVALID");
  if (begin.status !== "RESUME") return begin;
  if (
    begin.error_code !== null
    || begin.transition_id !== context.transition_id
    || !Number.isSafeInteger(begin.previous_generation)
    || (begin.previous_generation as number) < 0
    || !Number.isSafeInteger(begin.current_generation)
    || begin.current_generation !== (begin.previous_generation as number) + 1
  ) return error("RUNTIME_RECOVERY_RESULT_INVALID");
  const generation = {
    previous_generation: begin.previous_generation as number,
    current_generation: begin.current_generation as number,
    transition_id: context.transition_id,
  };

  const query = await channel.request("runtime.recovery.checkpoints", {}, { signal });
  if (!isRecord(query) || !Array.isArray(query.checkpoints) ||
    query.checkpoints.length > MAX_RECOVERY_CHECKPOINTS ||
    !query.checkpoints.every(isCheckpoint)) {
    return error("RUNTIME_RECOVERY_RESULT_INVALID");
  }

  let aggregate: { status: string; error_code: string | null } | undefined;
  const priority: Record<string, number> = { WAIT: 1, DEAD_LETTER: 2, SAFE_MODE: 3 };
  for (const checkpoint of query.checkpoints) {
    if (signal.aborted) return error("RUNTIME_RECOVERY_CANCELLED");
    if (generation.previous_generation < 1) return error("RUNTIME_RECOVERY_RESULT_INVALID");
    const decision = await channel.request(
      "runtime.recovery.recover_run",
      { run_id: checkpoint.run_id, ...generation },
      { signal },
    );
    if (!isDecision(decision)) return error("RUNTIME_RECOVERY_RESULT_INVALID");
    if (decision.status === "RESUME" && decision.error_code !== null) {
      return error("RUNTIME_RECOVERY_RESULT_INVALID");
    }
    if (decision.status !== "RESUME" &&
      (!aggregate || priority[decision.status] > priority[aggregate.status])) {
      aggregate = decision;
    }
  }
  if (aggregate) return aggregate;
  const completed = await channel.request(
    "runtime.recovery.complete",
    {
      transition_id: generation.transition_id,
      current_generation: generation.current_generation,
    },
    { signal },
  );
  if (!isDecision(completed) || completed.status !== "RESUME" || completed.error_code !== null) {
    if (isDecision(completed) && completed.status !== "RESUME" &&
      (completed.error_code === null || RECOVERY_ERROR_CODES.has(completed.error_code))) {
      return completed;
    }
    return error("RUNTIME_RECOVERY_RESULT_INVALID");
  }
  return { status: "RESUME", error_code: null };
}

const RECOVERY_ERROR_CODES = new Set([
  "DATABASE_SCHEMA_INCOMPATIBLE",
  "SAFE_MODE_RELEASE_NOT_READY",
  "RUNTIME_GENERATION_INVALID",
  "WORKER_LEASE_FENCE_UNCONFIRMED",
  "PROCESS_RECOVERY_FAILED",
]);

export function createRuntimeRecoveryCoordinator(): RuntimeRecoveryCoordinator {
  return new RuntimeRecoveryCoordinator(
    { max_restarts: 1, backoff: [0.5], health_timeout: 10 },
    recoverOnSidecar,
  );
}
