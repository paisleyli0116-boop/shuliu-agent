import { mkdir, writeFile } from "node:fs/promises";
import { generateKeyPairSync } from "node:crypto";
import { resolve } from "node:path";

const outputDirectory = process.argv[2] && resolve(process.argv[2]);
if (!outputDirectory) throw new Error("CI_SIGNING_DIRECTORY_REQUIRED");
await mkdir(outputDirectory, { recursive: false });
const { privateKey, publicKey } = generateKeyPairSync("ed25519", {
  privateKeyEncoding: { type: "pkcs8", format: "pem" },
  publicKeyEncoding: { type: "spki", format: "pem" },
});
await writeFile(resolve(outputDirectory, "private.pem"), privateKey, { flag: "wx" });
await writeFile(resolve(outputDirectory, "public.pem"), publicKey, { flag: "wx" });
console.log("CI-only ephemeral update-signing key pair created.");
