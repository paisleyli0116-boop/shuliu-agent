import { randomUUID } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import type { SpawnOptions } from "node:child_process";
import { isSafeRuntimeErrorCode, readSafeSidecarLogLine, type RuntimeDiagnostics, type RuntimeExitKind, type SafeRuntimeLog } from "./diagnostics";
import { NdjsonChannel, RuntimeIpcError } from "./ndjson-channel";
import { assertRuntimeHandshake } from "./handshake";
import type { RuntimeRecoveryContext, RuntimeRecoveryCoordinator, RuntimeRecoveryOutcome } from "./recovery-coordinator";

export type RuntimeState = "stopped" | "starting" | "ready" | "restarting" | "waiting" | "safe_mode";

export type SidecarSupervisorOptions = {
  command: string;
  args: string[];
  cwd: string;
  env?: NodeJS.ProcessEnv;
  handshakeTimeoutMs?: number;
  maxRestarts?: number;
  recovery?: RuntimeRecoveryCoordinator;
  recoverOnStart?: boolean;
  onLog?: (entry: SafeRuntimeLog) => void;
  onDiagnostic?: (diagnostics: RuntimeDiagnostics) => void;
};

const SIDECAR_ENV_KEYS = new Set([
  "PATH", "Path", "SystemRoot", "WINDIR", "HOME", "USERPROFILE", "TMP", "TEMP", "TMPDIR",
  "SHULIU_DATA_DIR", "SHULIU_MODEL_PROFILES_JSON", "SHULIU_ORGANIZATION_ID",
  "SHULIU_MODEL_CONNECTION_TEST",
  // Trusted Runtime identity and Workflow composition. Only this process sets
  // them, so a Renderer cannot promote itself by writing a request body.
  "SHULIU_ACTOR_ID", "SHULIU_ACTOR_PERMISSIONS", "SHULIU_CONTEXT_PRIVACY",
  "SHULIU_POLICY_BUNDLE", "SHULIU_WORKFLOW_CATALOG", "SHULIU_APPROVAL_TTL_SECONDS",
  "LANG", "LC_ALL", "PYTHONPATH", "PYTHONUTF8", "PYTHONIOENCODING", "VIRTUAL_ENV",
]);

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, milliseconds);
    timer.unref?.();
  });
}

function sidecarEnvironment(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  return {
    ...Object.fromEntries(
    Object.entries(source).filter(([key, value]) => SIDECAR_ENV_KEYS.has(key) && value !== undefined),
    ),
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
  };
}

export class SidecarSupervisor {
  private child?: ChildProcess;
  private channel?: NdjsonChannel;
  private stateValue: RuntimeState = "stopped";
  private startPromise?: Promise<void>;
  private restartPromise?: Promise<void>;
  private restartCount = 0;
  private generation = 0;
  private intentionalStop = false;
  private activeRecoveryAbort?: AbortController;
  private pendingRecoveryContext?: RuntimeRecoveryContext;
  private pendingRecoveryPromise?: Promise<RuntimeState>;
  private readonly fatalChildren = new WeakSet<ChildProcess>();
  private readonly terminationRequested = new WeakSet<ChildProcess>();
  private stderrBuffer = "";
  private startupStage: RuntimeDiagnostics["startupStage"] = "not_started";
  private handshakeStatus: RuntimeDiagnostics["handshakeStatus"] = "not_started";
  private failureStage: RuntimeDiagnostics["failureStage"] = "none";
  private exitKind: RuntimeExitKind = "none";
  private sidecarExitCode: number | null = null;
  private sidecarSignal: string | null = null;
  private safeErrorCode: string | null = null;
  private lastSidecarLog?: SafeRuntimeLog;

  constructor(private readonly options: SidecarSupervisorOptions) {}

  get state(): RuntimeState {
    return this.stateValue;
  }

  get diagnostics(): RuntimeDiagnostics {
    return {
      runtimeState: this.stateValue,
      startupStage: this.startupStage,
      handshakeStatus: this.handshakeStatus,
      failureStage: this.failureStage,
      exitKind: this.exitKind,
      sidecarExitCode: this.sidecarExitCode,
      sidecarSignal: this.sidecarSignal,
      safeErrorCode: this.safeErrorCode,
      sidecarLogEvent: this.lastSidecarLog?.event ?? null,
      sidecarLogCode: this.lastSidecarLog?.code ?? null,
    };
  }

  async start(): Promise<void> {
    if (this.stateValue === "ready") return;
    if (this.startPromise) return this.startPromise;
    if (this.stateValue === "safe_mode") throw new RuntimeIpcError("RUNTIME_SAFE_MODE");
    this.intentionalStop = false;
    this.startPromise = this.launch();
    try {
      await this.startPromise;
    } finally {
      this.startPromise = undefined;
    }
  }

  async request(
    method: string,
    params: Record<string, unknown> = {},
    options: { timeoutMs?: number; signal?: AbortSignal; traceId?: string } = {},
  ): Promise<Record<string, unknown>> {
    try {
      await this.waitUntilReady();
    } catch (error) {
      if (!options.traceId) throw error;
      const code = error instanceof RuntimeIpcError && isSafeRuntimeErrorCode(error.code)
        ? error.code
        : "RUNTIME_SAFE_MODE";
      throw new RuntimeIpcError(code, undefined, {
        traceId: options.traceId,
        requestStatus: "not_sent",
        failureStage: "sidecar_ipc",
        httpStatus: null,
      });
    }
    if (!this.channel || this.stateValue !== "ready") {
      throw new RuntimeIpcError("RUNTIME_SAFE_MODE", undefined, {
        ...(options.traceId ? { traceId: options.traceId } : {}),
        requestStatus: "not_sent",
        failureStage: "sidecar_ipc",
        httpStatus: null,
      });
    }
    return this.channel.request(method, params, options);
  }

  async health(): Promise<{ status: "healthy" | "degraded" | "safe_mode"; [key: string]: unknown }> {
    if (this.stateValue === "safe_mode") return { status: "safe_mode", code: this.safeErrorCode ?? "RUNTIME_SAFE_MODE" };
    if (this.stateValue === "waiting") return { status: "degraded", code: this.safeErrorCode ?? "RUNTIME_RECOVERY_WAITING" };
    return await this.request("runtime.health", {}) as { status: "healthy" | "degraded" | "safe_mode"; [key: string]: unknown };
  }

  async resumePendingRecovery(): Promise<RuntimeState> {
    if (this.pendingRecoveryPromise) return this.pendingRecoveryPromise;
    const child = this.child;
    const channel = this.channel;
    const context = this.pendingRecoveryContext;
    if (this.stateValue !== "waiting" || !child || !channel || !context || !this.options.recovery) {
      return this.stateValue;
    }
    this.pendingRecoveryPromise = this.completeRecovery(child, channel, context).finally(() => {
      this.pendingRecoveryPromise = undefined;
    });
    return this.pendingRecoveryPromise;
  }

  async close(): Promise<void> {
    this.intentionalStop = true;
    this.activeRecoveryAbort?.abort();
    this.pendingRecoveryContext = undefined;
    const child = this.child;
    this.stateValue = "stopped";
    this.channel?.close(new RuntimeIpcError("IPC_CHANNEL_CLOSED"));
    this.channel = undefined;
    if (!child) return;
    await new Promise<void>((resolve) => {
      let settled = false;
      let timer: NodeJS.Timeout | undefined;
      const finish = () => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        resolve();
      };
      timer = setTimeout(() => {
        this.terminateChild(child);
        finish();
      }, 2_000);
      timer.unref?.();
      child.once("close", finish);
      this.terminateChild(child);
    });
    if (this.child === child) this.child = undefined;
  }

  private async launch(recoveryContext?: RuntimeRecoveryContext): Promise<void> {
    const activeRecoveryContext: RuntimeRecoveryContext | undefined = recoveryContext ?? (this.options.recoverOnStart === true
      ? {
          previous_generation: 0,
          current_generation: this.generation + 1,
          transition_id: randomUUID(),
          previous_exit_code: null,
          previous_exit_kind: "initial_start",
          previous_error_code: null,
        }
      : undefined);
    this.generation = activeRecoveryContext?.current_generation ?? this.generation + 1;
    this.stateValue = this.restartCount === 0 ? "starting" : "restarting";
    this.startupStage = "sidecar_spawn";
    this.handshakeStatus = "not_started";
    this.failureStage = "none";
    this.exitKind = "none";
    this.sidecarExitCode = null;
    this.sidecarSignal = null;
    this.safeErrorCode = null;
    this.lastSidecarLog = undefined;
    this.stderrBuffer = "";
    let child: ChildProcess;
    try {
      const spawnOptions: SpawnOptions = {
        cwd: this.options.cwd,
        env: sidecarEnvironment(this.options.env ?? process.env),
        shell: false,
        windowsHide: true,
        stdio: ["pipe", "pipe", "pipe"],
      };
      child = spawn(this.options.command, [...this.options.args], spawnOptions);
    } catch {
      this.stateValue = "safe_mode";
      this.recordFailure("sidecar_spawn", "SIDECAR_SPAWN_FAILED");
      throw new RuntimeIpcError("SIDECAR_SPAWN_FAILED");
    }
    this.child = child;
    let childClosed = false;
    const childClosedPromise = new Promise<void>((resolve) => {
      child.once("close", () => {
        childClosed = true;
        resolve();
      });
    });
    const stdin = child.stdin;
    const stdout = child.stdout;
    const stderr = child.stderr;
    if (!stdin || !stdout || !stderr) {
      this.stateValue = "safe_mode";
      this.recordFailure("sidecar_spawn", "SIDECAR_SPAWN_FAILED");
      this.terminateChild(child);
      throw new RuntimeIpcError("SIDECAR_SPAWN_FAILED");
    }

    stderr.on("data", (chunk: Buffer | string) => {
      if (this.child === child) this.onStderr(chunk);
    });
    const channel = new NdjsonChannel(stdout, stdin, undefined, (error) => this.onChannelFailure(child, error));
    this.channel = channel;
    child.once("error", () => this.onChildExit(child, new RuntimeIpcError("SIDECAR_SPAWN_FAILED"), { kind: "spawn_error" }));
    child.once("close", (code, signal) => {
      this.onChildExit(child, new RuntimeIpcError("SIDECAR_EXITED"), { kind: "exit", code, signal });
    });

    try {
      this.startupStage = "ipc_handshake";
      this.handshakeStatus = "pending";
      const timeoutMs = this.options.handshakeTimeoutMs
        ?? (this.options.recovery?.policy.health_timeout ?? 5) * 1_000;
      const handshake = await channel.request("runtime.handshake", {}, { timeoutMs });
      assertRuntimeHandshake(handshake);
      if (this.child !== child || this.isSafeMode()) {
        throw new RuntimeIpcError("SIDECAR_EXITED");
      }
      this.handshakeStatus = "succeeded";
    } catch (error) {
      this.fatalChildren.add(child);
      this.stateValue = "safe_mode";
      const spawnFailed = error instanceof RuntimeIpcError && error.code === "SIDECAR_SPAWN_FAILED";
      if (!spawnFailed) this.handshakeStatus = "failed";
      this.recordFailure("ipc_handshake", error instanceof RuntimeIpcError ? error.code : "IPC_HANDSHAKE_FAILED");
      channel.close(error instanceof RuntimeIpcError ? error : new RuntimeIpcError("IPC_HANDSHAKE_FAILED"));
      if (error instanceof RuntimeIpcError && error.code === "SIDECAR_EXITED") {
        await Promise.race([childClosedPromise, delay(100)]);
      }
      if (!childClosed) this.terminateChild(child);
      if (!childClosed) await Promise.race([childClosedPromise, delay(2_000)]);
      throw error instanceof RuntimeIpcError ? error : new RuntimeIpcError("IPC_HANDSHAKE_FAILED");
    }

    if (activeRecoveryContext) {
      const recoveryState = await this.completeRecovery(child, channel, activeRecoveryContext);
      if (this.intentionalStop || this.child !== child || recoveryState !== "ready") return;
    }
    if (this.child !== child || this.isSafeMode()) {
      throw new RuntimeIpcError("SIDECAR_EXITED");
    }
    this.stateValue = "ready";
    this.startupStage = "ready";
  }

  private onStderr(chunk: Buffer | string): void {
    this.stderrBuffer += typeof chunk === "string" ? chunk : chunk.toString("utf8");
    if (Buffer.byteLength(this.stderrBuffer, "utf8") > 64 * 1024 && !this.stderrBuffer.includes("\n")) {
      this.stderrBuffer = "";
      return;
    }
    let newline = this.stderrBuffer.indexOf("\n");
    while (newline >= 0) {
      const line = this.stderrBuffer.slice(0, newline).replace(/\r$/, "");
      this.stderrBuffer = this.stderrBuffer.slice(newline + 1);
      const safeEntry = readSafeSidecarLogLine(line);
      if (safeEntry) {
        this.lastSidecarLog = safeEntry;
        this.options.onLog?.(safeEntry);
        this.options.onDiagnostic?.({ ...this.diagnostics });
      }
      newline = this.stderrBuffer.indexOf("\n");
    }
    if (Buffer.byteLength(this.stderrBuffer, "utf8") > 64 * 1024) this.stderrBuffer = "";
  }

  private onChannelFailure(child: ChildProcess, error: RuntimeIpcError): void {
    if (this.child !== child || this.intentionalStop) return;
    this.recordFailure(this.handshakeStatus === "pending" ? "ipc_handshake" : "sidecar_exit", error.code);
    if (error.code === "SIDECAR_EXITED" && this.handshakeStatus === "pending") {
      this.stateValue = "restarting";
      return;
    }
    if (["PROTOCOL_VERSION_UNSUPPORTED", "SCHEMA_VALIDATION_FAILED", "IPC_JSON_INVALID", "IPC_UTF8_INVALID", "IPC_FRAME_TOO_LARGE", "IPC_RESPONSE_UNMATCHED", "IPC_RESPONSE_TRACE_MISMATCH"].includes(error.code)) {
      this.fatalChildren.add(child);
      this.stateValue = "safe_mode";
      this.terminateChild(child);
      return;
    }
    if (error.code === "SIDECAR_EXITED" || error.code === "IPC_FRAME_TRUNCATED" || error.code === "SIDECAR_IO_FAILED") {
      this.stateValue = "restarting";
      this.terminateChild(child);
    }
  }

  private onChildExit(
    child: ChildProcess,
    error: RuntimeIpcError,
    outcome?: { kind: "spawn_error" } | { kind: "exit"; code: number | null; signal: NodeJS.Signals | null },
  ): void {
    if (this.child !== child) return;
    if (outcome?.kind === "exit") {
      this.sidecarExitCode = outcome.code;
      this.sidecarSignal = outcome.signal;
      this.exitKind = this.classifyExit(child, outcome.code, outcome.signal);
    }
    if (this.intentionalStop) {
      if (outcome?.kind === "exit") this.startupStage = "sidecar_exit";
      if (this.handshakeStatus === "pending") this.handshakeStatus = "failed";
      this.failureStage = "none";
      this.safeErrorCode = null;
      this.channel?.close(new RuntimeIpcError("IPC_CHANNEL_CLOSED"));
      this.channel = undefined;
      this.child = undefined;
      this.stateValue = "stopped";
      this.options.onDiagnostic?.({ ...this.diagnostics });
      return;
    }
    const spawnFailed = outcome?.kind === "spawn_error" || error.code === "SIDECAR_SPAWN_FAILED" && this.startupStage === "sidecar_spawn";
    const failureStage = spawnFailed
      ? "sidecar_spawn"
      : this.startupStage === "ipc_handshake" ? "ipc_handshake" : "sidecar_exit";
    this.recordFailure(failureStage, error.code);
    if (spawnFailed) {
      this.startupStage = "sidecar_spawn";
      this.handshakeStatus = "not_started";
    } else {
      if (this.handshakeStatus === "pending") this.handshakeStatus = "failed";
      this.startupStage = "sidecar_exit";
    }
    this.channel?.close(error);
    this.channel = undefined;
    this.child = undefined;
    this.options.onDiagnostic?.({ ...this.diagnostics });
    if (this.fatalChildren.has(child)) {
      this.stateValue = "safe_mode";
      return;
    }
    const maxRestarts = this.options.recovery?.policy.max_restarts ?? this.options.maxRestarts ?? 1;
    if (this.restartCount >= maxRestarts) {
      this.stateValue = "safe_mode";
      return;
    }
    this.restartCount += 1;
    this.stateValue = "restarting";
    const recoveryContext: RuntimeRecoveryContext = {
      previous_generation: this.generation,
      current_generation: this.generation + 1,
      transition_id: randomUUID(),
      previous_exit_code: outcome?.kind === "exit" ? outcome.code : null,
      previous_exit_kind: this.exitKind,
      previous_error_code: this.safeErrorCode,
    };
    this.restartPromise = this.restartAfterExit(recoveryContext).catch(() => {
      this.stateValue = "safe_mode";
    }).finally(() => {
      this.restartPromise = undefined;
    });
  }

  private async restartAfterExit(context: RuntimeRecoveryContext): Promise<void> {
    const delayMs = this.options.recovery?.backoffMilliseconds(this.restartCount) ?? 0;
    if (delayMs > 0) await delay(delayMs);
    if (this.intentionalStop || this.stateValue !== "restarting") return;
    await this.launch(context);
  }

  private async evaluateRecovery(
    channel: NdjsonChannel,
    context: RuntimeRecoveryContext,
  ): Promise<RuntimeRecoveryOutcome> {
    if (!this.options.recovery) {
      // Without a recovery coordinator there is no checkpoint to restore, so the
      // pre-existing semantics apply: a crash restarts the sidecar back to ready.
      return { status: "RESUME", safeErrorCode: null };
    }
    const controller = new AbortController();
    this.activeRecoveryAbort = controller;
    try {
      return await this.options.recovery.recoverAfterRestart(channel, context, controller.signal);
    } finally {
      if (this.activeRecoveryAbort === controller) this.activeRecoveryAbort = undefined;
    }
  }

  private async completeRecovery(
    child: ChildProcess,
    channel: NdjsonChannel,
    context: RuntimeRecoveryContext,
  ): Promise<RuntimeState> {
    this.startupStage = "recovery";
    const outcome = await this.evaluateRecovery(channel, context);
    const decision = outcome.status;
    if (this.intentionalStop || this.child !== child || this.channel !== channel) return this.stateValue;
    if (decision === "RESUME") {
      this.pendingRecoveryContext = undefined;
      this.failureStage = "none";
      this.safeErrorCode = null;
      this.startupStage = "ready";
      this.stateValue = "ready";
      this.options.onDiagnostic?.({ ...this.diagnostics });
      return this.stateValue;
    }
    if (decision === "WAIT") {
      this.pendingRecoveryContext = context;
      this.stateValue = "waiting";
      this.safeErrorCode = outcome.safeErrorCode ?? "RUNTIME_RECOVERY_WAITING";
      this.options.onDiagnostic?.({ ...this.diagnostics });
      return this.stateValue;
    }
    this.fatalChildren.add(child);
    this.stateValue = "safe_mode";
    const safeErrorCode = outcome.safeErrorCode ?? "RUNTIME_RECOVERY_REQUIRED";
    this.recordFailure("sidecar_exit", safeErrorCode);
    this.safeErrorCode = safeErrorCode;
    this.options.onDiagnostic?.({ ...this.diagnostics });
    channel.close(new RuntimeIpcError(safeErrorCode));
    this.terminateChild(child);
    await Promise.race([new Promise<void>((resolve) => child.once("close", () => resolve())), delay(2_000)]);
    // The child exit handler runs asynchronously and rewrites the startup stage
    // to sidecar_exit; keep the recovery stage that caused safe mode.
    this.startupStage = "recovery";
    this.options.onDiagnostic?.({ ...this.diagnostics });
    return this.stateValue;
  }

  private async waitUntilReady(): Promise<void> {
    if (this.isWaiting()) throw new RuntimeIpcError(this.safeErrorCode ?? "RUNTIME_RECOVERY_WAITING");
    if (this.stateValue === "ready") return;
    if (this.stateValue === "stopped") await this.start();
    const recoveryDeadline = Date.now() + 10_000;
    while (this.stateValue === "restarting" && !this.restartPromise && Date.now() < recoveryDeadline) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    if (this.restartPromise) await this.restartPromise;
    if (this.startPromise) await this.startPromise;
    if (this.isWaiting()) throw new RuntimeIpcError(this.safeErrorCode ?? "RUNTIME_RECOVERY_WAITING");
    if (!this.isReady()) throw new RuntimeIpcError("RUNTIME_SAFE_MODE");
  }

  private classifyExit(child: ChildProcess, code: number | null, signal: NodeJS.Signals | null): RuntimeExitKind {
    if (this.lastSidecarLog?.event === "runtime_unhandled_exception") return "unhandled_exception";
    if (this.intentionalStop || this.terminationRequested.has(child)) return "controlled";
    if (signal !== null || (code !== null && (code >>> 0) >= 0xc0000000)) return "process_crash";
    if (code === 0) return "normal_exit";
    return "unexpected_exit";
  }

  private terminateChild(child: ChildProcess): void {
    if (child.exitCode !== null || child.signalCode !== null) return;
    if (child.kill()) this.terminationRequested.add(child);
  }

  private isSafeMode(): boolean {
    return this.stateValue === "safe_mode";
  }

  private isReady(): boolean {
    return this.stateValue === "ready";
  }

  private isWaiting(): boolean {
    return this.stateValue === "waiting";
  }

  private recordFailure(stage: Exclude<RuntimeDiagnostics["failureStage"], "none" | "unknown">, code: string): void {
    if (this.failureStage === "none") this.failureStage = stage;
    if (this.safeErrorCode === null) this.safeErrorCode = isSafeRuntimeErrorCode(code) ? code : "UNKNOWN";
  }
}
