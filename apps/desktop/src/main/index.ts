import { app, BrowserWindow, ipcMain, safeStorage } from "electron";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
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
const supervisor = new SidecarSupervisor({
  ...createSidecarLaunchOptions(repositoryRoot, process.env, process.platform === "win32" ? "win32" : process.platform === "darwin" ? "darwin" : "linux"),
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

function isTrustedWindow(event: unknown): boolean {
  return Boolean(mainWindow && isTrustedRendererRequest(event, mainWindow.webContents, rendererUrl));
}

function createMainWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1100,
    minHeight: 720,
    show: false,
    backgroundColor: "#f5f7f8",
    title: "ShuLiu Agent · Foundation",
    webPreferences: createSecureWindowWebPreferences(resolve(__dirname, "../preload/entry.cjs")),
  });

  if (!removeRuntimeHandlers) {
    removeRuntimeHandlers = registerRuntimeIpc(ipcMain, supervisor, isTrustedWindow);
    removeDesktopHandlers = registerDesktopIpc(ipcMain, services, isTrustedWindow);
  }

  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    mainWindow = undefined;
  });
  void mainWindow.loadFile(rendererEntry);
}

app.whenReady().then(async () => {
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
  void supervisor.close().finally(() => app.quit());
});
