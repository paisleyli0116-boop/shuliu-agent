// Task 27-C platform smoke fixture: exercises the REAL Electron safeStorage
// (macOS Keychain on darwin, Windows DPAPI on win32) through the real
// compiled LocalSecureStorage adapter. No fake safeStorage may be used as
// platform evidence (ADR-027 / Task 27 spec).
//
// Protocol: the vitest spec spawns Electron with
//   SHULIU_SECURE_STORAGE_STATE_DIR  scratch dir used as userData + vault root
//   SHULIU_SECURE_STORAGE_REPORT     path where the JSON report is written
// The process exits 0 on success, 1 on any failure; the report file always
// records per-step results for runner evidence.
const fs = require("node:fs");
const path = require("node:path");
const { app, safeStorage } = require("electron");

app.disableHardwareAcceleration();

const stateDir = process.env.SHULIU_SECURE_STORAGE_STATE_DIR;
const reportPath = process.env.SHULIU_SECURE_STORAGE_REPORT;

if (stateDir) {
  // Keep Keychain/DPAPI smoke artifacts out of the developer's real profile.
  app.setPath("userData", stateDir);
}

app.whenReady().then(async () => {
  const report = {
    platform: process.platform,
    backend: null,
    available: false,
    encrypted_readback: false,
    no_plaintext_on_disk: false,
    deleted: false,
    ok: false,
    error: null,
  };
  try {
    if (!stateDir || !reportPath) {
      throw new Error(
        "fixture requires SHULIU_SECURE_STORAGE_STATE_DIR and SHULIU_SECURE_STORAGE_REPORT",
      );
    }

    report.available = await safeStorage.isAsyncEncryptionAvailable();
    if (!report.available) {
      throw new Error("safeStorage async encryption is unavailable on this platform");
    }
    report.backend = safeStorage.getSelectedStorageBackend?.() ?? null;

    // Real adapter compiled from src/main/security/secure-storage.ts by
    // build:ipc-test (tsconfig.ipc-test.json). Deliberately NOT a fake.
    const { LocalSecureStorage } = require("../../../.ipc-test-build/main/security/secure-storage.js");
    const vaultDir = path.join(stateDir, "vault");
    const storage = new LocalSecureStorage({
      safeStorage,
      platform: process.platform,
      userDataPath: vaultDir,
    });

    const reference = "secret://os/platform-smoke/api-key";
    const plain = "shuliu-platform-smoke-" + "9f3ac2";

    await storage.put(reference, plain);

    const vaultFile = fs.readFileSync(path.join(vaultDir, "secrets.json"), "utf8");
    report.no_plaintext_on_disk =
      !vaultFile.includes(plain) && !vaultFile.includes("shuliu-platform-smoke-");

    const roundtrip = await storage.get(reference);
    report.encrypted_readback = roundtrip === plain;

    await storage.delete(reference);
    let deleted = false;
    try {
      await storage.get(reference);
    } catch (error) {
      deleted = error instanceof Error && error.name === "SecretNotFoundError";
    }
    report.deleted = deleted;

    report.ok =
      report.no_plaintext_on_disk && report.encrypted_readback && report.deleted;
  } catch (error) {
    report.error = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  }

  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  app.exit(report.ok ? 0 : 1);
});
