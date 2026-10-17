import { describe, expect, it } from "vitest";

const loadTypes = async () =>
  import("../src/index.js")
    .then((module) => module)
    .catch(() => undefined);

describe("shared risk and policy types", () => {
  it("exposes the confirmed risk levels and policy decisions", async () => {
    const types = await loadTypes();
    expect(types?.RISK_LEVELS).toBeDefined();
    if (!types) return;

    expect(types.RISK_LEVELS).toEqual(["LOW", "MEDIUM", "HIGH", "CRITICAL"]);
    expect(types.POLICY_DECISIONS).toEqual(["ALLOW", "DENY", "REQUIRE_APPROVAL"]);
  });

  it("parses a known risk level and rejects an unknown one", async () => {
    const types = await loadTypes();
    expect(types?.parseRiskLevel).toBeTypeOf("function");
    if (!types) return;

    expect(types.parseRiskLevel("HIGH")).toEqual({ valid: true, value: "HIGH" });
    expect(types.parseRiskLevel("EXTREME")).toEqual({
      valid: false,
      error: { code: "UNKNOWN_RISK_LEVEL", path: "" },
    });
  });

  it("exposes only approval states described by the current foundation flow", async () => {
    const types = await loadTypes();
    expect(types?.APPROVAL_STATES).toBeDefined();
    if (!types) return;

    expect(types.APPROVAL_STATES).toEqual(["PENDING", "APPROVED", "REJECTED", "EXPIRED"]);
  });
});
