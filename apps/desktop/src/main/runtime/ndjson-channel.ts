import { randomUUID } from "node:crypto";
import type { Readable, Writable } from "node:stream";
import { TextDecoder } from "node:util";
import { isSafeRuntimeErrorCode } from "./diagnostics";

const MAX_FRAME_BYTES = 1_048_576;
const MAX_TOMBSTONES = 1024;
const TRACE_ID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const ERROR_CATEGORIES = new Set([
  "VALIDATION", "AUTH", "PERMISSION", "POLICY", "APPROVAL", "MODEL", "TOOL", "BROWSER",
  "COMPUTER", "NETWORK", "WORKER", "WORKFLOW", "STORAGE", "SYSTEM",
]);

export type RuntimeRequestStatus = "not_sent" | "http_response_received" | "unknown";
export type RuntimeFailureStage =
  | "preflight" | "canary_gate" | "provider_profile" | "secret_resolution" | "gateway" | "policy" | "budget"
  | "provider_setup" | "provider_transport" | "provider_http" | "provider_response" | "sidecar_ipc" | "unknown";

type RuntimeIpcErrorContext = {
  traceId?: string;
  requestStatus?: RuntimeRequestStatus;
  failureStage?: RuntimeFailureStage;
  httpStatus?: number | null;
};

export class RuntimeIpcError extends Error {
  constructor(
    readonly code: string,
    message = `Runtime IPC failed (${code})`,
    private readonly context: RuntimeIpcErrorContext = {},
  ) {
    super(message);
    this.name = "RuntimeIpcError";
  }

  get traceId(): string | undefined { return this.context.traceId; }
  get requestStatus(): RuntimeRequestStatus | undefined { return this.context.requestStatus; }
  get failureStage(): RuntimeFailureStage | undefined { return this.context.failureStage; }
  get httpStatus(): number | null | undefined { return this.context.httpStatus; }
}

type RuntimeResponse = {
  protocol_name: "runtime-ipc";
  protocol_version: 1;
  message_id: string;
  request_id: string;
  trace_id: string;
  message_type: "response";
  payload: { result?: Record<string, unknown>; error?: Record<string, unknown> };
};

type PendingRequest = {
  resolve: (value: Record<string, unknown>) => void;
  reject: (reason: RuntimeIpcError) => void;
  traceId: string;
  writeAttempted: boolean;
  timer: NodeJS.Timeout;
  signal?: AbortSignal;
  abortListener?: () => void;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const nonEmptyString = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0;

const isShuLiuError = (value: unknown, traceId: string): value is Record<string, unknown> =>
  isRecord(value) &&
  nonEmptyString(value.code) &&
  typeof value.category === "string" && ERROR_CATEGORIES.has(value.category) &&
  nonEmptyString(value.message) &&
  typeof value.retryable === "boolean" &&
  typeof value.recoverable === "boolean" &&
  nonEmptyString(value.severity) &&
  isRecord(value.details) &&
  value.trace_id === traceId &&
  Object.keys(value).every((key) => ["code", "category", "message", "retryable", "recoverable", "severity", "details", "cause", "trace_id"].includes(key));

function validateResponse(value: unknown): RuntimeResponse {
  if (!isRecord(value)) throw new RuntimeIpcError("SCHEMA_VALIDATION_FAILED");
  if (value.protocol_version !== 1) {
    throw new RuntimeIpcError("PROTOCOL_VERSION_UNSUPPORTED");
  }
  if (
    value.protocol_name !== "runtime-ipc" ||
    !nonEmptyString(value.message_id) ||
    !nonEmptyString(value.request_id) ||
    !nonEmptyString(value.trace_id) ||
    value.message_type !== "response" ||
    !isRecord(value.payload)
  ) {
    throw new RuntimeIpcError("SCHEMA_VALIDATION_FAILED");
  }
  const payload = value.payload;
  const hasResult = Object.hasOwn(payload, "result");
  const hasError = Object.hasOwn(payload, "error");
  if (
    hasResult === hasError ||
    Object.keys(payload).length !== 1 ||
    (hasResult && !isRecord(payload.result)) ||
    (hasError && !isShuLiuError(payload.error, value.trace_id))
  ) {
    throw new RuntimeIpcError("SCHEMA_VALIDATION_FAILED");
  }
  return value as unknown as RuntimeResponse;
}

export class NdjsonChannel {
  private buffer = "";
  private closed = false;
  private readonly decoder = new TextDecoder("utf-8", { fatal: true });
  private readonly pending = new Map<string, PendingRequest>();
  private readonly cancelledIds = new Set<string>();

  constructor(
    private readonly stdout: Readable,
    private readonly stdin: Writable,
    private readonly maxFrameBytes = MAX_FRAME_BYTES,
    private readonly onFatal?: (error: RuntimeIpcError) => void,
  ) {
    this.stdout.on("data", this.onData);
    this.stdout.once("end", this.onEnd);
    this.stdout.once("error", this.onStreamError);
    this.stdin.once("error", this.onStreamError);
  }

  request(
    method: string,
    params: Record<string, unknown>,
    options: { timeoutMs?: number; signal?: AbortSignal; traceId?: string } = {},
  ): Promise<Record<string, unknown>> {
    const traceId = options.traceId ?? randomUUID();
    if (!TRACE_ID.test(traceId)) {
      return Promise.reject(new RuntimeIpcError("IPC_REQUEST_INVALID", undefined, {
        requestStatus: "not_sent",
        failureStage: "preflight",
      }));
    }
    if (this.closed) return Promise.reject(this.requestError("IPC_CHANNEL_CLOSED", traceId, "not_sent"));
    if (!/^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/.test(method) || !isRecord(params)) {
      return Promise.reject(this.requestError("IPC_REQUEST_INVALID", traceId, "not_sent", "preflight"));
    }
    if (options.signal?.aborted) return Promise.reject(this.requestError("IPC_REQUEST_CANCELLED", traceId, "not_sent"));

    const requestId = randomUUID();
    const timeoutMs = Math.max(1, Math.min(options.timeoutMs ?? 10_000, 300_000));

    return new Promise((resolve, reject) => {
      const pending: PendingRequest = {
        resolve,
        reject,
        traceId,
        writeAttempted: false,
        timer: setTimeout(() => {
          this.sendCancel(requestId, traceId);
          this.rejectPending(requestId, this.requestError("IPC_REQUEST_TIMEOUT", traceId, "unknown"), true);
        }, timeoutMs),
        signal: options.signal,
      };
      if (pending.timer.unref) pending.timer.unref();
      if (options.signal) {
        pending.abortListener = () => {
          this.sendCancel(requestId, traceId);
          this.rejectPending(requestId, this.requestError("IPC_REQUEST_CANCELLED", traceId, "unknown"), true);
        };
        options.signal.addEventListener("abort", pending.abortListener, { once: true });
      }
      this.pending.set(requestId, pending);
      pending.writeAttempted = true;
      this.write({
        protocol_name: "runtime-ipc",
        protocol_version: 1,
        message_id: randomUUID(),
        request_id: requestId,
        trace_id: traceId,
        message_type: "request",
        payload: { method, params },
      });
    });
  }

  close(error = new RuntimeIpcError("IPC_CHANNEL_CLOSED")): void {
    if (this.closed) return;
    this.closed = true;
    this.stdout.off("data", this.onData);
    this.stdout.off("end", this.onEnd);
    this.stdout.off("error", this.onStreamError);
    this.stdin.off("error", this.onStreamError);
    for (const requestId of [...this.pending.keys()]) this.rejectPending(requestId, error);
  }

  private readonly onData = (chunk: Buffer | string): void => {
    if (this.closed) return;
    try {
      this.buffer += typeof chunk === "string" ? chunk : this.decoder.decode(chunk, { stream: true });
    } catch {
      this.fail(new RuntimeIpcError("IPC_UTF8_INVALID"));
      return;
    }
    if (Buffer.byteLength(this.buffer, "utf8") > this.maxFrameBytes && !this.buffer.includes("\n")) {
      this.fail(new RuntimeIpcError("IPC_FRAME_TOO_LARGE"));
      return;
    }
    let newline = this.buffer.indexOf("\n");
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline).replace(/\r$/, "");
      this.buffer = this.buffer.slice(newline + 1);
      if (Buffer.byteLength(line, "utf8") > this.maxFrameBytes) {
        this.fail(new RuntimeIpcError("IPC_FRAME_TOO_LARGE"));
        return;
      }
      let parsed: unknown;
      try {
        parsed = JSON.parse(line) as unknown;
      } catch {
        this.fail(new RuntimeIpcError("IPC_JSON_INVALID"));
        return;
      }
      try {
        this.onResponse(validateResponse(parsed));
      } catch (error) {
        this.fail(error instanceof RuntimeIpcError ? error : new RuntimeIpcError("SCHEMA_VALIDATION_FAILED"));
        return;
      }
      newline = this.buffer.indexOf("\n");
    }
    if (Buffer.byteLength(this.buffer, "utf8") > this.maxFrameBytes) {
      this.fail(new RuntimeIpcError("IPC_FRAME_TOO_LARGE"));
    }
  };

  private readonly onEnd = (): void => {
    try {
      this.buffer += this.decoder.decode();
    } catch {
      this.fail(new RuntimeIpcError("IPC_UTF8_INVALID"));
      return;
    }
    this.fail(new RuntimeIpcError(this.buffer ? "IPC_FRAME_TRUNCATED" : "SIDECAR_EXITED"));
  };

  private readonly onStreamError = (): void => {
    this.fail(new RuntimeIpcError("SIDECAR_IO_FAILED"));
  };

  private onResponse(response: RuntimeResponse): void {
    const pending = this.pending.get(response.request_id);
    if (!pending) {
      if (this.cancelledIds.delete(response.request_id)) return;
      throw new RuntimeIpcError("IPC_RESPONSE_UNMATCHED");
    }
    if (pending.traceId !== response.trace_id) throw new RuntimeIpcError("IPC_RESPONSE_TRACE_MISMATCH");
    this.clearPending(response.request_id, pending);
    if (response.payload.error) {
      const receivedCode = String(response.payload.error.code);
      const code = isSafeRuntimeErrorCode(receivedCode) ? receivedCode : "IPC_REQUEST_FAILED";
      pending.reject(this.requestError(code, pending.traceId, "unknown"));
    } else {
      pending.resolve(response.payload.result ?? {});
    }
  }

  private write(frame: Record<string, unknown>): void {
    if (this.closed) return;
    try {
      this.stdin.write(`${JSON.stringify(frame)}\n`);
    } catch {
      this.fail(new RuntimeIpcError("IPC_WRITE_FAILED"));
    }
  }

  private sendCancel(requestId: string, traceId: string): void {
    this.write({
      protocol_name: "runtime-ipc",
      protocol_version: 1,
      message_id: randomUUID(),
      request_id: requestId,
      trace_id: traceId,
      message_type: "cancel",
      payload: {},
    });
  }

  private rejectPending(requestId: string, error: RuntimeIpcError, remember = false): void {
    const pending = this.pending.get(requestId);
    if (!pending) return;
    this.clearPending(requestId, pending);
    if (remember) {
      this.cancelledIds.add(requestId);
      if (this.cancelledIds.size > MAX_TOMBSTONES) {
        this.cancelledIds.delete(this.cancelledIds.values().next().value as string);
      }
    }
    const status = error.requestStatus ?? (pending.writeAttempted ? "unknown" : "not_sent");
    const safeCode = isSafeRuntimeErrorCode(error.code) ? error.code : "IPC_REQUEST_FAILED";
    pending.reject(this.requestError(safeCode, pending.traceId, status, error.failureStage ?? "sidecar_ipc"));
  }

  private clearPending(requestId: string, pending: PendingRequest): void {
    clearTimeout(pending.timer);
    if (pending.signal && pending.abortListener) {
      pending.signal.removeEventListener("abort", pending.abortListener);
    }
    this.pending.delete(requestId);
  }

  private fail(error: RuntimeIpcError): void {
    const wasOpen = !this.closed;
    this.close(error);
    if (wasOpen) this.onFatal?.(error);
  }

  private requestError(
    code: string,
    traceId: string,
    requestStatus: RuntimeRequestStatus,
    failureStage: RuntimeFailureStage = "sidecar_ipc",
  ): RuntimeIpcError {
    const safeCode = isSafeRuntimeErrorCode(code) ? code : "IPC_REQUEST_FAILED";
    return new RuntimeIpcError(safeCode, undefined, {
      traceId,
      requestStatus,
      failureStage,
      httpStatus: null,
    });
  }
}
