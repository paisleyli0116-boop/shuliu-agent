import { createHash, generateKeyPairSync, sign } from "node:crypto";
import { describe, expect, it } from "vitest";
import {
  canonicalizeUpdateManifest,
  verifyUpdatePackage,
  type CompatibilityManifest,
  type RuntimeVersionManifest,
  type UpdateManifest,
} from "../../src/main/update/verify";

const current: RuntimeVersionManifest = {
  foundation: "0.1.0",
  runtime: "44.9.0",
  worker: "0.1.0",
  skill_protocol: "1.0.0",
  event_schema: "1.0.0",
  database_schema: "1.0.0",
  python_runtime: "3.11.9",
};

const compatible: CompatibilityManifest = {
  foundation: ">=0.1.0 <0.2.0",
  runtime: ">=44.0.0 <45.0.0",
  worker: ">=0.1.0 <0.2.0",
  skill_protocol: "^1.0.0",
  event_schema: "^1.0.0",
  database_schema: "^1.0.0",
  python_runtime: ">=3.11.0 <3.12.0",
};

const keys = generateKeyPairSync("ed25519");
const trustedKeys = {
  release_test: keys.publicKey.export({ type: "spki", format: "pem" }).toString(),
};

function makePackage(
  overrides: Partial<UpdateManifest> = {},
  payload = Buffer.from("fixture signed update payload"),
) {
  const manifest: UpdateManifest = {
    format_version: 1,
    target_version: "0.1.1",
    versions: { ...current, foundation: "0.1.1" },
    compatibility: compatible,
    payload_sha256: createHash("sha256").update(payload).digest("hex"),
    signing_key_id: "release_test",
    ...overrides,
  };
  const signature = sign(
    null,
    Buffer.from(canonicalizeUpdateManifest(manifest), "utf8"),
    keys.privateKey,
  ).toString("base64");
  return { manifest, payload, signature };
}

describe("signed Windows update packages", () => {
  it("accepts a trusted signature, matching checksum, and compatible manifest", () => {
    const update = makePackage();

    const verified = verifyUpdatePackage(update, trustedKeys, current);

    expect(verified.payload).toEqual(update.payload);
    expect(verified.manifest.target_version).toBe("0.1.1");
    expect(verified.payload_sha256).toBe(update.manifest.payload_sha256);
  });

  it("rejects a payload changed after its checksum and signature were created", () => {
    const update = makePackage();
    update.payload[0] = update.payload[0]! ^ 0xff;

    expect(() => verifyUpdatePackage(update, trustedKeys, current))
      .toThrowError(/UPDATE_CHECKSUM_MISMATCH/);
  });

  it("rejects an invalid detached signature", () => {
    const update = makePackage({ signing_key_id: "release_test" });
    update.signature = Buffer.alloc(64, 0).toString("base64");

    expect(() => verifyUpdatePackage(update, trustedKeys, current))
      .toThrowError(/UPDATE_SIGNATURE_INVALID/);
  });

  it("rejects a signing key that is not in the trusted key set", () => {
    const update = makePackage({ signing_key_id: "unknown" });

    expect(() => verifyUpdatePackage(update, trustedKeys, current))
      .toThrowError(/UPDATE_SIGNER_UNTRUSTED/);
  });

  it("rejects an update whose compatibility range excludes the installed versions", () => {
    const update = makePackage({
      compatibility: { ...compatible, runtime: ">=42.0.0 <43.0.0" },
    });

    expect(() => verifyUpdatePackage(update, trustedKeys, current))
      .toThrowError(/UPDATE_INCOMPATIBLE/);
  });

  it("rejects an unknown manifest format before returning a verified package", () => {
    const update = makePackage({
      format_version: 7,
    });

    expect(() => verifyUpdatePackage(update, trustedKeys, current))
      .toThrowError(/UPDATE_MANIFEST_INVALID/);
  });
});
