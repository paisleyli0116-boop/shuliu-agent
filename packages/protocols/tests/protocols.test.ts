import { describe, expect, it } from "vitest";

const loadValidator = async () =>
  import("../src/index.js")
    .then((module) => module.validateProtocol)
    .catch(() => undefined);

const validAgentMessage = {
  protocol_name: "agent",
  protocol_version: 1,
  message_id: "msg-agent-001",
  request_id: "req-agent-001",
  trace_id: "trace-agent-001",
  payload: {},
};

describe("protocol validation", () => {
  it("accepts a version 1 message with the required correlation envelope", async () => {
    const validateProtocol = await loadValidator();
    expect(validateProtocol).toBeTypeOf("function");
    if (!validateProtocol) return;

    expect(validateProtocol("agent", validAgentMessage)).toMatchObject({
      valid: true,
      schema_id: "urn:shuliu:protocol:agent:v1",
      errors: [],
    });
  });

  it("returns a stable error path for a missing correlation field", async () => {
    const validateProtocol = await loadValidator();
    expect(validateProtocol).toBeTypeOf("function");
    if (!validateProtocol) return;

    const { trace_id: _, ...message } = validAgentMessage;

    expect(validateProtocol("agent", message)).toMatchObject({
      valid: false,
      schema_id: "urn:shuliu:protocol:agent:v1",
      errors: [
        {
          code: "SCHEMA_VALIDATION_FAILED",
          path: "/trace_id",
          schema_id: "urn:shuliu:protocol:agent:v1",
        },
      ],
    });
  });

  it("rejects an unsupported major with a stable protocol version error", async () => {
    const validateProtocol = await loadValidator();
    expect(validateProtocol).toBeTypeOf("function");
    if (!validateProtocol) return;

    expect(validateProtocol("agent", { ...validAgentMessage, protocol_version: 2 })).toMatchObject({
      valid: false,
      schema_id: "urn:shuliu:protocol:agent:v1",
      errors: [
        {
          code: "PROTOCOL_VERSION_UNSUPPORTED",
          path: "/protocol_version",
          schema_id: "urn:shuliu:protocol:agent:v1",
        },
      ],
    });
  });

  it("rejects a message whose protocol name does not match the selected schema", async () => {
    const validateProtocol = await loadValidator();
    expect(validateProtocol).toBeTypeOf("function");
    if (!validateProtocol) return;

    expect(validateProtocol("agent", { ...validAgentMessage, protocol_name: "skill" })).toMatchObject({
      valid: false,
      schema_id: "urn:shuliu:protocol:agent:v1",
      errors: [
        {
          code: "SCHEMA_VALIDATION_FAILED",
          path: "/protocol_name",
          schema_id: "urn:shuliu:protocol:agent:v1",
        },
      ],
    });
  });

  it("rejects an unknown protocol name with a stable error", async () => {
    const validateProtocol = await loadValidator();
    expect(validateProtocol).toBeTypeOf("function");
    if (!validateProtocol) return;

    expect(validateProtocol("unknown", validAgentMessage)).toMatchObject({
      valid: false,
      errors: [
        {
          code: "PROTOCOL_UNKNOWN",
          path: "/protocol_name",
          schema_id: "urn:shuliu:protocol:unknown:v1",
        },
      ],
    });
  });
});
