export type CoreHealthStatus = "healthy" | "degraded" | "unhealthy" | "unknown";
export type CoreHealthSnapshot = Partial<Record<
  "desktop" | "python_runtime" | "ipc" | "database",
  CoreHealthStatus
>>;

export type HealthGateResult = {
  passed: boolean;
  safe_mode_required: boolean;
  failed_components: string[];
};

const REQUIRED_CORE_COMPONENTS = ["desktop", "python_runtime", "ipc", "database"] as const;

export function evaluateHealthGate(
  snapshot: CoreHealthSnapshot,
  requiredComponents: readonly (keyof CoreHealthSnapshot)[] = REQUIRED_CORE_COMPONENTS,
): HealthGateResult {
  const failedComponents = requiredComponents.filter((component) => snapshot[component] !== "healthy");
  return {
    passed: failedComponents.length === 0,
    safe_mode_required: failedComponents.length > 0,
    failed_components: [...failedComponents],
  };
}
