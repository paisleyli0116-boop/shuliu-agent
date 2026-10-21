import { spawn, type ChildProcess } from "node:child_process";
import type { SpawnOptions } from "node:child_process";
import { isSafeRuntimeErrorCode, readSafeSidecarLogLine, type RuntimeDiagnostics, type SafeRuntimeLog } from "./diagnostics";
import { NdjsonChannel, RuntimeIpcError } from "./ndjson-channel";
import { assertRuntimeHandshake } from "./handshake";

export type RuntimeState = "stopped" | "starting" | "ready" | "restarting" | "safe_mode";

export type SidecarSupervisorOptions = {
  command: string;
  args: string[];
  cwd: string;
  env?: NodeJS.ProcessEnv;
  handshakeTimeoutMs?: number;
  maxRestarts?: number;
  onLog?: (entry: SafeRuntimeLog) => void;
};

const SIDECAR_ENV_KEYS = new Set([
  "PATH", "Path", "SystemRoot", "WINDIR", "HOME", "USERPROFILE", "TMP", "TEMP", "TMPDIR",
  "SHULIU_DATA_DIR", "SHULIU_MODEL_PROFILES_JSON", "SHULIU_ORGANIZATION_ID",
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
  private intentionalStop = false;
  private readonly fatalChildren = new WeakSet<ChildProcess>();
  private stderrBuffer = "";
  private startupStage: RuntimeDiagnostics["startupStage"] = "not_started";
  private handshakeStatus: RuntimeDiagnostics["handshakeStatus"] = "not_started";
  private failureStage: RuntimeDiagnostics["failureStage"] = "none";
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
    options: { timeoutMs?: number; signal?: AbortSignal } = {},
  ): Promise<Record<string, unknown>> {
    await this.waitUntilReady();
    if (!this.channel || this.stateValue !== "ready") throw new RuntimeIpcError("RUNTIME_SAFE_MODE");
    return this.channel.request(method, params, options);
  }

  async health(): Promise<{ status: "healthy" | "degraded" | "safe_mode"; [key: string]: unknown }> {
    if (this.stateValue === "safe_mode") return { status: "safe_mode" };
    return await this.request("runtime.health", {}) as { status: "healthy" | "degraded" | "safe_mode"; [key: string]: unknown };
  }

  async close(): Promise<void> {
    this.intentionalStop = true;
    const child = this.child;
    this.stateValue = "stopped";
    this.channel?.close(new RuntimeIpcError("IPC_CHANNEL_CLOSED"));
    this.channel = undefined;
    if (!child || child.exitCode !== null || child.signalCode !== null) {
      this.child = undefined;
      return;
    }
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        child.kill();
        resolve();
      }, 2_000);
      timer.unref?.();
      child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      child.kill();
    });
    if (this.child === child) this.child = undefined;
  }

  private async launch(): Promise<void> {
    this.stateValue = this.restartCount === 0 ? "starting" : "restarting";
    this.startupStage = "sidecar_spawn";
    this.handshakeStatus = "not_started";
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
      child.kill();
      throw new RuntimeIpcError("SIDECAR_SPAWN_FAILED");
    }

    this.stderrBuffer = "";
    stderr.on("data", (chunk: Buffer | string) => this.onStderr(chunk));
    const channel = new NdjsonChannel(stdout, stdin, undefined, (error) => this.onChannelFailure(child, error));
    this.channel = channel;
    child.once("error", () => this.onChildExit(child, new RuntimeIpcError("SIDECAR_SPAWN_FAILED"), { kind: "spawn_error" }));
    child.once("exit", (code, signal) => {
      this.onChildExit(child, new RuntimeIpcError("SIDECAR_EXITED"), { kind: "exit", code, signal });
    });

    try {
      this.startupStage = "ipc_handshake";
      this.handshakeStatus = "pending";
      const timeoutMs = this.options.handshakeTimeoutMs ?? 5_000;
      const handshake = await channel.request("runtime.handshake", {}, { timeoutMs });
      assertRuntimeHandshake(handshake);
      if (this.child !== child || this.isSafeMode()) {
        throw new RuntimeIpcError("SIDECAR_EXITED");
      }
      this.stateValue = "ready";
      this.startupStage = "ready";
      this.handshakeStatus = "succeeded";
    } catch (error) {
      this.fatalChildren.add(child);
      this.stateValue = "safe_mode";
      this.handshakeStatus = "failed";
      this.recordFailure("ipc_handshake", error instanceof RuntimeIpcError ? error.code : "IPC_HANDSHAKE_FAILED");
      channel.close(error instanceof RuntimeIpcError ? error : new RuntimeIpcError("IPC_HANDSHAKE_FAILED"));
      if (error instanceof RuntimeIpcError && error.code === "SIDECAR_EXITED") {
        await Promise.race([childClosedPromise, delay(100)]);
      }
      if (!childClosed && child.exitCode === null && child.signalCode === null) child.kill();
      if (!childClosed) await Promise.race([childClosedPromise, delay(2_000)]);
      throw error instanceof RuntimeIpcError ? error : new RuntimeIpcError("IPC_HANDSHAKE_FAILED");
    }
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
      if (child.exitCode === null && child.signalCode === null) child.kill();
      return;
    }
    if (error.code === "SIDECAR_EXITED" || error.code === "IPC_FRAME_TRUNCATED" || error.code === "SIDECAR_IO_FAILED") {
      this.stateValue = "restarting";
      if (child.exitCode === null && child.signalCode === null) child.kill();
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
    }
    const spawnFailed = outcome?.kind === "spawn_error" || error.code === "SIDECAR_SPAWN_FAILED" && this.startupStage === "sidecar_spawn";
    const failureStage = spawnFailed
      ? "sidecar_spawn"
      : this.startupStage === "ipc_handshake" ? "ipc_handshake" : "sidecar_exit";
    this.recordFailure(failureStage, error.code);
    if (this.handshakeStatus === "pending") this.handshakeStatus = "failed";
    if (!spawnFailed) this.startupStage = "sidecar_exit";
    this.channel?.close(error);
    this.channel = undefined;
    this.child = undefined;
    if (this.intentionalStop) {
      this.stateValue = "stopped";
      return;
    }
    if (this.fatalChildren.has(child)) {
      this.stateValue = "safe_mode";
      return;
    }
    if (this.restartCount >= (this.options.maxRestarts ?? 1)) {
      this.stateValue = "safe_mode";
      return;
    }
    this.restartCount += 1;
    this.stateValue = "restarting";
    this.restartPromise = this.launch().catch(() => {
      this.stateValue = "safe_mode";
    }).finally(() => {
      this.restartPromise = undefined;
    });
  }

  private async waitUntilReady(): Promise<void> {
    if (this.stateValue === "ready") return;
    if (this.stateValue === "stopped") await this.start();
    const recoveryDeadline = Date.now() + 10_000;
    while (this.stateValue === "restarting" && !this.restartPromise && Date.now() < recoveryDeadline) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    if (this.restartPromise) await this.restartPromise;
    if (this.startPromise) await this.startPromise;
    if (!this.isReady()) throw new RuntimeIpcError("RUNTIME_SAFE_MODE");
  }

  private isSafeMode(): boolean {
    return this.stateValue === "safe_mode";
  }

  private isReady(): boolean {
    return this.stateValue === "ready";
  }

  private recordFailure(stage: Exclude<RuntimeDiagnostics["failureStage"], "none" | "unknown">, code: string): void {
    if (this.failureStage === "none") this.failureStage = stage;
    if (this.safeErrorCode === null) this.safeErrorCode = isSafeRuntimeErrorCode(code) ? code : "UNKNOWN";
  }
}
