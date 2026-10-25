import { appendFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { sanitizeRuntimeDiagnostics } from "./diagnostics";

const TRACE_ID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const requestStatuses = new Set(["not_sent", "http_response_received", "unknown"]);
const failureStages = new Set([
  "none", "preflight", "canary_gate", "provider_profile", "secret_resolution", "gateway", "policy", "budget",
  "provider_setup", "provider_transport", "provider_http", "provider_response", "sidecar_ipc", "unknown",
]);
const errorCodes = new Set([
  "MODEL_BALANCE_INSUFFICIENT", "MODEL_BUDGET_EXHAUSTED", "MODEL_BUDGET_SETTLEMENT_FAILED",
  "MODEL_BUDGET_UNAVAILABLE", "MODEL_CAPABILITY_UNSUPPORTED", "MODEL_CONNECTION_FAILED",
  "MODEL_CONNECTION_TEST_DISABLED", "MODEL_CREDENTIAL_REJECTED", "MODEL_CREDENTIAL_UNAVAILABLE",
  "MODEL_INPUT_INVALID", "MODEL_NETWORK_ERROR", "MODEL_NO_ELIGIBLE_PROVIDER", "MODEL_NOT_FOUND",
  "MODEL_POLICY_DENIED", "MODEL_POLICY_UNAVAILABLE", "MODEL_PROVIDER_ERROR",
  "MODEL_PROVIDER_NOT_CONFIGURED", "MODEL_PROVIDER_RESPONSE_INVALID", "MODEL_PROVIDER_SERVER_ERROR",
  "MODEL_PROVIDER_UNAVAILABLE", "MODEL_RATE_LIMITED", "MODEL_REQUEST_INVALID", "MODEL_REQUEST_REJECTED",
  "MODEL_ROUTE_MISMATCH", "MODEL_TELEMETRY_UNAVAILABLE", "MODEL_TIMEOUT", "IPC_CHANNEL_CLOSED",
  "IPC_REQUEST_CANCELLED", "IPC_REQUEST_FAILED", "IPC_REQUEST_TIMEOUT", "IPC_WRITE_FAILED",
  "RUNTIME_SAFE_MODE", "SIDECAR_EXITED", "SIDECAR_IO_FAILED", "SIDECAR_SPAWN_FAILED",
]);

let writeQueue: Promise<void> = Promise.resolve();

function appendSafeRecord(userDataPath: string, record: Record<string, unknown>): Promise<void> {
  const operation = writeQueue.catch(() => undefined).then(async () => {
    const directory = join(userDataPath, "logs");
    await mkdir(directory, { recursive: true });
    await appendFile(
      join(directory, "runtime-diagnostics.jsonl"),
      JSON.stringify({ timestamp: new Date().toISOString(), ...record }) + "\n",
      { encoding: "utf8" },
    );
  });
  writeQueue = operation.catch(() => undefined);
  return operation;
}

export function appendRuntimeDiagnostic(userDataPath: string, value: unknown): Promise<void> {
  return appendSafeRecord(userDataPath, {
    event: "runtime",
    ...sanitizeRuntimeDiagnostics(value),
  });
}

function safeHttpStatus(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 100 && value <= 599 ? value : null;
}

export function appendProviderConnectionDiagnostic(userDataPath: string, value: unknown): Promise<void> {
  const record = typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
  return appendSafeRecord(userDataPath, {
    event: "provider_connection",
    trace_id: typeof record.trace_id === "string" && TRACE_ID.test(record.trace_id) ? record.trace_id : "UNKNOWN",
    safe_error_code: typeof record.safe_error_code === "string" && errorCodes.has(record.safe_error_code)
      ? record.safe_error_code
      : record.safe_error_code === null ? null : "UNKNOWN",
    request_status: typeof record.request_status === "string" && requestStatuses.has(record.request_status)
      ? record.request_status
      : "unknown",
    failure_stage: typeof record.failure_stage === "string" && failureStages.has(record.failure_stage)
      ? record.failure_stage
      : "unknown",
    http_status: safeHttpStatus(record.http_status),
  });
}
