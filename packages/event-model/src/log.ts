import { assert_record_metadata, type RecordMetadata } from "./event.js";

export interface LogRecord extends RecordMetadata {
  level: string;
  component: string;
  run_id?: string;
  error_code?: string;
  retryable?: boolean;
}

export function create_log(input: LogRecord): LogRecord {
  assert_record_metadata(input);
  if (!input.level.trim()) throw new TypeError("level must be a non-empty string");
  if (!input.component.trim()) throw new TypeError("component must be a non-empty string");

  return {
    schema_version: input.schema_version,
    timestamp: input.timestamp,
    organization_id: input.organization_id,
    trace_id: input.trace_id,
    level: input.level,
    component: input.component,
    ...(input.run_id === undefined ? {} : { run_id: input.run_id }),
    ...(input.error_code === undefined ? {} : { error_code: input.error_code }),
    ...(input.retryable === undefined ? {} : { retryable: input.retryable }),
  };
}
