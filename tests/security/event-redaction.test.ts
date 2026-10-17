import { describe, expect, it } from "vitest";
import { redact } from "../../packages/event-model/src/index.js";

describe("event security boundaries", () => {
  it("does not retain credential values in nested observability payloads", () => {
    const safe = redact({
      headers: { authorization: "Bearer secret-value", cookie: "session=secret-value" },
      secret_ref: "vault://credential-01",
    });

    const serialized = JSON.stringify(safe);
    expect(serialized).not.toContain("Bearer secret-value");
    expect(serialized).not.toContain("session=secret-value");
    expect(serialized).toContain("vault://credential-01");
  });
});
