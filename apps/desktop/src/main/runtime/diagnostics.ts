export type RuntimeStartupStage = "not_started" | "sidecar_spawn" | "ipc_handshake" | "ready" | "sidecar_exit";
export type RuntimeHandshakeStatus = "not_started" | "pending" | "succeeded" | "failed";
export type RuntimeFailureStage = "none" | "sidecar_spawn" | "ipc_handshake" | "sidecar_exit" | "unknown";
export type RuntimeExitKind = "none" | "controlled" | "normal_exit" | "unhandled_exception" | "process_crash" | "unexpected_exit";

export type RuntimeDiagnostics = {
  runtimeState: "stopped" | "starting" | "ready" | "restarting" | "safe_mode";
  startupStage: RuntimeStartupStage;
  handshakeStatus: RuntimeHandshakeStatus;
  failureStage: RuntimeFailureStage;
  exitKind: RuntimeExitKind;
  sidecarExitCode: number | null;
  sidecarSignal: string | null;
  safeErrorCode: string | null;
  sidecarLogEvent: string | null;
  sidecarLogCode: string | null;
};

export type SafeRuntimeDiagnostics = {
  runtime_state: RuntimeDiagnostics["runtimeState"] | "unknown";
  startup_stage: RuntimeStartupStage | "unknown";
  ipc_handshake_status: RuntimeHandshakeStatus | "unknown";
  failure_stage: RuntimeFailureStage;
  exit_kind: RuntimeExitKind | "unknown";
  sidecar_exit_code: number | null;
  sidecar_signal: string | null;
  safe_error_code: string | "UNKNOWN" | null;
  sidecar_log_event: string | "UNKNOWN" | null;
  sidecar_log_code: string | "UNKNOWN" | null;
};

const runtimeStates = new Set<RuntimeDiagnostics["runtimeState"]>(["stopped", "starting", "ready", "restarting", "safe_mode"]);
const startupStages = new Set<RuntimeStartupStage>(["not_started", "sidecar_spawn", "ipc_handshake", "ready", "sidecar_exit"]);
const handshakeStatuses = new Set<RuntimeHandshakeStatus>(["not_started", "pending", "succeeded", "failed"]);
const failureStages = new Set<RuntimeFailureStage>(["none", "sidecar_spawn", "ipc_handshake", "sidecar_exit"]);
const exitKinds = new Set<RuntimeExitKind>(["none", "controlled", "normal_exit", "unhandled_exception", "process_crash", "unexpected_exit"]);
const processSignals = new Set([
  "SIGABRT", "SIGALRM", "SIGBUS", "SIGFPE", "SIGHUP", "SIGILL", "SIGINT", "SIGKILL", "SIGPIPE",
  "SIGQUIT", "SIGSEGV", "SIGTERM", "SIGTRAP", "SIGUSR1", "SIGUSR2",
]);

const runtimeErrorCodes = new Set([
  "DATABASE_SCHEMA_INCOMPATIBLE",
  "IPC_CHANNEL_CLOSED",
  "IPC_DIRECTION_INVALID",
  "IPC_FRAME_INVALID",
  "IPC_FRAME_TOO_LARGE",
  "IPC_FRAME_TRUNCATED",
  "IPC_HANDSHAKE_FAILED",
  "IPC_JSON_INVALID",
  "IPC_REQUEST_CANCELLED",
  "IPC_REQUEST_FAILED",
  "IPC_REQUEST_INVALID",
  "IPC_REQUEST_TIMEOUT",
  "IPC_RESPONSE_TRACE_MISMATCH",
  "IPC_RESPONSE_UNMATCHED",
  "IPC_UTF8_INVALID",
  "IPC_WRITE_FAILED",
  "MODEL_PROVIDER_CONFIGURATION_INVALID",
  "PROTOCOL_VERSION_UNSUPPORTED",
  "RUNTIME_SAFE_MODE",
  "RUNTIME_UNHANDLED_EXCEPTION",
  "SCHEMA_VALIDATION_FAILED",
  "SIDECAR_EXITED",
  "SIDECAR_IO_FAILED",
  "SIDECAR_SPAWN_FAILED",
]);

export function isSafeRuntimeErrorCode(value: string): boolean {
  return runtimeErrorCodes.has(value);
}

const sidecarLogCodes = new Map<string, ReadonlySet<string>>([
  ["provider_configuration_failed", new Set(["MODEL_PROVIDER_CONFIGURATION_INVALID"])],
  ["runtime_bootstrap_failed", new Set(["DATABASE_SCHEMA_INCOMPATIBLE"])],
  ["runtime_frame_rejected", new Set(["IPC_DIRECTION_INVALID", "IPC_FRAME_INVALID", "IPC_FRAME_TOO_LARGE"])],
  ["runtime_request_failed", new Set(["IPC_REQUEST_FAILED"])],
  ["runtime_unhandled_exception", new Set(["RUNTIME_UNHANDLED_EXCEPTION"])],
]);

export type SafeRuntimeLog = { level: "ERROR"; event: string; code: string };

export function readSafeSidecarLogLine(line: string): SafeRuntimeLog | undefined {
  const match = /^event=([a-z_]{1,64}) code=([A-Z0-9_:-]{1,96})$/.exec(line);
  if (!match) return undefined;
  const [, event, code] = match;
  if (!sidecarLogCodes.get(event)?.has(code)) return undefined;
  return { level: "ERROR", event, code };
}

function recordOf(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function allowedString<T extends string>(value: unknown, allowed: ReadonlySet<T>): T | "unknown" {
  return typeof value === "string" && allowed.has(value as T) ? value as T : "unknown";
}

function allowedCode(value: unknown, allowed: ReadonlySet<string>): string | "UNKNOWN" | null {
  if (value === null || value === undefined) return null;
  return typeof value === "string" && allowed.has(value) ? value : "UNKNOWN";
}

function allowedDiagnosticEvent(value: unknown): string | "UNKNOWN" | null {
  if (value === null || value === undefined) return null;
  return typeof value === "string" && sidecarLogCodes.has(value) ? value : "UNKNOWN";
}

function allowedDiagnosticLogCode(event: unknown, code: unknown): string | "UNKNOWN" | null {
  if (event === null && code === null || event === undefined && code === undefined) return null;
  if (typeof event !== "string" || typeof code !== "string") return "UNKNOWN";
  return sidecarLogCodes.get(event)?.has(code) ? code : "UNKNOWN";
}

function allowedExitCode(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= -2_147_483_648 && value <= 4_294_967_295
    ? value
    : null;
}

function allowedSignal(value: unknown): string | null {
  return typeof value === "string" && processSignals.has(value) ? value : null;
}

export function sanitizeRuntimeDiagnostics(value: unknown): SafeRuntimeDiagnostics {
  const record = recordOf(value);
  const logEvent = allowedDiagnosticEvent(record.sidecarLogEvent);
  return {
    runtime_state: allowedString(record.runtimeState, runtimeStates),
    startup_stage: allowedString(record.startupStage, startupStages),
    ipc_handshake_status: allowedString(record.handshakeStatus, handshakeStatuses),
    failure_stage: allowedString(record.failureStage, failureStages) as RuntimeFailureStage,
    exit_kind: allowedString(record.exitKind, exitKinds),
    sidecar_exit_code: allowedExitCode(record.sidecarExitCode),
    sidecar_signal: allowedSignal(record.sidecarSignal),
    safe_error_code: allowedCode(record.safeErrorCode, runtimeErrorCodes),
    sidecar_log_event: logEvent,
    sidecar_log_code: allowedDiagnosticLogCode(record.sidecarLogEvent, record.sidecarLogCode),
  };
}
