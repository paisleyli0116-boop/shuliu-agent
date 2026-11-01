import type { NdjsonChannel } from "./ndjson-channel";

export type ProcessRecoveryStatus = "RESUME" | "WAIT" | "SAFE_MODE" | "DEAD_LETTER";
export type RuntimeRecoveryOutcome = {
  status: ProcessRecoveryStatus;
  safeErrorCode: string | null;
};

export type RuntimeRestartPolicy = {
  max_restarts: number;
  /** Per-restart delay in seconds. The last configured delay is reused. */
  backoff: readonly number[];
  /** Sidecar handshake timeout in seconds. */
  health_timeout: number;
};

export type RuntimeRecoveryContext = {
  previous_generation: number;
  current_generation: number;
  previous_exit_code: number | null;
  previous_exit_kind: string;
  previous_error_code: string | null;
};

export type RuntimeRecoveryPort = (
  channel: NdjsonChannel,
  context: RuntimeRecoveryContext,
  signal: AbortSignal,
) => Promise<unknown>;

const RECOVERY_STATUSES = new Set<ProcessRecoveryStatus>([
  "RESUME", "WAIT", "SAFE_MODE", "DEAD_LETTER",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validatePolicy(policy: RuntimeRestartPolicy): void {
  if (!Number.isInteger(policy.max_restarts) || policy.max_restarts < 0 || policy.max_restarts > 10) {
    throw new TypeError("max_restarts must be an integer between 0 and 10");
  }
  if (!Array.isArray(policy.backoff) || policy.backoff.length > policy.max_restarts) {
    throw new TypeError("backoff must have at most one finite delay per restart");
  }
  if (policy.backoff.some((seconds) => !Number.isFinite(seconds) || seconds < 0 || seconds > 300)) {
    throw new TypeError("backoff delays must be between 0 and 300 seconds");
  }
  if (!Number.isFinite(policy.health_timeout) || policy.health_timeout <= 0 || policy.health_timeout > 300) {
    throw new TypeError("health_timeout must be between 0 and 300 seconds");
  }
}

export class RuntimeRecoveryCoordinator {
  private readonly policyValue: Readonly<RuntimeRestartPolicy>;

  get policy(): Readonly<RuntimeRestartPolicy> {
    return this.policyValue;
  }

  constructor(
    policy: RuntimeRestartPolicy,
    private readonly recover: RuntimeRecoveryPort,
  ) {
    validatePolicy(policy);
    if (typeof recover !== "function") throw new TypeError("process recovery port is required");
    this.policyValue = Object.freeze({
      ...policy,
      backoff: Object.freeze([...policy.backoff]),
    });
  }

  backoffMilliseconds(restartNumber: number): number {
    if (!Number.isInteger(restartNumber) || restartNumber < 1 || restartNumber > this.policy.max_restarts) {
      throw new RangeError("restart number is outside the configured restart budget");
    }
    const backoff = this.policy.backoff;
    if (backoff.length === 0) return 0;
    return backoff[Math.min(restartNumber - 1, backoff.length - 1)] * 1_000;
  }

  async recoverAfterRestart(
    channel: NdjsonChannel,
    context: RuntimeRecoveryContext,
    signal?: AbortSignal,
  ): Promise<RuntimeRecoveryOutcome> {
    if (signal?.aborted) return { status: "SAFE_MODE", safeErrorCode: "RUNTIME_RECOVERY_CANCELLED" };
    const controller = new AbortController();
    let timeout: NodeJS.Timeout | undefined;
    let abortHandler: (() => void) | undefined;
    try {
      const cancelled = new Promise<RuntimeRecoveryOutcome>((resolve) => {
        if (!signal) return;
        abortHandler = () => {
          controller.abort();
          resolve({ status: "SAFE_MODE", safeErrorCode: "RUNTIME_RECOVERY_CANCELLED" });
        };
        signal.addEventListener("abort", abortHandler, { once: true });
        if (signal.aborted) abortHandler();
      });
      const timedOut = new Promise<RuntimeRecoveryOutcome>((resolve) => {
        timeout = setTimeout(() => {
          controller.abort();
          resolve({ status: "SAFE_MODE", safeErrorCode: "RUNTIME_RECOVERY_TIMEOUT" });
        }, Math.ceil(this.policy.health_timeout * 1_000));
      });
      const recovered = Promise.resolve()
        .then(() => controller.signal.aborted
          ? { status: "SAFE_MODE", error_code: "RUNTIME_RECOVERY_CANCELLED" }
          : this.recover(channel, context, controller.signal))
        .then((result): RuntimeRecoveryOutcome => {
          if (!isRecord(result) || typeof result.status !== "string" || !RECOVERY_STATUSES.has(result.status as ProcessRecoveryStatus)) {
            return { status: "SAFE_MODE", safeErrorCode: "RUNTIME_RECOVERY_RESULT_INVALID" };
          }
          const status = result.status as ProcessRecoveryStatus;
          const suppliedCode = result.error_code;
          if (suppliedCode !== undefined && suppliedCode !== null &&
            (typeof suppliedCode !== "string" || !isSafeRuntimeRecoveryErrorCode(suppliedCode))) {
            return { status: "SAFE_MODE", safeErrorCode: "RUNTIME_RECOVERY_RESULT_INVALID" };
          }
          if (status === "RESUME" && suppliedCode !== undefined && suppliedCode !== null) {
            return { status: "SAFE_MODE", safeErrorCode: "RUNTIME_RECOVERY_RESULT_INVALID" };
          }
          return {
            status,
            safeErrorCode: typeof suppliedCode === "string"
              ? suppliedCode
              : defaultErrorCode(status),
          };
        })
        .catch((): RuntimeRecoveryOutcome => ({
          status: "SAFE_MODE",
          safeErrorCode: "RUNTIME_RECOVERY_FAILED",
        }));
      return await Promise.race([recovered, timedOut, cancelled]);
    } finally {
      if (timeout) clearTimeout(timeout);
      if (signal && abortHandler) signal.removeEventListener("abort", abortHandler);
    }
  }
}

const recoveryErrorCodes = new Set([
  "APPROVAL_PENDING",
  "APPLIED_RESULT_UNAVAILABLE",
  "CHECKPOINT_NOT_FOUND",
  "CHECKPOINT_SCHEMA_INCOMPATIBLE",
  "CHECKPOINT_STATE_INVALID",
  "CHECKPOINT_UNAVAILABLE",
  "EXTERNAL_STATE_INVALID",
  "EXTERNAL_STATE_UNAVAILABLE",
  "IDEMPOTENCY_KEY_REQUIRED",
  "PENDING_ACTION_INVALID",
  "PROCESS_RECOVERY_FAILED",
  "RECONCILED_RESULT_INVALID",
  "RECOVERY_AUDIT_UNAVAILABLE",
  "RECOVERY_METADATA_INVALID",
  "RUNTIME_GENERATION_INVALID",
  "SIDE_EFFECT_STATE_UNKNOWN",
  "WORKER_LEASE_FENCE_UNAVAILABLE",
  "WORKER_LEASE_FENCE_UNCONFIRMED",
  "WORKFLOW_CONTEXT_INCOMPATIBLE",
]);

function isSafeRuntimeRecoveryErrorCode(value: string): boolean {
  return recoveryErrorCodes.has(value);
}

function defaultErrorCode(status: ProcessRecoveryStatus): string | null {
  switch (status) {
    case "RESUME": return null;
    case "WAIT": return "RUNTIME_RECOVERY_WAITING";
    case "SAFE_MODE": return "RUNTIME_RECOVERY_REQUIRED";
    case "DEAD_LETTER": return "RUNTIME_RECOVERY_DEAD_LETTER";
  }
}
