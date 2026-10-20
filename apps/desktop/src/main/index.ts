import { app, BrowserWindow, ipcMain, safeStorage } from "electron";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { runHealthProbe } from "./health-probe.js";
import type { RuntimeHealth } from "../preload/api.js";
import { registerRuntimeIpc } from "./runtime/ipc.js";
import { SidecarSupervisor } from "./runtime/supervisor.js";
import { createSidecarLaunchOptions } from "./runtime/launch-options.js";
import { LocalSecureStorage } from "./security/secure-storage.js";
import { createDemoDesktopServices } from "./services/demo-desktop-services.js";
import { registerDesktopIpc } from "./ipc/desktop-ipc.js";
import { createSecureWindowWebPreferences, isTrustedRendererRequest } from "./window-security.js";

const repositoryRoot = process.env.SHULIU_REPOSITORY_ROOT ?? resolve(app.getAppPath(), "../..");
const rendererEntry = resolve(app.getAppPath(), "dist/renderer/index.html");
const rendererUrl = pathToFileURL(rendererEntry).href;
const healthProbeMode = process.argv.includes("--shuliu-health-check");
const supervisor = new SidecarSupervisor({
  ...createSidecarLaunchOptions(
    repositoryRoot,
    process.env,
    process.platform === "win32" ? "win32" : process.platform === "darwin" ? "darwin" : "linux",
    {
      packaged: app.isPackaged,
      resourcesPath: process.resourcesPath,
      userDataPath: app.getPath("userData"),
    },
  ),
  maxRestarts: 1,
});

const secureStorage = new LocalSecureStorage({
  safeStorage,
  platform: process.platform === "darwin" ? "darwin" : process.platform === "win32" ? "win32" : "linux",
  userDataPath: app.getPath("userData"),
});
const services = createDemoDesktopServices(secureStorage);
let mainWindow: BrowserWindow | undefined;
let removeRuntimeHandlers: (() => void) | undefined;
let removeDesktopHandlers: (() => void) | undefined;
let quitting = false;
let requestedExitCode = 0;

function isTrustedWindow(event: unknown): boolean {
  return Boolean(mainWindow && isTrustedRendererRequest(event, mainWindow.webContents, rendererUrl));
}

function createMainWindow(showWindow = true): { window: BrowserWindow; loaded: Promise<void> } {
  const window = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1100,
    minHeight: 720,
    show: false,
    backgroundColor: "#f5f7f8",
    title: "ShuLiu Agent · Foundation",
    webPreferences: createSecureWindowWebPreferences(resolve(__dirname, "../preload/entry.cjs")),
  });
  mainWindow = window;

  if (!removeRuntimeHandlers) {
    removeRuntimeHandlers = registerRuntimeIpc(ipcMain, supervisor, isTrustedWindow);
    removeDesktopHandlers = registerDesktopIpc(ipcMain, services, isTrustedWindow);
  }

  if (showWindow) window.once("ready-to-show", () => window.show());
  window.on("closed", () => {
    if (mainWindow === window) mainWindow = undefined;
  });
  const loaded = window.loadFile(rendererEntry).then(
    () => undefined,
    () => { throw new Error("RENDERER_LOAD_FAILED"); },
  );
  return { window, loaded };
}

async function runInstalledHealthCheck(): Promise<void> {
  const exitCode = await runHealthProbe({
    startRuntime: () => supervisor.start(),
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

app.whenReady().then(async () => {
  if (healthProbeMode) {
    await runInstalledHealthCheck();
    return;
  }

  try {
    await supervisor.start();
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
