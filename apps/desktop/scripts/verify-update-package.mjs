import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const [packageDirectory, publicKeyPath] = process.argv.slice(2).map((value) => value && resolve(value));
if (!packageDirectory || !publicKeyPath) throw new Error("UPDATE_PACKAGE_VERIFY_ARGUMENTS_REQUIRED");
const manifest = JSON.parse(await readFile(resolve(packageDirectory, "manifest.json"), "utf8"));
const signature = (await readFile(resolve(packageDirectory, "manifest.sig"), "utf8")).trim();
const payload = await readFile(resolve(packageDirectory, "payload.exe"));
const publicKey = await readFile(publicKeyPath, "utf8");
const require = createRequire(import.meta.url);
const { verifyUpdatePackage } = require(resolve(desktopRoot, "dist/main/update/verify.js"));
const verified = verifyUpdatePackage(
  { manifest, signature, payload },
  { [manifest.signing_key_id]: publicKey },
  {
    foundation: "0.1.0",
    runtime: "0.1.0",
    worker: "0.1.0",
    skill_protocol: "1.0.0",
    event_schema: "1.0.0",
    database_schema: "1.0.0",
    python_runtime: "3.11.0",
  },
);
console.log(JSON.stringify({ signature_verified: true, target_version: verified.manifest.target_version, payload_sha256: verified.payload_sha256 }));
