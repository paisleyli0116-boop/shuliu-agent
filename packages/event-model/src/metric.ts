import { assert_record_metadata, type RecordMetadata } from "./event.js";
import { redact } from "./redaction.js";

export type MetricLabel = string | number | boolean;

export interface MetricRecord extends RecordMetadata {
  name: string;
  value: number;
  labels: Record<string, MetricLabel>;
}

function isContentLabel(name: string): boolean {
  return /(prompt|body|content|message|user.?input)/i.test(name);
}

export function record_metric(
  name: string,
  value: number,
  labels: Record<string, MetricLabel>,
  context: RecordMetadata,
): MetricRecord {
  assert_record_metadata(context);
  if (!name.trim()) throw new TypeError("metric name must be a non-empty string");
  if (!Number.isFinite(value)) throw new TypeError("metric value must be finite");

  const safeLabels: Record<string, MetricLabel> = {};
  for (const [key, label] of Object.entries(labels)) {
    if (isContentLabel(key)) continue;
    if (
      typeof label !== "string" &&
      typeof label !== "number" &&
      typeof label !== "boolean"
    ) {
      throw new TypeError(`metric label must be scalar: ${key}`);
    }
    if (typeof label === "number" && !Number.isFinite(label)) {
      throw new TypeError(`metric label must be finite: ${key}`);
    }
    safeLabels[key] = label;
  }
  const redactedLabels = redact(safeLabels);
  return {
    name,
    value,
    labels: redactedLabels as Record<string, MetricLabel>,
    schema_version: context.schema_version,
    timestamp: context.timestamp,
    organization_id: context.organization_id,
    trace_id: context.trace_id,
  };
}
