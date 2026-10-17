import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

type SafeStorageFake = {
  isAsyncEncryptionAvailable: () => Promise<boolean>;
  encryptStringAsync: (value: string) => Promise<Buffer>;
  decryptStringAsync: (value: Buffer) => Promise<{ result: string; shouldReEncrypt: boolean }>;
  getSelectedStorageBackend: () => string;
};

type StorageModule = {
  LocalSecureStorage: new (options: {
    safeStorage: SafeStorageFake;
    platform: "darwin" | "win32" | "linux";
    userDataPath: string;
  }) => {
    get(reference: string): Promise<string>;
    put(reference: string, value: string): Promise<void>;
    delete(reference: string): Promise<void>;
  };
  SecureStorageUnavailableError: new (...args: never[]) => Error;
  SecretNotFoundError: new (...args: never[]) => Error;
};

async function loadStorageModule(): Promise<StorageModule> {
  const loaded = await import("../../src/main/security/secure-storage");
  const module = loaded as unknown as Partial<StorageModule>;
  expect(typeof module.LocalSecureStorage).toBe("function");
  expect(typeof module.SecureStorageUnavailableError).toBe("function");
  expect(typeof module.SecretNotFoundError).toBe("function");
  return module as StorageModule;
}

function makeSafeStorage(backend = "keychain", available = true): SafeStorageFake {
  return {
    isAsyncEncryptionAvailable: async () => available,
    getSelectedStorageBackend: () => backend,
    encryptStringAsync: async (value) =>
      Buffer.from([...Buffer.from(value, "utf8")].map((byte) => byte ^ 0xa7)),
    decryptStringAsync: async (encrypted) => ({
      result: Buffer.from([...encrypted].map((byte) => byte ^ 0xa7)).toString("utf8"),
      shouldReEncrypt: false,
    }),
  };
}

async function withTemporaryStorage<T>(run: (root: string) => Promise<T>): Promise<T> {
  const root = await mkdtemp(join(tmpdir(), "shuliu-secure-storage-"));
  try {
    return await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

describe("Electron Main secure storage", () => {
  it("persists encrypted local secrets and reads them through the main-process adapter", async () => {
    const module = await loadStorageModule();

    await withTemporaryStorage(async (userDataPath) => {
      const storage = new module.LocalSecureStorage({
        safeStorage: makeSafeStorage(),
        platform: "darwin",
        userDataPath,
      });
      const secret = "local-plain-secret-value";
      const reference = "secret://os/worker/session-token";

      await storage.put(reference, secret);

      const onDisk = await readFile(join(userDataPath, "secrets.json"), "utf8");
      expect(onDisk).not.toContain(secret);
      expect(await storage.get(reference)).toBe(secret);
    });
  });

  it("rejects Linux basic_text fallback storage", async () => {
    const module = await loadStorageModule();

    await withTemporaryStorage(async (userDataPath) => {
      const storage = new module.LocalSecureStorage({
        safeStorage: makeSafeStorage("basic_text"),
        platform: "linux",
        userDataPath,
      });

      await expect(storage.put("secret://os/worker/token", "secret-value")).rejects.toBeInstanceOf(
        module.SecureStorageUnavailableError,
      );
    });
  });

  it("rejects secure storage when platform encryption is unavailable", async () => {
    const module = await loadStorageModule();

    await withTemporaryStorage(async (userDataPath) => {
      const storage = new module.LocalSecureStorage({
        safeStorage: makeSafeStorage("keychain", false),
        platform: "darwin",
        userDataPath,
      });

      await expect(storage.put("secret://os/worker/token", "secret-value")).rejects.toBeInstanceOf(
        module.SecureStorageUnavailableError,
      );
    });
  });

  it("returns a safe not-found error and deletes local secrets", async () => {
    const module = await loadStorageModule();

    await withTemporaryStorage(async (userDataPath) => {
      const storage = new module.LocalSecureStorage({
        safeStorage: makeSafeStorage(),
        platform: "darwin",
        userDataPath,
      });
      const reference = "secret://os/worker/token";

      await expect(storage.get(reference)).rejects.toBeInstanceOf(module.SecretNotFoundError);
      await storage.put(reference, "secret-value");
      await storage.delete(reference);
      await expect(storage.get(reference)).rejects.toBeInstanceOf(module.SecretNotFoundError);
    });
  });
});
