import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parse_event } from "../../packages/event-model/src/index.js";

const here = dirname(fileURLToPath(import.meta.url));
const repository = resolve(here, "../..");

describe("TypeScript and Python event contract", () => {
  it("round trips a TypeScript event through the Python serializer", () => {
    const event = {
      event_id: "evt-contract-01",
      schema_version: 1,
      timestamp: "2026-10-17T08:00:00.000Z",
      organization_id: "org-01",
      trace_id: "trace-01",
      event_type: "task.started",
      payload: { task_id: "task-01" },
    };
    const python = [
      "import json, sys",
      "from shuliu_runtime.events.models import event_from_dict, event_to_dict",
      "print(json.dumps(event_to_dict(event_from_dict(json.load(sys.stdin))), separators=(',', ':'))) ",
    ].join("\n");
    const pythonPath = resolve(process.cwd(), "runtimes/python-worker");
    // Prefer the project venv created by `uv sync`: spawning uvx needs a
    // writable uv tool directory and can exceed the test timeout while it
    // initializes an environment. Fall back to uvx when no venv exists.
    const venvPython = join(
      repository,
      ".venv",
      process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
    );
    const useVenv = existsSync(venvPython);
    const result = spawnSync(
      useVenv ? venvPython : "uvx",
      useVenv
        ? ["-c", python]
        : ["--from", "uv==0.12.20", "uv", "run", "--locked", "--package", "shuliu-python-runtime", "python", "-c", python],
      {
        cwd: process.cwd(),
        encoding: "utf8",
        env: { ...process.env, PYTHONPATH: pythonPath },
        input: JSON.stringify(event),
      },
    );

    expect(result.status, result.stderr).toBe(0);
    expect(parse_event(JSON.parse(result.stdout))).toEqual(event);
  });
});
