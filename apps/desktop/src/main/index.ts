import { app, BrowserWindow, ipcMain, safeStorage } from "electron";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { runHealthProbe } from "./health-probe.js";
import type { RuntimeHealth } from "../preload/api.js";
import { registerRuntimeIpc } from "./runtime/ipc.js";
import { SidecarSupervisor } from "./runtime/supervisor.js";
import { createSidecarLaunchOptions } from "./runtime/launch-options.js";
import { consumeProviderCanarySession } from "./runtime/canary-session.js";
import { appendProviderConnectionDiagnostic, appendRuntimeDiagnostic } from "./runtime/diagnostic-journal.js";
import { appendDesktopStartupDiagnostic } from "./runtime/startup-diagnostics.js";
import { RuntimeProviderService } from "./ipc/providers.js";
import { createDemoDesktopServices } from "./services/demo-desktop-services.js";
import { registerDesktopIpc } from "./ipc/desktop-ipc.js";
import { createSecureWindowWebPreferences, isTrustedRendererRequest } from "./window-security.js";

function recordStartup(value: Parameters<typeof appendDesktopStartupDiagnostic>[1]): void {
  try {
    appendDesktopStartupDiagnostic(app.getPath("userData"), value);
  } catch {
    // Diagnostics must never prevent the Desktop from starting.
  }
}

recordStartup({ stage: "electron_main", status: "started" });
process.on("uncaughtExceptionMonitor", () => {
  recordStartup({ stage: "electron_main", status: "failed", code: "DESKTOP_UNCAUGHT_EXCEPTION" });
});
process.on("exit", (exitCode) => {
  recordStartup({
    stage: "electron_exit",
    status: exitCode === 0 ? "succeeded" : "failed",
    code: exitCode === 0 ? null : "DESKTOP_EXIT_NONZERO",
    exitCode,
  });
});

const appPath = app.getAppPath();
const userDataPath = app.getPath("userData");
const repositoryRoot = process.env.SHULIU_REPOSITORY_ROOT ?? resolve(appPath, "../..");
const rendererEntry = resolve(appPath, "dist/renderer/index.html");
const preloadEntry = resolve(__dirname, "../preload/entry.cjs");
const rendererUrl = pathToFileURL(rendererEntry).href;
const healthProbeMode = process.argv.includes("--shuliu-health-check");
const canarySessionEnabled = consumeProviderCanarySession(process.env);
const sidecarEnvironment: NodeJS.ProcessEnv = { ...process.env };
if (canarySessionEnabled) sidecarEnvironment.SHULIU_MODEL_CONNECTION_TEST = "1";
const sidecarLaunchOptions = createSidecarLaunchOptions(
  repositoryRoot,
  sidecarEnvironment,
  process.platform === "win32" ? "win32" : process.platform === "darwin" ? "darwin" : "linux",
  {
    packaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    userDataPath,
  },
);
recordStartup({
  stage: "resource_resolution",
  status: "observed",
  appPackaged: app.isPackaged,
  appPathResolved: existsSync(appPath),
  rendererResourceAvailable: existsSync(rendererEntry),
  preloadResourceAvailable: existsSync(preloadEntry),
  sidecarResourceAvailable: existsSync(sidecarLaunchOptions.command),
});
const supervisor = new SidecarSupervisor({
  ...sidecarLaunchOptions,
  maxRestarts: 1,
  onDiagnostic: (diagnostics) => {
    void appendRuntimeDiagnostic(userDataPath, diagnostics).catch(() => undefined);
  },
});

const providers = new RuntimeProviderService(
  (method, params, options) => supervisor.request(method, params, options),
  (result) => {
    void appendProviderConnectionDiagnostic(userDataPath, result).catch(() => undefined);
  },
  { canaryEnabled: canarySessionEnabled },
);
const services = createDemoDesktopServices(providers);
let mainWindow: BrowserWindow | undefined;
let removeRuntimeHandlers: (() => void) | undefined;
let removeDesktopHandlers: (() => void) | undefined;
let quitting = false;
let requestedExitCode = 0;

function isTrustedWindow(event: unknown): boolean {
  return Boolean(mainWindow && isTrustedRendererRequest(event, mainWindow.webContents, rendererUrl));
}

function createMainWindow(showWindow = true): { window: BrowserWindow; loaded: Promise<void> } {
  recordStartup({ stage: "browser_window", status: "started" });
  let window: BrowserWindow;
  try {
    window = new BrowserWindow({
      width: 1440,
      height: 920,
      minWidth: 1100,
      minHeight: 720,
      show: false,
      backgroundColor: "#f5f7f8",
      title: "ShuLiu Agent · Foundation",
      webPreferences: createSecureWindowWebPreferences(preloadEntry),
    });
  } catch {
    recordStartup({ stage: "browser_window", status: "failed", code: "BROWSER_WINDOW_CREATE_FAILED" });
    throw new Error("BROWSER_WINDOW_CREATE_FAILED");
  }
  recordStartup({ stage: "browser_window", status: "succeeded" });
  mainWindow = window;

  if (!removeRuntimeHandlers) {
    removeRuntimeHandlers = registerRuntimeIpc(ipcMain, supervisor, isTrustedWindow);
    removeDesktopHandlers = registerDesktopIpc(ipcMain, services, isTrustedWindow);
  }

  window.webContents.on("preload-error", () => {
    recordStartup({ stage: "preload", status: "failed", code: "PRELOAD_LOAD_FAILED" });
  });
  window.webContents.on("render-process-gone", (_event, details) => {
    recordStartup({
      stage: "renderer_process",
      status: "failed",
      code: "RENDERER_PROCESS_GONE",
      processType: "Renderer",
      reason: details.reason,
      exitCode: details.exitCode,
    });
  });
  window.webContents.on("did-fail-load", (_event, errorCode, _description, _url, isMainFrame) => {
    if (isMainFrame) {
      recordStartup({ stage: "renderer_load", status: "failed", code: "RENDERER_LOAD_FAILED", exitCode: errorCode });
    }
  });
  if (showWindow) window.once("ready-to-show", () => {
    recordStartup({ stage: "browser_window", status: "observed" });
    window.show();
  });
  window.on("closed", () => {
    recordStartup({ stage: "browser_window", status: "observed" });
    if (mainWindow === window) mainWindow = undefined;
  });
  const loaded = window.loadFile(rendererEntry).then(
    async () => {
      recordStartup({ stage: "renderer_load", status: "succeeded" });
      try {
        const preloadAvailable = await window.webContents.executeJavaScript(
          "Boolean(window.shuliu && window.shuliu.runtime && typeof window.shuliu.runtime.health?.get === 'function')",
        ) as boolean;
        recordStartup({
          stage: "preload",
          status: preloadAvailable ? "succeeded" : "failed",
          code: preloadAvailable ? null : "PRELOAD_BRIDGE_UNAVAILABLE",
        });
      } catch {
        recordStartup({ stage: "preload", status: "failed", code: "PRELOAD_BRIDGE_UNAVAILABLE" });
      }
    },
    () => {
      recordStartup({ stage: "renderer_load", status: "failed", code: "RENDERER_LOAD_FAILED" });
      throw new Error("RENDERER_LOAD_FAILED");
    },
  );
  return { window, loaded };
}

async function runInstalledHealthCheck(): Promise<void> {
  const exitCode = await runHealthProbe({
    startRuntime: () => startSidecar(),
    runtimeDiagnostics: () => supervisor.diagnostics,
    inspectRenderer: async () => {
      const instance = createMainWindow(false);
      await instance.loaded;
      return await instance.window.webContents.executeJavaScript(`(async () => {
        const bridge = window.shuliu;
        if (!bridge || !bridge.runtime || !bridge.runtime.health || typeof bridge.runtime.health.get !== "function") {
          return { preloadAvailable: false, health: { status: "unhealthy" } };
        }
        return { preloadAvailable: true, health: await bridge.runtime.health.get() };
      })()`) as { preloadAvailable: boolean; health: RuntimeHealth };
    },
    appVersion: app.getVersion(),
    electronVersion: process.versions.electron ?? "unknown",
    nodeVersion: process.versions.node,
    write: (jsonLine) => process.stdout.write(`${jsonLine}\n`),
  });
  requestedExitCode = exitCode;
  process.exitCode = exitCode;
  app.quit();
}

async function startSidecar(): Promise<void> {
  recordStartup({ stage: "sidecar_create", status: "started" });
  try {
    await supervisor.start();
    recordStartup({ stage: "sidecar_create", status: "succeeded" });
    recordStartup({ stage: "ipc_handshake", status: "succeeded" });
  } catch {
    const diagnostics = supervisor.diagnostics;
    const spawnFailed = diagnostics.failureStage === "sidecar_spawn";
    recordStartup({
      stage: "sidecar_create",
      status: spawnFailed ? "failed" : "succeeded",
      code: spawnFailed ? "SIDECAR_SPAWN_FAILED" : null,
      exitCode: diagnostics.sidecarExitCode,
    });
    recordStartup({
      stage: "ipc_handshake",
      status: spawnFailed ? "observed" : "failed",
      code: spawnFailed ? null : "IPC_HANDSHAKE_FAILED",
      exitCode: diagnostics.sidecarExitCode,
    });
    throw new Error(spawnFailed ? "SIDECAR_SPAWN_FAILED" : "IPC_HANDSHAKE_FAILED");
  }
}

app.on("child-process-gone", (_event, details) => {
  recordStartup({
    stage: "child_process",
    status: details.reason === "clean-exit" ? "observed" : "failed",
    code: "CHILD_PROCESS_GONE",
    processType: details.type,
    reason: details.reason,
    exitCode: details.exitCode,
  });
});

app.whenReady().then(async () => {
  let safeStorageAvailable = false;
  try {
    safeStorageAvailable = safeStorage.isEncryptionAvailable();
    recordStartup({
      stage: "safe_storage",
      status: safeStorageAvailable ? "observed" : "failed",
      code: safeStorageAvailable ? null : "SAFE_STORAGE_UNAVAILABLE",
      safeStorageAvailable,
    });
  } catch {
    recordStartup({ stage: "safe_storage", status: "failed", code: "SAFE_STORAGE_UNAVAILABLE" });
  }
  if (healthProbeMode) {
    await runInstalledHealthCheck();
    return;
  }

  try {
    await startSidecar();
  } catch {
    // The workspace remains available in Safe Mode so users can inspect health and settings.
  }
  createMainWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
  });
});

app.on("before-quit", (event) => {
  if (quitting) return;
  event.preventDefault();
  quitting = true;
  removeRuntimeHandlers?.();
  removeDesktopHandlers?.();
  void supervisor.close().finally(() => app.exit(requestedExitCode));
});
