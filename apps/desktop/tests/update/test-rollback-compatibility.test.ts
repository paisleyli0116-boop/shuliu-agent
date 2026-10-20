import { createHash, generateKeyPairSync, sign } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import {
  applyUpdate,
  type UpdateDependencies,
} from "../../src/main/update/apply";
import {
  canonicalizeUpdateManifest,
  type CompatibilityManifest,
  type RuntimeVersionManifest,
  type SignedUpdatePackage,
  type UpdateManifest,
} from "../../src/main/update/verify";
import { evaluateHealthGate } from "../../src/main/update/health-gate";

const installed: RuntimeVersionManifest = {
  foundation: "0.1.0",
  runtime: "44.9.0",
  worker: "0.1.0",
  skill_protocol: "1.0.0",
  event_schema: "1.0.0",
  database_schema: "1.0.0",
  python_runtime: "3.11.9",
};
const compatibility: CompatibilityManifest = {
  foundation: ">=0.1.0 <0.2.0",
  runtime: ">=44.0.0 <45.0.0",
  worker: ">=0.1.0 <0.2.0",
  skill_protocol: "^1.0.0",
  event_schema: "^1.0.0",
  database_schema: "^1.0.0",
  python_runtime: ">=3.11.0 <3.12.0",
};
const keyPair = generateKeyPairSync("ed25519");
const trustedKeys = {
  test: keyPair.publicKey.export({ type: "spki", format: "pem" }).toString(),
};

function signedPackage(): SignedUpdatePackage {
  const payload = Buffer.from("signed staged release");
  const manifest: UpdateManifest = {
    format_version: 1,
    target_version: "0.1.1",
    versions: { ...installed, foundation: "0.1.1" },
    compatibility,
    payload_sha256: createHash("sha256").update(payload).digest("hex"),
    signing_key_id: "test",
  };
  return {
    manifest,
    payload,
    signature: sign(null, Buffer.from(canonicalizeUpdateManifest(manifest)), keyPair.privateKey).toString("base64"),
  };
}

function createDependencies(
  overrides: Partial<UpdateDependencies<string>> = {},
): UpdateDependencies<string> {
  return {
    download: vi.fn(async () => signedPackage()),
    installedVersions: installed,
    trustedKeys,
    stage: vi.fn(async () => "staged-release"),
    preflightMigration: vi.fn(async () => ({ compatible: true })),
    migrate: vi.fn(async () => undefined),
    healthGate: vi.fn(async () => ({ passed: true })),
    activate: vi.fn(async () => undefined),
    rollbackCompatibility: vi.fn(async () => ({ protocol: true, database: true })),
    rollback: vi.fn(async () => undefined),
    enterSafeMode: vi.fn(async () => undefined),
    ...overrides,
  };
}

describe("guarded update activation and rollback", () => {
  it("fails closed when downloading is interrupted before staging", async () => {
    const download = vi.fn(async () => { throw new Error("interrupted download secret path"); });
    const dependencies = { ...createDependencies(), download } as UpdateDependencies<string>;

    const result = await applyUpdate(dependencies);

    expect(result).toEqual({ state: "FAILED", history: ["FAILED"], error_code: "DOWNLOAD_FAILED" });
    expect(download).toHaveBeenCalledOnce();
    expect(dependencies.stage).not.toHaveBeenCalled();
    expect(dependencies.rollback).not.toHaveBeenCalled();
    expect(dependencies.enterSafeMode).not.toHaveBeenCalled();
    expect(JSON.stringify(result)).not.toContain("interrupted download secret path");
  });

  it("activates only after verification, staging, migration, and health checks", async () => {
    const dependencies = createDependencies();

    const result = await applyUpdate(dependencies);

    expect(result.state).toBe("ACTIVE");
    expect(result.history).toEqual([
      "DOWNLOADED",
      "VERIFIED",
      "STAGED",
      "MIGRATED",
      "HEALTH_CHECKED",
      "ACTIVE",
    ]);
    expect(dependencies.activate).toHaveBeenCalledOnce();
    expect(dependencies.rollback).not.toHaveBeenCalled();
  });

  it("rolls back a migration failure only when protocol and database compatibility allow it", async () => {
    const dependencies = createDependencies({
      migrate: vi.fn(async () => { throw new Error("private migration detail"); }),
    });

    const result = await applyUpdate(dependencies);

    expect(result.state).toBe("ROLLED_BACK");
    expect(result.error_code).toBe("MIGRATION_FAILED");
    expect(result.history.at(-1)).toBe("ROLLED_BACK");
    expect(dependencies.rollback).toHaveBeenCalledOnce();
    expect(dependencies.activate).not.toHaveBeenCalled();
    expect(JSON.stringify(result)).not.toContain("private migration detail");
  });

  it("enters Safe Mode instead of rolling back across an incompatible database", async () => {
    const dependencies = createDependencies({
      healthGate: vi.fn(async () => ({ passed: false, code: "RUNTIME_UNHEALTHY" })),
      rollbackCompatibility: vi.fn(async () => ({ protocol: true, database: false })),
    });

    const result = await applyUpdate(dependencies);

    expect(result.state).toBe("SAFE_MODE");
    expect(result.error_code).toBe("ROLLBACK_INCOMPATIBLE");
    expect(dependencies.enterSafeMode).toHaveBeenCalledOnce();
    expect(dependencies.rollback).not.toHaveBeenCalled();
    expect(dependencies.activate).not.toHaveBeenCalled();
  });

  it("enters Safe Mode when restoring the previous release fails", async () => {
    const dependencies = createDependencies({
      migrate: vi.fn(async () => { throw new Error("migration failed"); }),
      rollback: vi.fn(async () => { throw new Error("restore details"); }),
    });

    const result = await applyUpdate(dependencies);

    expect(result.state).toBe("SAFE_MODE");
    expect(dependencies.enterSafeMode).toHaveBeenCalledOnce();
  });

  it("does not stage a package whose compatibility check fails", async () => {
    const update = signedPackage();
    const incompatibleManifest = {
      ...update.manifest,
      compatibility: { ...compatibility, database_schema: "^2.0.0" },
    };
    update.manifest = incompatibleManifest;
    update.signature = sign(
      null,
      Buffer.from(canonicalizeUpdateManifest(incompatibleManifest)),
      keyPair.privateKey,
    ).toString("base64");
    const dependencies = createDependencies({ download: vi.fn(async () => update) });

    await expect(applyUpdate(dependencies)).rejects.toThrow(/UPDATE_INCOMPATIBLE/);
    expect(dependencies.stage).not.toHaveBeenCalled();
  });

  it("requires every configured release health probe to be healthy", () => {
    expect(evaluateHealthGate({
      desktop: "healthy",
      python_runtime: "healthy",
      ipc: "healthy",
      database: "healthy",
    })).toMatchObject({ passed: true });

    expect(evaluateHealthGate({
      desktop: "healthy",
      python_runtime: "healthy",
      ipc: "healthy",
      database: "unknown",
    })).toMatchObject({ passed: false, safe_mode_required: true });
  });
});
