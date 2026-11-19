import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { afterAll, afterEach, describe, expect, it } from "vitest";

const REPO_ROOT = process.cwd();
const SIDECAR_SCRIPT = join(
  REPO_ROOT,
  "tests",
  "e2e",
  "fixtures",
  "lifecycle_sidecar.py",
);
const UV_RUN = [
  "--from",
  "uv==0.12.20",
  "uv",
  "run",
  "--locked",
  "--package",
  "shuliu-python-runtime",
  "python",
  SIDECAR_SCRIPT,
];

type SidecarResponse = {
  id: number;
  ok: boolean;
  result?: Record<string, unknown>;
  error?: { code: string; message: string };
};

class SidecarError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(`${code}: ${message}`);
    this.code = code;
  }
}

class LifecycleSidecar {
  private child: ChildProcessWithoutNullStreams;
  private nextId = 1;
  private readonly pending = new Map<
    number,
    { resolve: (value: SidecarResponse) => void; reject: (error: Error) => void }
  >();
  private stderr: string[] = [];
  private readonly stateDir: string;
  private exitListeners: Array<() => void> = [];

  constructor(stateDir: string) {
    this.stateDir = stateDir;
    this.child = this.spawn();
  }

  private spawn(): ChildProcessWithoutNullStreams {
    const pythonPath = [join(REPO_ROOT, "runtimes", "python-worker")];
    if (process.env.PYTHONPATH) {
      pythonPath.push(process.env.PYTHONPATH);
    }
    const child = spawn("uvx", [...UV_RUN, this.stateDir], {
      cwd: REPO_ROOT,
      env: { ...process.env, PYTHONPATH: pythonPath.join(";") },
      stdio: ["pipe", "pipe", "pipe"],
    }) as ChildProcessWithoutNullStreams;
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string) => {
      this.stderr.push(chunk);
    });
    const rl = createInterface({ input: child.stdout });
    rl.on("line", (line) => {
      if (!line.trim()) {
        return;
      }
      const response = JSON.parse(line) as SidecarResponse;
      const pending = this.pending.get(response.id);
      if (!pending) {
        return;
      }
      this.pending.delete(response.id);
      pending.resolve(response);
    });
    child.on("exit", () => {
      for (const pending of this.pending.values()) {
        pending.reject(
          new Error(
            `sidecar exited before answering; stderr: ${this.stderr.join("").slice(-2000)}`,
          ),
        );
      }
      this.pending.clear();
      const listeners = this.exitListeners;
      this.exitListeners = [];
      for (const listener of listeners) {
        listener();
      }
    });
    return child;
  }

  request(action: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    const id = this.nextId++;
    const payload = JSON.stringify({ id, action, params }) + "\n";
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`sidecar timed out for ${action}`));
      }, 90_000);
      this.pending.set(id, {
        resolve: (response) => {
          clearTimeout(timer);
          if (response.ok) {
            resolve(response.result ?? {});
          } else {
            reject(
              new SidecarError(
                response.error?.code ?? "SIDECAR_UNKNOWN",
                response.error?.message ?? "sidecar error",
              ),
            );
          }
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });
      this.child.stdin.write(payload);
    });
  }

  /** Kill the current process and boot a fresh one on the same state dir. */
  async restart(): Promise<void> {
    await this.stopProcess();
    this.stderr = [];
    this.child = this.spawn();
  }

  async stop(): Promise<void> {
    await this.stopProcess();
  }

  private stopProcess(): Promise<void> {
    if (this.child.exitCode !== null || this.child.killed) {
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      this.exitListeners.push(resolve);
      this.child.kill();
      setTimeout(resolve, 5_000).unref?.();
    });
  }
}

const liveSidecars: LifecycleSidecar[] = [];
const stateDirs: string[] = [];

function makeSidecar(): LifecycleSidecar {
  const stateDir = mkdtempSync(join(tmpdir(), "shuliu-e2e-lifecycle-"));
  stateDirs.push(stateDir);
  const sidecar = new LifecycleSidecar(stateDir);
  liveSidecars.push(sidecar);
  return sidecar;
}

afterEach(async () => {
  for (const sidecar of liveSidecars) {
    await sidecar.stop();
  }
  liveSidecars.length = 0;
});

afterAll(() => {
  for (const dir of stateDirs) {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      // Windows may briefly hold locks; the temp dir is cleaned by the OS.
    }
  }
});

function findEvent(
  events: Array<Record<string, unknown>>,
  eventType: string,
): Record<string, unknown> | undefined {
  return events.find((event) => event.event_type === eventType);
}

function expectStepError(
  run: Record<string, unknown>,
  errorCode: string,
): void {
  const steps = run.steps as Array<Record<string, unknown>>;
  expect(
    steps.some((step) => step.error_code === errorCode),
    `expected step error ${errorCode}`,
  ).toBe(true);
}

describe("foundation lifecycle end to end", () => {
  it(
    "completes the approval-gated foundation lifecycle",
    async () => {
      const sidecar = makeSidecar();
      const run = await sidecar.request("start", {
        mode: "approval",
        document_id: "doc-1",
      });
      expect(run.status).toBe("WAITING");
      expect(run.wait_reason).toBe("APPROVAL");
      expect(typeof run.resume_token).toBe("string");
      expect(typeof run.approval_id).toBe("string");
      const resumeToken = run.resume_token as string;
      const approvalId = run.approval_id as string;

      let events = (await sidecar.request("events")).events as Array<
        Record<string, unknown>
      >;
      expect(findEvent(events, "WORKFLOW_RUN_CREATED")).toBeDefined();
      expect(findEvent(events, "WORKFLOW_RUN_WAITING")).toBeDefined();
      const requested = findEvent(events, "APPROVAL_REQUESTED");
      expect(requested).toBeDefined();

      await expect(
        sidecar.request("decide", {
          approval_id: approvalId,
          approver_id: "agent-1",
          decision: "APPROVE",
        }),
      ).rejects.toMatchObject({ code: "APPROVAL_SELF_APPROVAL" });
      await expect(
        sidecar.request("decide", {
          approval_id: approvalId,
          approver_id: "approver-2",
          decision: "APPROVE",
        }),
      ).rejects.toMatchObject({ code: "APPROVAL_NOT_AUTHORIZED" });

      const decision = await sidecar.request("decide", {
        approval_id: approvalId,
        approver_id: "approver-1",
        decision: "APPROVE",
      });
      expect(decision.status).toBe("APPROVED");

      await expect(
        sidecar.request("resume", {
          run_id: run.run_id,
          resume_token: "stale-fence-token",
        }),
      ).rejects.toMatchObject({ code: "WORKFLOW_RESUME_REJECTED" });

      const resumed = await sidecar.request("resume", {
        run_id: run.run_id,
        resume_token: resumeToken,
      });
      expect(resumed.status).toBe("SUCCEEDED");
      expect(resumed.resume_token).toBeNull();

      events = (await sidecar.request("events")).events as Array<
        Record<string, unknown>
      >;
      expect(findEvent(events, "WORKFLOW_RUN_SUCCEEDED")).toBeDefined();
      expect(findEvent(events, "TOOL_EXECUTION_COMPLETED")).toBeDefined();
      expect(findEvent(events, "APPROVAL_APPROVED")).toBeDefined();
      expect(findEvent(events, "APPROVAL_CONSUMED")).toBeDefined();

      const stats = await sidecar.request("stats");
      expect(stats.executor_calls).toBe(1);
      expect(stats.side_effects).toBe(1);
    },
    120_000,
  );

  it(
    "runs a low-risk document write without approval",
    async () => {
      const sidecar = makeSidecar();
      const run = await sidecar.request("start", {
        mode: "direct",
        document_id: "doc-low",
      });
      expect(run.status).toBe("SUCCEEDED");
      expect(run.approval_id).toBeUndefined();

      const events = (await sidecar.request("events")).events as Array<
        Record<string, unknown>
      >;
      expect(findEvent(events, "TOOL_EXECUTION_COMPLETED")).toBeDefined();
      expect(findEvent(events, "APPROVAL_REQUESTED")).toBeUndefined();
    },
    120_000,
  );

  it(
    "keeps policy denial fail closed",
    async () => {
      const sidecar = makeSidecar();
      const run = await sidecar.request("start", {
        mode: "direct",
        document_id: "doc-restricted",
        classification: "RESTRICTED",
      });
      expect(run.status).toBe("FAILED");
      expect(run.error_code).toBe("POLICY_DENIED");
      expectStepError(run, "POLICY_DENIED");

      const events = (await sidecar.request("events")).events as Array<
        Record<string, unknown>
      >;
      expect(findEvent(events, "TOOL_EXECUTION_DENIED")).toBeDefined();
      expect(findEvent(events, "APPROVAL_REQUESTED")).toBeUndefined();

      const stats = await sidecar.request("stats");
      expect(stats.side_effects).toBe(0);
    },
    120_000,
  );

  it(
    "propagates an approval rejection to a failed run",
    async () => {
      const sidecar = makeSidecar();
      const run = await sidecar.request("start", {
        mode: "approval",
        document_id: "doc-reject",
      });
      const decision = await sidecar.request("decide", {
        approval_id: run.approval_id as string,
        approver_id: "approver-1",
        decision: "REJECT",
      });
      expect(decision.status).toBe("REJECTED");

      const failed = await sidecar.request("resume", {
        run_id: run.run_id,
        resume_token: run.resume_token as string,
      });
      expect(failed.status).toBe("FAILED");
      expectStepError(failed, "APPROVAL_REJECTED");

      const events = (await sidecar.request("events")).events as Array<
        Record<string, unknown>
      >;
      expect(findEvent(events, "APPROVAL_REJECTED")).toBeDefined();
      const stats = await sidecar.request("stats");
      expect(stats.side_effects).toBe(0);
    },
    120_000,
  );

  it(
    "recovers a waiting run in a fresh process after a crash",
    async () => {
      const sidecar = makeSidecar();
      const run = await sidecar.request("start", {
        mode: "approval",
        document_id: "doc-crash",
      });
      expect(run.status).toBe("WAITING");
      const resumeToken = run.resume_token as string;
      const approvalId = run.approval_id as string;

      await sidecar.restart();

      const persisted = await sidecar.request("get_run", {
        run_id: run.run_id,
      });
      expect(persisted.status).toBe("WAITING");
      expect(persisted.wait_reason).toBe("APPROVAL");

      const decision = await sidecar.request("decide", {
        approval_id: approvalId,
        approver_id: "approver-1",
        decision: "APPROVE",
      });
      expect(decision.status).toBe("APPROVED");

      const resumed = await sidecar.request("resume", {
        run_id: run.run_id,
        resume_token: resumeToken,
      });
      expect(resumed.status).toBe("SUCCEEDED");

      const stats = await sidecar.request("stats");
      expect(stats.executor_calls).toBe(1);
      expect(stats.side_effects).toBe(1);
    },
    180_000,
  );

  it(
    "keeps an unknown tool dispatch waiting for reconciliation",
    async () => {
      const sidecar = makeSidecar();
      await sidecar.request("set_executor_failures", { times: 1 });
      const run = await sidecar.request("start", {
        mode: "approval",
        document_id: "doc-ack-loss",
      });
      await sidecar.request("decide", {
        approval_id: run.approval_id as string,
        approver_id: "approver-1",
        decision: "APPROVE",
      });

      const waiting = await sidecar.request("resume", {
        run_id: run.run_id,
        resume_token: run.resume_token as string,
      });
      expect(waiting.status).toBe("WAITING");
      expect(waiting.wait_reason).toBe("EXTERNAL");
      expectStepError(waiting, "TOOL_EXECUTION_UNKNOWN");

      // A repeat resume must not bypass the reconciliation gate.
      const gated = await sidecar.request("resume", {
        run_id: run.run_id,
        resume_token: waiting.resume_token as string,
      });
      expect(gated.status).toBe("WAITING");
      expectStepError(gated, "RECONCILIATION_REQUIRED");

      const stats = await sidecar.request("stats");
      expect(stats.side_effects).toBe(0);
    },
    120_000,
  );

  it(
    "fails closed when the actor ACL is revoked",
    async () => {
      const sidecar = makeSidecar();
      await sidecar.request("revoke_acl", { revoked: true });
      const run = await sidecar.request("start", {
        mode: "approval",
        document_id: "doc-acl",
      });
      expect(run.status).toBe("FAILED");
      expect(run.error_code).toBe("IDENTITY_PERMISSION_DENIED");
      expectStepError(run, "IDENTITY_PERMISSION_DENIED");

      const events = (await sidecar.request("events")).events as Array<
        Record<string, unknown>
      >;
      expect(findEvent(events, "APPROVAL_REQUESTED")).toBeUndefined();
      const stats = await sidecar.request("stats");
      expect(stats.side_effects).toBe(0);
    },
    120_000,
  );

  it(
    "invalidates a tampered approval through the bad update",
    async () => {
      const sidecar = makeSidecar();
      const run = await sidecar.request("start", {
        mode: "approval",
        document_id: "doc-tamper",
      });
      await sidecar.request("decide", {
        approval_id: run.approval_id as string,
        approver_id: "approver-1",
        decision: "APPROVE",
      });

      const tampered = await sidecar.request("tamper", { run_id: run.run_id });
      expect(tampered.status).toBe("WAITING");

      const failed = await sidecar.request("resume", {
        run_id: run.run_id,
        resume_token: run.resume_token as string,
      });
      expect(failed.status).toBe("FAILED");
      expectStepError(failed, "APPROVAL_CONTEXT_CHANGED");

      const events = (await sidecar.request("events")).events as Array<
        Record<string, unknown>
      >;
      expect(findEvent(events, "APPROVAL_INVALIDATED")).toBeDefined();
      const stats = await sidecar.request("stats");
      expect(stats.side_effects).toBe(0);
    },
    120_000,
  );
});
