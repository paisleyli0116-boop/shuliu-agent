import { delimiter, resolve } from "node:path";

export type SidecarLaunchPaths = {
  packaged?: boolean;
  resourcesPath?: string;
  userDataPath?: string;
};

export type SidecarLaunchOptions = {
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
  handshakeTimeoutMs?: number;
};

export function createSidecarLaunchOptions(
  repositoryRoot: string,
  environment: NodeJS.ProcessEnv,
  platform: "darwin" | "win32" | "linux",
  paths: SidecarLaunchPaths = {},
): SidecarLaunchOptions {
  if (paths.packaged) {
    if (platform !== "win32" || !paths.resourcesPath || !paths.userDataPath) {
      throw new Error("PACKAGED_SIDECAR_PATHS_INVALID");
    }
    const env: NodeJS.ProcessEnv = { ...environment, SHULIU_DATA_DIR: paths.userDataPath };
    delete env.PYTHONPATH;
    delete env.SHULIU_PYTHON_EXECUTABLE;
    return {
      command: resolve(paths.resourcesPath, "python", "ShuLiuRuntime.exe"),
      args: [],
      cwd: paths.userDataPath,
      env,
      // The frozen Windows one-file Sidecar must extract and initialize before answering IPC.
      // Keep startup bounded; runtime health still must pass within the existing 90-second probe.
      handshakeTimeoutMs: 15_000,
    };
  }

  const sourceRuntimePath = resolve(repositoryRoot, "runtimes/python-worker");
  const pythonPaths = [environment.PYTHONPATH, sourceRuntimePath].filter((path): path is string => Boolean(path));
  return {
    command: environment.SHULIU_PYTHON_EXECUTABLE ?? (platform === "win32" ? "python" : "python3"),
    args: ["-m", "shuliu_runtime.ipc.server"],
    cwd: repositoryRoot,
    env: {
      ...environment,
      ...(paths.userDataPath ? { SHULIU_DATA_DIR: paths.userDataPath } : {}),
      PYTHONPATH: pythonPaths.join(delimiter),
    },
  };
}
