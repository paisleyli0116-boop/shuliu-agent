import { describe, expect, it, vi } from "vitest";
import { runHealthProbe } from "../../src/main/health-probe";

describe("installed desktop health probe", () => {
  const versions = { appVersion: "0.1.1", electronVersion: "44.9.0", nodeVersion: "24.22.0" };

  it("reports packed Electron, Node, app version, preload, and database health", async () => {
    const write = vi.fn();
    const exitCode = await runHealthProbe({
      ...versions,
      startRuntime: vi.fn(async () => undefined),
      inspectRenderer: vi.fn(async () => ({
        preloadAvailable: true,
        health: { status: "healthy" as const, components: { database: { schema_version: "1.0.0" } } },
      })),
      write,
    });

    expect(exitCode).toBe(0);
    expect(JSON.parse(write.mock.calls[0]?.[0] ?? "{}")).toEqual({
      status: "healthy" as const,
      app_version: "0.1.1",
      electron_version: "44.9.0",
      node_version: "24.22.0",
      preload_available: true,
      database_schema: "1.0.0",
    });
  });

  it("reports Safe Mode after Sidecar startup failure without exposing exception text", async () => {
    const write = vi.fn();
    const exitCode = await runHealthProbe({
      ...versions,
      startRuntime: async () => { throw new Error("secret path or credential"); },
      inspectRenderer: async () => ({ preloadAvailable: true, health: { status: "safe_mode" as const } }),
      write,
    });

    expect(exitCode).toBe(2);
    expect(JSON.parse(write.mock.calls[0]?.[0] ?? "{}")).toMatchObject({
      status: "safe_mode",
      app_version: "0.1.1",
      code: "RUNTIME_SAFE_MODE",
      preload_available: true,
    });
    expect(write.mock.calls[0]?.[0]).not.toContain("secret path or credential");
  });

  it("fails if preload is unavailable and emits only a stable code", async () => {
    const write = vi.fn();
    const exitCode = await runHealthProbe({
      ...versions,
      startRuntime: vi.fn(async () => undefined),
      inspectRenderer: vi.fn(async () => ({ preloadAvailable: false, health: { status: "healthy" as const } })),
      write,
    });

    expect(exitCode).toBe(3);
    expect(JSON.parse(write.mock.calls[0]?.[0] ?? "{}")).toMatchObject({
      status: "unhealthy",
      app_version: "0.1.1",
      code: "PRELOAD_UNAVAILABLE",
    });
  });
});
