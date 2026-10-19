import { delimiter, resolve } from "node:path";

export type SidecarLaunchOptions = {
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
};

export function createSidecarLaunchOptions(
  repositoryRoot: string,
  environment: NodeJS.ProcessEnv,
  platform: "darwin" | "win32" | "linux",
): SidecarLaunchOptions {
  const sourceRuntimePath = resolve(repositoryRoot, "runtimes/python-worker");
  const pythonPaths = [environment.PYTHONPATH, sourceRuntimePath].filter((path): path is string => Boolean(path));

  return {
    command: environment.SHULIU_PYTHON_EXECUTABLE ?? (platform === "win32" ? "python" : "python3"),
    args: ["-m", "shuliu_runtime.ipc.server"],
    cwd: repositoryRoot,
    env: {
      ...environment,
      PYTHONPATH: pythonPaths.join(delimiter),
    },
  };
}
