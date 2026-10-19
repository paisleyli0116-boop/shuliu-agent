import type { RuntimeHealth as RuntimeHealthModel } from "../../preload/api";

const statusLabel: Record<RuntimeHealthModel["status"], string> = {
  healthy: "运行正常",
  degraded: "部分降级",
  unhealthy: "运行异常",
  safe_mode: "Safe Mode",
};

export function RuntimeHealth({ health, compact = false }: { health: RuntimeHealthModel | null; compact?: boolean }) {
  if (!health) {
    return <div className={`health-card ${compact ? "health-card--compact" : ""}`}><span className="status-dot status-dot--pending" />正在检查运行状态</div>;
  }
  const healthy = health.status === "healthy";
  return (
    <div className={`health-card ${compact ? "health-card--compact" : ""}`}>
      <span className={`status-dot ${healthy ? "status-dot--healthy" : "status-dot--warning"}`} aria-hidden="true" />
      <span className="health-card__label">Python Runtime</span>
      <strong>{statusLabel[health.status]}</strong>
      {health.code && <code>{health.code}</code>}
    </div>
  );
}
