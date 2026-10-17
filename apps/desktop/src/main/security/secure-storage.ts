import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

export type SecureStoragePlatform = "darwin" | "win32" | "linux";

export interface ElectronSafeStorage {
  isAsyncEncryptionAvailable(): Promise<boolean>;
  encryptStringAsync(plainText: string): Promise<Buffer>;
  decryptStringAsync(encrypted: Buffer): Promise<{
    result: string;
    shouldReEncrypt: boolean;
  }>;
  getSelectedStorageBackend?(): string;
}

export class SecureStorageUnavailableError extends Error {
  constructor(message = "OS secure storage is unavailable") {
    super(message);
    this.name = "SecureStorageUnavailableError";
  }
}

export class SecretNotFoundError extends Error {
  constructor(reference: string) {
    super(`secret not found: ${reference}`);
    this.name = "SecretNotFoundError";
  }
}

export class InvalidSecretReferenceError extends Error {
  constructor() {
    super("secret reference is invalid");
    this.name = "InvalidSecretReferenceError";
  }
}

type PersistedVault = {
  version: 1;
  encryptedValues: Record<string, string>;
};

const LINUX_SECURE_BACKENDS = new Set([
  "gnome_libsecret",
  "kwallet",
  "kwallet5",
  "kwallet6",
  "org.freedesktop.portal.Secret",
]);
const LOCAL_REFERENCE = new RegExp(
  "^secret://os/[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$",
);

export class LocalSecureStorage {
  private readonly filePath: string;

  constructor(
    private readonly options: {
      safeStorage: ElectronSafeStorage;
      platform: SecureStoragePlatform;
      userDataPath: string;
    },
  ) {
    this.filePath = join(options.userDataPath, "secrets.json");
  }

  async get(reference: string): Promise<string> {
    const safeReference = this.validateReference(reference);
    await this.assertSecureStorageAvailable();
    const vault = await this.readVault();
    const encrypted = vault.encryptedValues[safeReference];
    if (encrypted === undefined) {
      throw new SecretNotFoundError(safeReference);
    }

    try {
      const decrypted = await this.options.safeStorage.decryptStringAsync(
        Buffer.from(encrypted, "base64"),
      );
      if (typeof decrypted.result !== "string") {
        throw new Error("decryption returned invalid data");
      }
      return decrypted.result;
    } catch {
      throw new SecureStorageUnavailableError("OS secure storage could not decrypt the value");
    }
  }

  async put(reference: string, value: string): Promise<void> {
    const safeReference = this.validateReference(reference);
    if (typeof value !== "string") {
      throw new TypeError("secret value must be text");
    }
    await this.assertSecureStorageAvailable();
    let encrypted: Buffer;
    try {
      encrypted = await this.options.safeStorage.encryptStringAsync(value);
    } catch {
      throw new SecureStorageUnavailableError("OS secure storage could not encrypt the value");
    }
    if (!Buffer.isBuffer(encrypted)) {
      throw new SecureStorageUnavailableError("OS secure storage returned invalid encrypted data");
    }

    const vault = await this.readVault();
    vault.encryptedValues[safeReference] = encrypted.toString("base64");
    await this.writeVault(vault);
  }

  async delete(reference: string): Promise<void> {
    const safeReference = this.validateReference(reference);
    await this.assertSecureStorageAvailable();
    const vault = await this.readVault();
    if (vault.encryptedValues[safeReference] === undefined) {
      return;
    }
    delete vault.encryptedValues[safeReference];
    await this.writeVault(vault);
  }

  private validateReference(reference: string): string {
    if (typeof reference !== "string" || !LOCAL_REFERENCE.test(reference)) {
      throw new InvalidSecretReferenceError();
    }
    return reference;
  }

  private async assertSecureStorageAvailable(): Promise<void> {
    let available = false;
    try {
      available = await this.options.safeStorage.isAsyncEncryptionAvailable();
    } catch {
      throw new SecureStorageUnavailableError();
    }
    if (!available) {
      throw new SecureStorageUnavailableError();
    }

    if (this.options.platform === "linux") {
      let backend: string | undefined;
      try {
        backend = this.options.safeStorage.getSelectedStorageBackend?.();
      } catch {
        throw new SecureStorageUnavailableError();
      }
      if (!backend || !LINUX_SECURE_BACKENDS.has(backend)) {
        throw new SecureStorageUnavailableError();
      }
    }
  }

  private async readVault(): Promise<PersistedVault> {
    let serialized: string;
    try {
      serialized = await readFile(this.filePath, "utf8");
    } catch (error) {
      if (typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT") {
        return { version: 1, encryptedValues: {} };
      }
      throw new SecureStorageUnavailableError("local secure storage could not be read");
    }

    try {
      const parsed: unknown = JSON.parse(serialized);
      if (
        typeof parsed !== "object" ||
        parsed === null ||
        !("version" in parsed) ||
        parsed.version !== 1 ||
        !("encryptedValues" in parsed) ||
        typeof parsed.encryptedValues !== "object" ||
        parsed.encryptedValues === null ||
        Array.isArray(parsed.encryptedValues)
      ) {
        throw new Error("invalid vault data");
      }
      const encryptedValues: Record<string, string> = {};
      for (const [reference, value] of Object.entries(parsed.encryptedValues)) {
        if (typeof value !== "string") {
          throw new Error("invalid encrypted value");
        }
        encryptedValues[reference] = value;
      }
      return { version: 1, encryptedValues };
    } catch {
      throw new SecureStorageUnavailableError("local secure storage data is invalid");
    }
  }

  private async writeVault(vault: PersistedVault): Promise<void> {
    const temporaryPath = join(
      this.options.userDataPath,
      `.secrets-${randomUUID()}.tmp`,
    );
    try {
      await mkdir(this.options.userDataPath, { recursive: true, mode: 0o700 });
      await writeFile(temporaryPath, JSON.stringify(vault), {
        encoding: "utf8",
        mode: 0o600,
      });
      await rename(temporaryPath, this.filePath);
    } catch {
      await rm(temporaryPath, { force: true }).catch(() => undefined);
      throw new SecureStorageUnavailableError("local secure storage could not be written");
    }
  }
}
