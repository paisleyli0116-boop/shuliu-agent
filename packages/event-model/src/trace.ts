import { assert_record_metadata, type RecordMetadata } from "./event.js";

export interface Span extends RecordMetadata {
  span_id: string;
  parent_span_id?: string;
  started_at: string;
  ended_at?: string;
}

export function validate_trace(spans: readonly Span[]): void {
  const byId = new Map<string, Span>();
  for (const span of spans) {
    assert_record_metadata(span);
    if (!span.span_id.trim()) {
      throw new TypeError("span_id must be a non-empty string");
    }
    if (!Number.isFinite(Date.parse(span.started_at))) {
      throw new TypeError("started_at must be a valid date-time string");
    }
    if (span.ended_at !== undefined && !Number.isFinite(Date.parse(span.ended_at))) {
      throw new TypeError("ended_at must be a valid date-time string");
    }
    if (byId.has(span.span_id)) {
      throw new TypeError(`duplicate span_id: ${span.span_id}`);
    }
    byId.set(span.span_id, span);
  }

  for (const span of spans) {
    if (span.parent_span_id === undefined) continue;
    const parent = byId.get(span.parent_span_id);
    if (parent === undefined) {
      throw new TypeError(`parent span not found: ${span.parent_span_id}`);
    }
    if (parent.trace_id !== span.trace_id || parent.organization_id !== span.organization_id) {
      throw new TypeError(`parent span scope does not match: ${span.parent_span_id}`);
    }
    if (parent.span_id === span.span_id) {
      throw new TypeError(`span cannot be its own parent: ${span.span_id}`);
    }
  }

  for (const span of spans) {
    const visited = new Set<string>();
    let current: Span | undefined = span;
    while (current?.parent_span_id !== undefined) {
      if (visited.has(current.span_id)) {
        throw new TypeError(`span parent cycle detected: ${current.span_id}`);
      }
      visited.add(current.span_id);
      current = byId.get(current.parent_span_id);
    }
  }
}
