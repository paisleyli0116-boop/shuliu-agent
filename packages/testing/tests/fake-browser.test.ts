import { describe, expect, it } from "vitest";

describe("Browser Tool payload contract (test double, never a real browser)", () => {
  it("replays isolated verified and uncertain results without inventing success", async () => {
    const module = await import("../src/fake-browser.js");
    expect(module, "Task 24 needs a FakeBrowser sharing the real adapter result contract").toBeDefined();
    const verified = { status: "VERIFIED", trace_id: "trace", session_id: "session", retryable: false,
      outcome_unknown: false, evidence_ref: "fixture:verified" };
    const uncertain = { ...verified, status: "UNVERIFIED", outcome_unknown: true, evidence_ref: "fixture:unknown" };
    const browser = new module!.FakeBrowser([verified, uncertain]);
    const first = browser.takeResult();
    first.status = "FAILED";
    expect(verified.status).toBe("VERIFIED");
    expect(browser.takeResult()).toEqual(uncertain);
    expect(() => browser.takeResult()).toThrow("No scripted browser result");
  });

  it("rejects leaked fields, missing evidence and verified unknown outcomes", async () => {
    const module = await import("../src/fake-browser.js");
    expect(module).toBeDefined();
    const result = { status: "VERIFIED", trace_id: "trace", session_id: "session", retryable: false,
      outcome_unknown: false, evidence_ref: "fixture:evidence" };
    for (const invalid of [{ ...result, cookies: "secret" }, { ...result, evidence_ref: "" },
      { ...result, outcome_unknown: true }, { ...result, retryable: true }]) {
      expect(() => new module!.FakeBrowser([invalid])).toThrow("Invalid browser result");
    }
  });
});
