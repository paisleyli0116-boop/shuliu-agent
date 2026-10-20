import { spawn, type ChildProcess } from "node:child_process";
import type { SpawnOptions } from "node:child_process";
import { NdjsonChannel, RuntimeIpcError } from "./ndjson-channel";
import { assertRuntimeHandshake } from "./handshake";

export type RuntimeState = "stopped" | "starting" | "ready" | "restarting" | "safe_mode";
export type SafeRuntimeLog = { level: "DEBUG" | "INFO" | "WARN" | "ERROR"; event: string; code: string };

export type SidecarSupervisorOptions = {
  command: string;
  args: string[];
  cwd: string;
  env?: NodeJS.ProcessEnv;
  handshakeTimeoutMs?: number;
  maxRestarts?: number;
  onLog?: (entry: SafeRuntimeLog) => void;
};

const safeLogLevel = new Set(["DEBUG", "INFO", "WARN", "ERROR"]);
const SAFE_IDENTIFIER = /^[A-Z0-9_.:-]{1,96}$/;
const SIDECAR_ENV_KEYS = new Set([
  "PATH", "Path", "SystemRoot", "WINDIR", "HOME", "USERPROFILE", "TMP", "TEMP", "TMPDIR",
  "SHULIU_DATA_DIR",
  "LANG", "LC_ALL", "PYTHONPATH", "PYTHONUTF8", "PYTHONIOENCODING", "VIRTUAL_ENV",
]);

function sidecarEnvironment(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  return {
    ...Object.fromEntries(
    Object.entries(source).filter(([key, value]) => SIDECAR_ENV_KEYS.has(key) && value !== undefined),
    ),
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
  };
}

function readSafeStderrLine(line: string): SafeRuntimeLog | undefined {
  try {
    const value: unknown = JSON.parse(line);
    if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
    const record = value as Record<string, unknown>;
    if (
      typeof record.level !== "string" ||
      !safeLogLevel.has(record.level) ||
      typeof record.event !== "string" ||
      !SAFE_IDENTIFIER.test(record.event) ||
      typeof record.code !== "string" ||
      !SAFE_IDENTIFIER.test(record.code)
    ) return undefined;
    return { level: record.level as SafeRuntimeLog["level"], event: record.event, code: record.code };
  } catch {
    return undefined;
  }
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

  constructor(private readonly options: SidecarSupervisorOptions) {}

  get state(): RuntimeState {
    return this.stateValue;
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
      throw new RuntimeIpcError("SIDECAR_SPAWN_FAILED");
    }
    this.child = child;
    const stdin = child.stdin;
    const stdout = child.stdout;
    const stderr = child.stderr;
    if (!stdin || !stdout || !stderr) {
      this.stateValue = "safe_mode";
      child.kill();
      throw new RuntimeIpcError("SIDECAR_SPAWN_FAILED");
    }

    this.stderrBuffer = "";
    stderr.on("data", (chunk: Buffer | string) => this.onStderr(chunk));
    const channel = new NdjsonChannel(stdout, stdin, undefined, (error) => this.onChannelFailure(child, error));
    this.channel = channel;
    child.once("error", () => this.onChildExit(child, new RuntimeIpcError("SIDECAR_SPAWN_FAILED")));
    child.once("exit", (code, signal) => {
      const error = new RuntimeIpcError("SIDECAR_EXITED", `Python Sidecar exited (${signal ?? code ?? "unknown"})`);
      this.onChildExit(child, error);
    });

    try {
      const timeoutMs = this.options.handshakeTimeoutMs ?? 5_000;
      const handshake = await channel.request("runtime.handshake", {}, { timeoutMs });
      assertRuntimeHandshake(handshake);
      if (this.child !== child || this.isSafeMode()) {
        throw new RuntimeIpcError("SIDECAR_EXITED");
      }
      this.stateValue = "ready";
    } catch (error) {
      this.fatalChildren.add(child);
      this.stateValue = "safe_mode";
      channel.close(error instanceof RuntimeIpcError ? error : new RuntimeIpcError("IPC_HANDSHAKE_FAILED"));
      if (child.exitCode === null && child.signalCode === null) child.kill();
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
      const safeEntry = readSafeStderrLine(line);
      if (safeEntry) this.options.onLog?.(safeEntry);
      newline = this.stderrBuffer.indexOf("\n");
    }
    if (Buffer.byteLength(this.stderrBuffer, "utf8") > 64 * 1024) this.stderrBuffer = "";
  }

  private onChannelFailure(child: ChildProcess, error: RuntimeIpcError): void {
    if (this.child !== child || this.intentionalStop) return;
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

  private onChildExit(child: ChildProcess, error: RuntimeIpcError): void {
    if (this.child !== child) return;
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
}
