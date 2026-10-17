import { describe, expect, it } from "vitest";

const loadTypes = async () =>
  import("../src/index.js")
    .then((module) => module)
    .catch(() => undefined);

const validError = {
  code: "MODEL_TIMEOUT",
  category: "MODEL",
  message: "The model request timed out.",
  retryable: true,
  recoverable: true,
  severity: "warning",
  details: { provider: "primary" },
  cause: "upstream timeout",
  trace_id: "trace-001",
};

describe("ShuLiuError envelope", () => {
  it("creates, serializes, and parses a valid error without losing its fields", async () => {
    const types = await loadTypes();
    expect(types?.createShuLiuError).toBeTypeOf("function");
    if (!types) return;

    const error = types.createShuLiuError(validError);
    const serialized = types.serializeShuLiuError(error);

    expect(types.parseShuLiuErrorJson(serialized)).toEqual({ valid: true, value: validError });
  });

  it("rejects an unknown error category with a stable path and code", async () => {
    const types = await loadTypes();
    expect(types?.parseShuLiuError).toBeTypeOf("function");
    if (!types) return;

    expect(types.parseShuLiuError({ ...validError, category: "PURCHASING" })).toMatchObject({
      valid: false,
      errors: [{ code: "INVALID_ERROR_CATEGORY", path: "/category" }],
    });
  });

  it("rejects an error without trace_id", async () => {
    const types = await loadTypes();
    expect(types?.parseShuLiuError).toBeTypeOf("function");
    if (!types) return;

    const { trace_id: _, ...missingTrace } = validError;
    expect(types.parseShuLiuError(missingTrace)).toMatchObject({
      valid: false,
      errors: [{ code: "MISSING_REQUIRED_FIELD", path: "/trace_id" }],
    });
  });

  it("rejects a retryable field that is not boolean", async () => {
    const types = await loadTypes();
    expect(types?.parseShuLiuError).toBeTypeOf("function");
    if (!types) return;

    expect(types.parseShuLiuError({ ...validError, retryable: "yes" })).toMatchObject({
      valid: false,
      errors: [{ code: "INVALID_FIELD_TYPE", path: "/retryable" }],
    });
  });
});
