import { redact } from "./redaction.js";

export interface RecordMetadata {
  schema_version: number;
  timestamp: string;
  organization_id: string;
  trace_id: string;
}

export interface EventRecord extends RecordMetadata {
  event_id: string;
  event_type: string;
  payload: Record<string, unknown>;
}

export interface EventRepository {
  append_event(event: EventRecord): string | Promise<string>;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireText(record: Record<string, unknown>, field: string): string {
  const value = record[field];
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new TypeError(`${field} must be a non-empty string`);
  }
  return value;
}

export function assert_record_metadata(value: unknown): asserts value is RecordMetadata {
  if (!isObject(value)) {
    throw new TypeError("record must be an object");
  }
  if (!Number.isInteger(value.schema_version) || Number(value.schema_version) < 1) {
    throw new TypeError("schema_version must be a positive integer");
  }
  const timestamp = requireText(value, "timestamp");
  if (!Number.isFinite(Date.parse(timestamp))) {
    throw new TypeError("timestamp must be a valid date-time string");
  }
  requireText(value, "organization_id");
  requireText(value, "trace_id");
}

export function parse_event(value: unknown): EventRecord {
  if (!isObject(value)) {
    throw new TypeError("event must be an object");
  }
  assert_record_metadata(value);
  const event_id = requireText(value, "event_id");
  const event_type = requireText(value, "event_type");
  if (!isObject(value.payload)) {
    throw new TypeError("payload must be an object");
  }

  return {
    event_id,
    schema_version: value.schema_version,
    timestamp: value.timestamp as string,
    organization_id: value.organization_id as string,
    trace_id: value.trace_id as string,
    event_type,
    payload: redact(value.payload) as Record<string, unknown>,
  };
}

export function serialize_event(event: EventRecord): string {
  return JSON.stringify(parse_event(event));
}
