import { appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

export type DesktopStartupStage =
  | "electron_main"
  | "resource_resolution"
  | "safe_storage"
  | "sidecar_create"
  | "ipc_handshake"
  | "browser_window"
  | "preload"
  | "renderer_load"
  | "renderer_process"
  | "child_process"
  | "electron_exit";

export type DesktopStartupStatus = "started" | "succeeded" | "failed" | "observed";

export type DesktopStartupDiagnosticInput = {
  stage?: unknown;
  status?: unknown;
  code?: unknown;
  exitCode?: unknown;
  processType?: unknown;
  reason?: unknown;
  appPackaged?: unknown;
  appPathResolved?: unknown;
  rendererResourceAvailable?: unknown;
  preloadResourceAvailable?: unknown;
  sidecarResourceAvailable?: unknown;
  safeStorageAvailable?: unknown;
};

export type SafeDesktopStartupDiagnostic = {
  stage: DesktopStartupStage | "unknown";
  status: DesktopStartupStatus | "unknown";
  code: string | null;
  exit_code: number | null;
  process_type: string | null;
  reason: string | null;
  app_packaged: boolean | null;
  app_path_resolved: boolean | null;
  renderer_resource_available: boolean | null;
  preload_resource_available: boolean | null;
  sidecar_resource_available: boolean | null;
  safe_storage_available: boolean | null;
};

const stages = new Set<DesktopStartupStage>([
  "electron_main", "resource_resolution", "safe_storage", "sidecar_create", "ipc_handshake",
  "browser_window", "preload", "renderer_load", "renderer_process", "child_process", "electron_exit",
]);
const statuses = new Set<DesktopStartupStatus>(["started", "succeeded", "failed", "observed"]);
const codes = new Set([
  "APP_READY_FAILED", "BROWSER_WINDOW_CREATE_FAILED", "CHILD_PROCESS_GONE", "DESKTOP_UNCAUGHT_EXCEPTION",
  "DESKTOP_EXIT_NONZERO", "IPC_HANDSHAKE_FAILED", "PRELOAD_BRIDGE_UNAVAILABLE", "PRELOAD_LOAD_FAILED",
  "RENDERER_LOAD_FAILED", "RENDERER_PROCESS_GONE", "SAFE_STORAGE_UNAVAILABLE", "SIDECAR_SPAWN_FAILED",
]);
const processTypes = new Set(["Renderer", "GPU", "Utility", "Sandbox helper", "Unknown"]);
const reasons = new Set(["clean-exit", "crashed", "killed", "oom", "launch-failed", "integrity-failure", "abnormal-exit", "Unknown"]);

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function safeBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function safeExitCode(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= -2_147_483_648 && value <= 4_294_967_295
    ? value
    : null;
}

function safeEnum<T extends string>(value: unknown, allowed: ReadonlySet<T>): T | null {
  return typeof value === "string" && allowed.has(value as T) ? value as T : null;
}

export function sanitizeDesktopStartupDiagnostic(value: unknown): SafeDesktopStartupDiagnostic {
  const record = asRecord(value);
  const stage = safeEnum(record.stage, stages) ?? "unknown";
  const status = safeEnum(record.status, statuses) ?? "unknown";
  const code = typeof record.code === "string" && codes.has(record.code)
    ? record.code
    : record.code === undefined || record.code === null ? null : "UNKNOWN";
  const processType = safeEnum(record.processType, processTypes);
  const reason = safeEnum(record.reason, reasons);

  return {
    stage,
    status,
    code,
    exit_code: safeExitCode(record.exitCode),
    process_type: processType,
    reason,
    app_packaged: safeBoolean(record.appPackaged),
    app_path_resolved: safeBoolean(record.appPathResolved),
    renderer_resource_available: safeBoolean(record.rendererResourceAvailable),
    preload_resource_available: safeBoolean(record.preloadResourceAvailable),
    sidecar_resource_available: safeBoolean(record.sidecarResourceAvailable),
    safe_storage_available: safeBoolean(record.safeStorageAvailable),
  };
}

export function appendDesktopStartupDiagnostic(userDataPath: string, value: unknown): void {
  const directory = join(userDataPath, "logs");
  mkdirSync(directory, { recursive: true });
  appendFileSync(
    join(directory, "desktop-startup.jsonl"),
    `${JSON.stringify({ timestamp: new Date().toISOString(), event: "desktop_startup", ...sanitizeDesktopStartupDiagnostic(value) })}\n`,
    { encoding: "utf8" },
  );
}
