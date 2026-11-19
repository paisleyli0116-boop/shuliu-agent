import { randomUUID } from "node:crypto";
import { RuntimeIpcError } from "../runtime/ndjson-channel";
import type { ComputerCapability, ComputerObservation } from "../../preload/api";
import type { DesktopComputerService } from "./desktop-ipc";

type SidecarRequest = (
  method: string,
  params: Record<string, unknown>,
  options?: { timeoutMs?: number; traceId?: string },
) => Promise<Record<string, unknown>>;

const tracePattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const safeCodes = new Set([
  "COMPUTER_DPI_UNAVAILABLE",
  "COMPUTER_ELEMENT_STALE",
  "COMPUTER_EVIDENCE_UNAVAILABLE",
  "COMPUTER_OBSERVATION_INVALID",
  "COMPUTER_REQUEST_FAILED",
  "COMPUTER_RESPONSE_INVALID",
  "COMPUTER_RUNTIME_INITIALIZATION_FAILED",
  "COMPUTER_RUNTIME_UNAVAILABLE",
  "COMPUTER_SESSION_LOCKED",
  "COMPUTER_SESSION_NOT_INTERACTIVE",
  "COMPUTER_SESSION_STATE_UNKNOWN",
  "COMPUTER_SESSION_UNAVAILABLE",
  "COMPUTER_UIA_UNAVAILABLE",
  "COMPUTER_WINDOWS_ONLY",
  "COMPUTER_WINDOW_MINIMIZED",
  "COMPUTER_WINDOW_UNAVAILABLE",
  "COMPUTER_WINDOW_REF_INVALID",
  "IPC_CHANNEL_CLOSED",
  "IPC_REQUEST_CANCELLED",
  "IPC_REQUEST_FAILED",
  "IPC_REQUEST_TIMEOUT",
  "SIDECAR_EXITED",
]);

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorResult(traceId: string, code: string): ComputerCapability {
  return {
    status: "unknown",
    safe_error_code: safeCodes.has(code) ? code : "COMPUTER_RUNTIME_UNAVAILABLE",
    trace_id: traceId,
  };
}

export class RuntimeComputerService implements DesktopComputerService {
  constructor(private readonly request: SidecarRequest) {}

  async capability(): Promise<ComputerCapability> {
    const traceId = randomUUID();
    try {
      const response = await this.request(
        "computer.capability",
        { trace_id: traceId },
        { timeoutMs: 10_000, traceId },
      );
      if (
        !record(response) ||
        response.status !== "succeeded" ||
        response.trace_id !== traceId ||
        !record(response.output) ||
        (response.output.status !== "AVAILABLE" && response.output.status !== "UNAVAILABLE")
      ) {
        return errorResult(traceId, "COMPUTER_RESPONSE_INVALID");
      }
      const unavailableCode = response.output.reason_code;
      if (response.output.status === "UNAVAILABLE") {
        const safeCode = typeof unavailableCode === "string" && safeCodes.has(unavailableCode)
          ? unavailableCode
          : "COMPUTER_SESSION_UNAVAILABLE";
        return { status: "unavailable", safe_error_code: safeCode, trace_id: traceId };
      }
      return { status: "available", safe_error_code: null, trace_id: traceId };
    } catch (error) {
      const runtimeError = error instanceof RuntimeIpcError ? error : undefined;
      return errorResult(traceId, runtimeError?.code ?? "COMPUTER_RUNTIME_UNAVAILABLE");
    }
  }

  async observeForeground(): Promise<ComputerObservation> {
    const traceId = randomUUID();
    try {
      const response = await this.request(
        "computer.observe_foreground",
        { trace_id: traceId },
        { timeoutMs: 15_000, traceId },
      );
      if (!record(response) || response.trace_id !== traceId) {
        return observationFailure(traceId, "COMPUTER_RESPONSE_INVALID");
      }
      if (response.status === "blocked") {
        return observationFailure(
          traceId,
          typeof response.safe_error_code === "string" ? response.safe_error_code : "COMPUTER_RUNTIME_UNAVAILABLE",
        );
      }
      if (response.status !== "succeeded" || !record(response.output) || response.output.status !== "OBSERVED" ||
        typeof response.output.title !== "string" || response.output.title.length > 512 ||
        !Array.isArray(response.output.elements) || response.output.elements.length > 300) {
        return observationFailure(traceId, "COMPUTER_RESPONSE_INVALID");
      }
      const elements: ComputerObservation["elements"] = [];
      for (const value of response.output.elements) {
        if (!record(value) || typeof value.role !== "string" || value.role.length > 64 ||
          typeof value.name !== "string" || value.name.length > 256 ||
          typeof value.sensitive !== "boolean" || typeof value.enabled !== "boolean") {
          return observationFailure(traceId, "COMPUTER_RESPONSE_INVALID");
        }
        elements.push({
          role: value.role,
          name: value.sensitive ? "[已隐藏的敏感控件]" : value.name,
          sensitive: value.sensitive,
          enabled: value.enabled,
        });
      }
      return {
        status: "observed",
        title: response.output.title,
        elements,
        safe_error_code: null,
        trace_id: traceId,
      };
    } catch (error) {
      const runtimeError = error instanceof RuntimeIpcError ? error : undefined;
      return observationFailure(traceId, runtimeError?.code ?? "COMPUTER_RUNTIME_UNAVAILABLE");
    }
  }
}

function observationFailure(traceId: string, code: string): ComputerObservation {
  return {
    status: "blocked",
    title: null,
    elements: [],
    safe_error_code: safeCodes.has(code) ? code : "COMPUTER_RUNTIME_UNAVAILABLE",
    trace_id: traceId,
  };
}
