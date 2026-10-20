export type ProbeRuntimeHealth = {
  status: "healthy" | "degraded" | "unhealthy" | "safe_mode";
  components?: {
    database?: { schema_version?: unknown };
  };
};

export type HealthProbeDependencies = {
  startRuntime(): Promise<void>;
  inspectRenderer(): Promise<{ preloadAvailable: boolean; health: ProbeRuntimeHealth }>;
  appVersion: string;
  electronVersion: string;
  nodeVersion: string;
  write(jsonLine: string): void;
};

export async function runHealthProbe(dependencies: HealthProbeDependencies): Promise<number> {
  const versions = {
    app_version: dependencies.appVersion,
    electron_version: dependencies.electronVersion,
    node_version: dependencies.nodeVersion,
  };

  try {
    await dependencies.startRuntime();
  } catch {
    // The renderer reports the supervisor's Safe Mode state through the regular preload API.
  }

  try {
    const inspected = await dependencies.inspectRenderer();
    if (!inspected.preloadAvailable) {
      dependencies.write(JSON.stringify({
        status: "unhealthy",
        ...versions,
        preload_available: false,
        code: "PRELOAD_UNAVAILABLE",
      }));
      return 3;
    }

    if (inspected.health.status === "safe_mode") {
      dependencies.write(JSON.stringify({
        status: "safe_mode",
        ...versions,
        preload_available: true,
        code: "RUNTIME_SAFE_MODE",
      }));
      return 2;
    }

    const schemaVersion = inspected.health.components?.database?.schema_version;
    if (inspected.health.status !== "healthy") {
      dependencies.write(JSON.stringify({
        status: "unhealthy",
        ...versions,
        preload_available: true,
        code: "RUNTIME_UNHEALTHY",
      }));
      return 3;
    }
    if (typeof schemaVersion !== "string" || !/^\d+\.\d+\.\d+$/.test(schemaVersion)) {
      dependencies.write(JSON.stringify({
        status: "unhealthy",
        ...versions,
        preload_available: true,
        code: "DATABASE_SCHEMA_UNAVAILABLE",
      }));
      return 3;
    }

    dependencies.write(JSON.stringify({
      status: "healthy",
      ...versions,
      preload_available: true,
      database_schema: schemaVersion,
    }));
    return 0;
  } catch {
    dependencies.write(JSON.stringify({
      status: "unhealthy",
      ...versions,
      preload_available: false,
      code: "DESKTOP_HEALTH_PROBE_FAILED",
    }));
    return 3;
  }
}
