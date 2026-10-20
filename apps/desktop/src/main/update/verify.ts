import { createHash, createPublicKey, timingSafeEqual, verify as verifySignature } from "node:crypto";
import { satisfies, valid, validRange } from "semver";

export type RuntimeVersionManifest = {
  foundation: string;
  runtime: string;
  worker: string;
  skill_protocol: string;
  event_schema: string;
  database_schema: string;
  python_runtime: string;
};

export type CompatibilityManifest = RuntimeVersionManifest;

export type UpdateManifest = {
  format_version: number;
  target_version: string;
  versions: RuntimeVersionManifest;
  compatibility: CompatibilityManifest;
  payload_sha256: string;
  signing_key_id: string;
};

export type SignedUpdatePackage = {
  manifest: UpdateManifest;
  payload: Uint8Array;
  signature: string;
};

export type VerifiedUpdatePackage = {
  manifest: UpdateManifest;
  payload: Buffer;
  payload_sha256: string;
};

export type UpdateVerificationErrorCode =
  | "UPDATE_MANIFEST_INVALID"
  | "UPDATE_CHECKSUM_MISMATCH"
  | "UPDATE_SIGNER_UNTRUSTED"
  | "UPDATE_SIGNATURE_INVALID"
  | "UPDATE_INCOMPATIBLE";

export class UpdateVerificationError extends Error {
  constructor(readonly code: UpdateVerificationErrorCode) {
    super(code);
    this.name = "UpdateVerificationError";
  }
}

const VERSION_FIELDS: readonly (keyof RuntimeVersionManifest)[] = [
  "foundation",
  "runtime",
  "worker",
  "skill_protocol",
  "event_schema",
  "database_schema",
  "python_runtime",
];

const MANIFEST_FIELDS = [
  "format_version",
  "target_version",
  "versions",
  "compatibility",
  "payload_sha256",
  "signing_key_id",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const required = [...expected].sort();
  return actual.length === required.length && actual.every((key, index) => key === required[index]);
}

function isVersionManifest(value: unknown): value is RuntimeVersionManifest {
  if (!isRecord(value) || !hasExactKeys(value, VERSION_FIELDS)) return false;
  return VERSION_FIELDS.every((field) => typeof value[field] === "string" && valid(value[field]) !== null);
}

function isCompatibilityManifest(value: unknown): value is CompatibilityManifest {
  if (!isRecord(value) || !hasExactKeys(value, VERSION_FIELDS)) return false;
  return VERSION_FIELDS.every((field) =>
    typeof value[field] === "string" && validRange(value[field]) !== null
  );
}

function isUpdateManifest(value: unknown): value is UpdateManifest {
  return isRecord(value)
    && hasExactKeys(value, MANIFEST_FIELDS)
    && value.format_version === 1
    && typeof value.target_version === "string"
    && valid(value.target_version) !== null
    && isVersionManifest(value.versions)
    && value.target_version === value.versions.foundation
    && isCompatibilityManifest(value.compatibility)
    && typeof value.payload_sha256 === "string"
    && /^[a-f0-9]{64}$/i.test(value.payload_sha256)
    && typeof value.signing_key_id === "string"
    && /^[A-Za-z0-9_.-]{1,80}$/.test(value.signing_key_id);
}

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, canonicalValue(value[key])]),
  );
}

export function canonicalizeUpdateManifest(manifest: UpdateManifest): string {
  return JSON.stringify(canonicalValue(manifest));
}

export function verifyUpdatePackage(
  updatePackage: SignedUpdatePackage,
  trustedPublicKeys: Readonly<Record<string, string>>,
  installedVersions: RuntimeVersionManifest,
): VerifiedUpdatePackage {
  if (
    !isRecord(updatePackage)
    || !isUpdateManifest(updatePackage.manifest)
    || !(updatePackage.payload instanceof Uint8Array)
    || typeof updatePackage.signature !== "string"
    || !/^[A-Za-z0-9+/]+={0,2}$/.test(updatePackage.signature)
  ) {
    throw new UpdateVerificationError("UPDATE_MANIFEST_INVALID");
  }

  const { manifest, payload, signature } = updatePackage;
  if (!isVersionManifest(installedVersions)) {
    throw new UpdateVerificationError("UPDATE_MANIFEST_INVALID");
  }

  const actualDigest = createHash("sha256").update(payload).digest();
  const declaredDigest = Buffer.from(manifest.payload_sha256, "hex");
  if (
    declaredDigest.length !== actualDigest.length
    || !timingSafeEqual(actualDigest, declaredDigest)
  ) {
    throw new UpdateVerificationError("UPDATE_CHECKSUM_MISMATCH");
  }

  const publicKeyPem = trustedPublicKeys[manifest.signing_key_id];
  if (typeof publicKeyPem !== "string" || publicKeyPem.length === 0) {
    throw new UpdateVerificationError("UPDATE_SIGNER_UNTRUSTED");
  }

  let validSignature = false;
  try {
    const publicKey = createPublicKey(publicKeyPem);
    const signatureBytes = Buffer.from(signature, "base64");
    validSignature = signatureBytes.length === 64 && verifySignature(
      null,
      Buffer.from(canonicalizeUpdateManifest(manifest), "utf8"),
      publicKey,
      signatureBytes,
    );
  } catch {
    validSignature = false;
  }
  if (!validSignature) throw new UpdateVerificationError("UPDATE_SIGNATURE_INVALID");

  const compatible = VERSION_FIELDS.every((field) =>
    satisfies(installedVersions[field], manifest.compatibility[field])
  );
  if (!compatible) throw new UpdateVerificationError("UPDATE_INCOMPATIBLE");

  return {
    manifest,
    payload: Buffer.from(payload),
    payload_sha256: actualDigest.toString("hex"),
  };
}
