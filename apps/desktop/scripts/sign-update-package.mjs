import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash, createPrivateKey, createPublicKey, sign } from "node:crypto";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const [installerPath, outputPath] = process.argv.slice(2).map((value) => value && resolve(value));
if (!installerPath || !outputPath) throw new Error("UPDATE_PACKAGE_ARGUMENTS_REQUIRED");
const privatePem = process.env.SHULIU_UPDATE_SIGNING_PRIVATE_KEY;
const keyId = process.env.SHULIU_UPDATE_SIGNING_KEY_ID;
const targetVersion = process.env.SHULIU_UPDATE_TARGET_VERSION;
if (!privatePem || !keyId || !targetVersion) throw new Error("UPDATE_SIGNING_CONFIGURATION_REQUIRED");
if (!/^[A-Za-z0-9_.-]{1,80}$/.test(keyId) || !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(targetVersion)) {
  throw new Error("UPDATE_SIGNING_CONFIGURATION_INVALID");
}

let privateKey;
try {
  privateKey = createPrivateKey(privatePem);
} catch {
  throw new Error("UPDATE_SIGNING_KEY_INVALID");
}
if (privateKey.asymmetricKeyType !== "ed25519") throw new Error("UPDATE_SIGNING_KEY_TYPE_INVALID");

const compatibilityTemplate = JSON.parse(
  await readFile(resolve(desktopRoot, "build/update-compatibility.json"), "utf8"),
);
const payload = await readFile(installerPath);
const manifest = {
  format_version: 1,
  target_version: targetVersion,
  versions: {
    foundation: targetVersion,
    runtime: "0.1.0",
    worker: "0.1.0",
    skill_protocol: "1.0.0",
    event_schema: "1.0.0",
    database_schema: "1.0.0",
    python_runtime: "3.11.0",
  },
  compatibility: compatibilityTemplate.compatibility,
  payload_sha256: createHash("sha256").update(payload).digest("hex"),
  signing_key_id: keyId,
};
const canonical = (value) => Array.isArray(value)
  ? value.map(canonical)
  : typeof value === "object" && value !== null
    ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]))
    : value;
const signature = sign(null, Buffer.from(JSON.stringify(canonical(manifest))), privateKey).toString("base64");
await mkdir(outputPath, { recursive: false });
await copyFile(installerPath, resolve(outputPath, "payload.exe"));
await writeFile(resolve(outputPath, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, { flag: "wx" });
await writeFile(resolve(outputPath, "manifest.sig"), `${signature}\n`, { flag: "wx" });
const publicOutput = process.env.SHULIU_UPDATE_PUBLIC_KEY_OUTPUT;
if (publicOutput) {
  const publicPem = createPublicKey(privateKey).export({ type: "spki", format: "pem" });
  await writeFile(resolve(publicOutput), publicPem, { flag: "wx" });
}
console.log(JSON.stringify({ target_version: targetVersion, payload_sha256: manifest.payload_sha256, signing_key_id: keyId }));
