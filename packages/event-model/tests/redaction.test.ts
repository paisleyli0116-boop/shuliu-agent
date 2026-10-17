import { describe, expect, it } from "vitest";
import { redact } from "../src/index.js";

describe("redaction", () => {
  it("removes secrets, cookies, and authorization values recursively", () => {
    const safe = redact({
      event: { name: "task.started", api_key: "secret-key" },
      headers: {
        Cookie: "session=secret",
        Authorization: "Bearer secret",
        client_secret: "client-secret-value",
      },
      secret_ref: "vault://credential-01",
      trace_id: "trace-01",
    });

    const serialized = JSON.stringify(safe);
    expect(serialized).not.toContain("secret-key");
    expect(serialized).not.toContain("session=secret");
    expect(serialized).not.toContain("Bearer secret");
    expect(serialized).not.toContain("client-secret-value");
    expect(serialized).toContain("vault://credential-01");
    expect(safe).toMatchObject({ trace_id: "trace-01" });
  });
});
