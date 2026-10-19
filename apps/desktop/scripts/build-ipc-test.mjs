import { spawnSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(desktopRoot, "../..");
const compiler = resolve(repositoryRoot, "node_modules/typescript/bin/tsc");
const result = spawnSync(process.execPath, [compiler, "--project", resolve(desktopRoot, "tsconfig.ipc-test.json")], {
  cwd: repositoryRoot,
  stdio: "inherit",
});
if (result.status !== 0) process.exit(result.status ?? 1);

const buildDirectory = resolve(desktopRoot, ".ipc-test-build");
await mkdir(buildDirectory, { recursive: true });
await writeFile(resolve(buildDirectory, "package.json"), JSON.stringify({ type: "commonjs" }) + "\n");

const require = createRequire(import.meta.url);
const preloadApi = require(resolve(buildDirectory, "preload/api.js"));
const rendererApiFactory = preloadApi.createRendererApi.toString().replaceAll(
  "exports.DESKTOP_IPC_CHANNELS",
  "DESKTOP_IPC_CHANNELS",
);
const preloadBundle = [
  'const { contextBridge, ipcRenderer } = require("electron");',
  `const DESKTOP_IPC_CHANNELS = ${JSON.stringify(preloadApi.DESKTOP_IPC_CHANNELS)};`,
  `const createRendererApi = ${rendererApiFactory};`,
  'contextBridge.exposeInMainWorld("shuliu", createRendererApi((channel) => ipcRenderer.invoke(channel)));',
  "",
].join("\n");
await writeFile(resolve(buildDirectory, "preload.cjs"), preloadBundle);
