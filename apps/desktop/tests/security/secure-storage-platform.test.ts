import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const desktopRoot = resolve(here, "../..");
const require = createRequire(import.meta.url);

type PlatformReport = {
  platform: string;
  backend: string | null;
  available: boolean;
  encrypted_readback: boolean;
  no_plaintext_on_disk: boolean;
  deleted: boolean;
  ok: boolean;
  error: string | null;
};

// Task 27-C: the real-Electron safeStorage smoke is platform evidence for
// macOS Keychain and Windows DPAPI (ADR-027). Linux has no OS keyring in CI,
// so the required gates only run on darwin/win32 runners; skip there.
describe.skipIf(process.platform === "linux")(
  "Electron native safeStorage platform smoke",
  () => {
    it("encrypts, reads back, keeps plaintext off disk, and deletes via the real OS backend", { timeout: 180_000 }, () => {
      // The fixture requires the compiled real adapter; build it on demand so
      // both `pnpm test` and the dedicated gate script work standalone.
      const adapter = join(desktopRoot, ".ipc-test-build", "main", "security", "secure-storage.js");
      if (!existsSync(adapter)) {
        const build = spawnSync(process.execPath, [join(desktopRoot, "scripts", "build-ipc-test.mjs")], {
          stdio: "inherit",
        });
        expect(build.status).toBe(0);
      }

      const electronPath = require("electron") as string;
      const stateDir = mkdtempSync(join(tmpdir(), "shuliu-secure-storage-smoke-"));
      const reportPath = join(stateDir, "report.json");
      try {
        // ELECTRON_RUN_AS_NODE must never leak into a real Electron spawn
        // (it downgrades the binary to plain Node and voids the evidence).
        const { ELECTRON_RUN_AS_NODE: _ignored, ...environment } = process.env;
        const result = spawnSync(
          electronPath,
          [
            "--no-sandbox",
            "--headless",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            join(desktopRoot, "tests", "fixtures", "secure-storage-platform", "main.cjs"),
          ],
          {
            cwd: desktopRoot,
            env: {
              ...environment,
              SHULIU_SECURE_STORAGE_STATE_DIR: stateDir,
              SHULIU_SECURE_STORAGE_REPORT: reportPath,
            },
            timeout: 150_000,
            encoding: "utf8",
          },
        );

        const reportText = existsSync(reportPath) ? readFileSync(reportPath, "utf8") : null;
        if (reportText === null) {
          throw new Error(
            `fixture produced no report (status=${result.status}, signal=${result.signal}, stderr=${(result.stderr ?? "").slice(0, 2000)})`,
          );
        }
        const report = JSON.parse(reportText) as PlatformReport;
        expect(report.error).toBeNull();
        expect(result.status).toBe(0);
        expect(report.platform).toBe(process.platform);
        expect(report.available).toBe(true);
        expect(report.no_plaintext_on_disk).toBe(true);
        expect(report.encrypted_readback).toBe(true);
        expect(report.deleted).toBe(true);
        expect(report.ok).toBe(true);
        // The plaintext must not leak into the fixture's stdout either.
        expect(result.stdout ?? "").not.toContain("shuliu-platform-smoke-");
        expect(result.stderr ?? "").not.toContain("shuliu-platform-smoke-");
      } finally {
        rmSync(stateDir, { recursive: true, force: true });
      }
    });

    it("declares the gate on darwin and win32 and skips elsewhere", () => {
      expect(["darwin", "win32"]).toContain(process.platform);
    });
  },
);
