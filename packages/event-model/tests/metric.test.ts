import { describe, expect, it } from "vitest";
import { record_metric } from "../src/index.js";

describe("metrics", () => {
  it("redacts user-content labels while preserving operational labels", () => {
    const metric = record_metric(
      "tool.duration_ms",
      125,
      { component: "browser", prompt: "private user prompt" },
      {
        schema_version: 1,
        timestamp: "2026-10-17T08:00:00.000Z",
        organization_id: "org-01",
        trace_id: "trace-01",
      },
    );

    expect(metric.labels).toEqual({ component: "browser" });
    expect(JSON.stringify(metric)).not.toContain("private user prompt");
  });

  it("rejects complex labels that could contain unbounded user content", () => {
    expect(() =>
      record_metric(
        "tool.duration_ms",
        125,
        { component: { text: "private user prompt" } } as unknown as Record<string, string | number | boolean>,
        {
          schema_version: 1,
          timestamp: "2026-10-17T08:00:00.000Z",
          organization_id: "org-01",
          trace_id: "trace-01",
        },
      ),
    ).toThrow(/label/i);
  });
});
