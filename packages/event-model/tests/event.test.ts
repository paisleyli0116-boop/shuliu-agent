import { describe, expect, it } from "vitest";
import {
  parse_event,
  serialize_event,
  validate_trace,
  type AuditRecord,
  type AuditRepository,
  type EvidenceRecord,
} from "../src/index.js";

const event = {
  event_id: "evt-01",
  schema_version: 1,
  timestamp: "2026-10-17T08:00:00.000Z",
  organization_id: "org-01",
  trace_id: "trace-01",
  event_type: "task.started",
  payload: { task_id: "task-01" },
};

describe("event model", () => {
  it("round trips a versioned event with organization and trace scope", () => {
    expect(parse_event(JSON.parse(serialize_event(event)))).toEqual(event);
  });

  it("rejects an event without a schema version", () => {
    const { schema_version: _schema_version, ...withoutVersion } = event;
    expect(() => parse_event(withoutVersion)).toThrow(/schema_version/i);
  });

  it("removes credentials from event payloads before serialization", () => {
    const unsafe = {
      ...event,
      payload: {
        task_id: "task-01",
        authorization: "Bearer private-token",
        nested: { cookie: "session=private" },
      },
    };

    const serialized = serialize_event(unsafe);
    expect(serialized).not.toContain("private-token");
    expect(serialized).not.toContain("session=private");
    expect(serialized).toContain("task-01");
  });

  it("rejects a span whose parent is missing from the trace", () => {
    expect(() =>
      validate_trace([
        {
          span_id: "span-child",
          schema_version: 1,
          timestamp: "2026-10-17T08:00:00.000Z",
          organization_id: "org-01",
          trace_id: "trace-01",
          parent_span_id: "span-missing",
          started_at: "2026-10-17T08:00:00.000Z",
        },
      ]),
    ).toThrow(/parent/i);
  });

  it("rejects a broken parent link anywhere in a multi-span trace", () => {
    expect(() =>
      validate_trace([
        {
          span_id: "span-root",
          schema_version: 1,
          timestamp: "2026-10-17T08:00:00.000Z",
          organization_id: "org-01",
          trace_id: "trace-01",
          started_at: "2026-10-17T08:00:00.000Z",
        },
        {
          span_id: "span-child",
          schema_version: 1,
          timestamp: "2026-10-17T08:00:00.000Z",
          organization_id: "org-01",
          trace_id: "trace-01",
          parent_span_id: "span-root",
          started_at: "2026-10-17T08:00:00.000Z",
        },
        {
          span_id: "span-grandchild",
          schema_version: 1,
          timestamp: "2026-10-17T08:00:00.000Z",
          organization_id: "org-01",
          trace_id: "trace-01",
          parent_span_id: "span-missing",
          started_at: "2026-10-17T08:00:00.000Z",
        },
      ]),
    ).toThrow(/parent/i);
  });

  it("exposes audit as append-only and evidence through a policy reference", async () => {
    const audit: AuditRecord = {
      audit_id: "audit-01",
      schema_version: 1,
      timestamp: "2026-10-17T08:00:00.000Z",
      organization_id: "org-01",
      trace_id: "trace-01",
      actor_id: "user-01",
      action: "approval.recorded",
    };
    const repository: AuditRepository = {
      append_audit: async (record) => record.audit_id,
    };
    const evidence: EvidenceRecord = {
      evidence_id: "evidence-01",
      schema_version: 1,
      timestamp: "2026-10-17T08:00:00.000Z",
      organization_id: "org-01",
      trace_id: "trace-01",
      risk_level: "HIGH",
      evidence_kind: "execution_result",
      artifact_ref: "artifact://evidence-01",
      retention_policy_id: "retention-policy-default",
      metadata: {},
    };

    expect(await repository.append_audit(audit)).toBe("audit-01");
    expect(evidence.retention_policy_id).toBe("retention-policy-default");
    expect(evidence).not.toHaveProperty("retention_days");
  });
});
