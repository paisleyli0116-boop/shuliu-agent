import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const desktopRoot = resolve(import.meta.dirname, "../..");

describe("Windows installer package contract", () => {
  it("ships the packaged Sidecar and retains per-user data on uninstall", () => {
    const config = JSON.parse(readFileSync(resolve(desktopRoot, "electron-builder.yml"), "utf8")) as {
      extraResources: Array<{ from: string; to: string; filter: string[] }>;
      nsis: { allowToChangeInstallationDirectory: boolean; deleteAppDataOnUninstall: boolean; include: string };
      win: { target: Array<{ target: string; arch: string[] }> };
    };

    expect(config.win.target).toContainEqual({ target: "nsis", arch: ["x64"] });
    expect(config.extraResources).toContainEqual(expect.objectContaining({
      from: "../../runtimes/python-worker/dist",
      to: "python",
    }));
    expect(config.nsis.allowToChangeInstallationDirectory).toBe(true);
    expect(config.nsis.deleteAppDataOnUninstall).toBe(false);
    expect(config.nsis.include).toBe("installer/installer.nsi");
  });

  it("freezes the IPC schemas into the standalone Windows Sidecar", () => {
    const spec = readFileSync(resolve(desktopRoot, "build/windows.spec"), "utf8");
    expect(spec).toContain("server.py");
    expect(spec).toContain("common-v1.schema.json");
    expect(spec).toContain("runtime-ipc-v1.schema.json");
    expect(spec).toContain("ShuLiuRuntime");
  });

  it("stages the executable without an .exe suffix and restores its runtime name during install", () => {
    const buildScript = readFileSync(resolve(desktopRoot, "scripts/package-windows.mjs"), "utf8");
    const installerScript = readFileSync(resolve(desktopRoot, "installer/installer.nsi"), "utf8");
    expect(buildScript).toContain("ShuLiuRuntime.bin");
    expect(installerScript).toContain('Rename "$INSTDIR\\resources\\python\\ShuLiuRuntime.bin" "$INSTDIR\\resources\\python\\ShuLiuRuntime.exe"');
    expect(installerScript).toContain('install.log');
  });
});
