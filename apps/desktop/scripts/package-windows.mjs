import { access, copyFile, mkdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Arch, Platform, build } from "electron-builder";

const desktopRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const repositoryRoot = resolve(desktopRoot, "../..");
const packageJson = JSON.parse(await readFile(resolve(desktopRoot, "package.json"), "utf8"));
const version = process.env.SHULIU_PACKAGE_VERSION ?? packageJson.version;
if (!/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(version)) {
  throw new Error("PACKAGE_VERSION_INVALID");
}

const artifactDirectory = resolve(
  process.env.SHULIU_ARTIFACTS_DIR ?? resolve(desktopRoot, "release"),
);
const sidecarExecutable = resolve(repositoryRoot, "runtimes/python-worker/dist/ShuLiuRuntime.exe");
const requiredPaths = [
  sidecarExecutable,
  resolve(desktopRoot, "dist/main/index.js"),
  resolve(desktopRoot, "dist/renderer/index.html"),
];
for (const requiredPath of requiredPaths) await access(requiredPath);
await mkdir(artifactDirectory, { recursive: true });

// Stage the one-file runtime as data to avoid Windows executable scanning during resource copy.
const stagingRoot = resolve(artifactDirectory, "sidecar-staging");
await mkdir(stagingRoot, { recursive: false });
const stagedSidecar = resolve(stagingRoot, "python");
await mkdir(stagedSidecar, { recursive: false });
await copyFile(sidecarExecutable, resolve(stagedSidecar, "ShuLiuRuntime.bin"));

const config = JSON.parse(await readFile(resolve(desktopRoot, "electron-builder.yml"), "utf8"));
config.directories = { output: artifactDirectory };
config.extraMetadata = { version };
if (!Array.isArray(config.extraResources) || !config.extraResources[0]) {
  throw new Error("WINDOWS_SIDECAR_RESOURCE_CONFIG_MISSING");
}
config.extraResources[0].from = stagedSidecar;
config.extraResources[0].filter = ["**/*"];
const artifacts = await build({
  projectDir: desktopRoot,
  targets: Platform.WINDOWS.createTarget(["nsis"], Arch.x64),
  config,
});
const installer = artifacts?.find((artifact) => artifact.toLowerCase().endsWith(".exe"));
if (!installer) throw new Error("WINDOWS_INSTALLER_NOT_CREATED");
console.log(JSON.stringify({ installer: installer.split(/[\\/]/).pop(), version, arch: "x64" }));
