import { delimiter, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { createSidecarLaunchOptions } from "../../src/main/runtime/launch-options";

describe("Python Sidecar launch options", () => {
  it("keeps the source runtime importable in development without dropping configured paths", () => {
    const repositoryRoot = resolve("/workspace/shuliu-agent");
    const launch = createSidecarLaunchOptions(repositoryRoot, {
      SHULIU_PYTHON_EXECUTABLE: "/venv/bin/python",
      PYTHONPATH: "/workspace/shared-python",
    }, "darwin");

    expect(launch.command).toBe("/venv/bin/python");
    expect(launch.args).toEqual(["-m", "shuliu_runtime.ipc.server"]);
    expect(launch.cwd).toBe(repositoryRoot);
    expect(launch.env.PYTHONPATH?.split(delimiter)).toEqual([
      "/workspace/shared-python",
      resolve(repositoryRoot, "runtimes/python-worker"),
    ]);
  });

  it("defaults to the platform Python executable", () => {
    const repositoryRoot = resolve("/workspace/shuliu-agent");
    expect(createSidecarLaunchOptions(repositoryRoot, {}, "win32").command).toBe("python");
    expect(createSidecarLaunchOptions(repositoryRoot, {}, "darwin").command).toBe("python3");
  });
});
