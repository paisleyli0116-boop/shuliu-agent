import { describe, expect, it } from "vitest";
import { create_log } from "../src/index.js";

describe("structured logs", () => {
  it("keeps operational fields and does not add user content", () => {
    const log = create_log({
      schema_version: 1,
      timestamp: "2026-10-17T08:00:00.000Z",
      organization_id: "org-01",
      trace_id: "trace-01",
      level: "warning",
      component: "agent-core",
      run_id: "run-01",
      error_code: "TOOL_TIMEOUT",
      retryable: true,
    });

    expect(log).toMatchObject({
      trace_id: "trace-01",
      component: "agent-core",
      error_code: "TOOL_TIMEOUT",
      retryable: true,
    });
    expect(log).not.toHaveProperty("message");
  });
});
