import { describe, expect, it } from "vitest";
import { validateProtocol } from "../../packages/protocols/src/index.js";

const envelope = {
  protocol_name: "worker",
  protocol_version: 1,
  message_id: "message-1",
  request_id: "request-1",
  trace_id: "trace-1",
};

describe("Worker protocol v1", () => {
  it.each([
    ["register", { registration_token: "one-time-token", worker_version: "0.1.0", capabilities: ["worker.runtime"], device_key_algorithm: "Ed25519", device_public_key: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" }],
    ["registered", { worker_id: "worker_01", session_credential: "opaque-session", expires_at: "2026-10-18T00:05:00Z" }],
    ["heartbeat", { worker_id: "worker_01", sent_at: "2026-10-18T00:00:00Z" }],
    ["lease", { task_id: "task_01", lease_id: "lease_01", fencing_token: 1, idempotency_key: "idem_01", expires_at: "2026-10-18T00:01:00Z", task: { required_capabilities: ["worker.runtime"], payload: {} } }],
    ["ack", { task_id: "task_01", lease_id: "lease_01", idempotency_key: "idem_01" }],
    ["progress", { task_id: "task_01", lease_id: "lease_01", idempotency_key: "idem_01", progress: { completed: 1, total: 2 } }],
    ["cancel", { task_id: "task_01", lease_id: "lease_01", reason_code: "USER_CANCELLED" }],
    ["result", { task_id: "task_01", lease_id: "lease_01", idempotency_key: "idem_01", status: "SUCCEEDED", result: {} }],
    ["error", { task_id: "task_01", lease_id: "lease_01", code: "WORKER_TASK_FAILED", retryable: false }],
  ] as const)("accepts %s messages", (message_type, payload) => {
    expect(validateProtocol("worker", { ...envelope, message_type, payload }).valid).toBe(true);
  });

  it("rejects a lease that omits its fencing token", () => {
    const result = validateProtocol("worker", {
      ...envelope,
      message_type: "lease",
      payload: {
        task_id: "task_01",
        lease_id: "lease_01",
        idempotency_key: "idem_01",
        expires_at: "2026-10-18T00:01:00Z",
      },
    });

    expect(result.valid).toBe(false);
  });

  it("requires an optional generic task payload to be an object", () => {
    const result = validateProtocol("worker", {
      ...envelope,
      message_type: "lease",
      payload: {
        task_id: "task_01",
        lease_id: "lease_01",
        fencing_token: 1,
        idempotency_key: "idem_01",
        expires_at: "2026-10-18T00:01:00Z",
        task: "role-specific fields are not part of the Worker envelope",
      },
    });

    expect(result.valid).toBe(false);
  });

  it("rejects registration without worker version and capabilities", () => {
    const result = validateProtocol("worker", {
      ...envelope,
      message_type: "register",
      payload: {
        registration_token: "one-time-token",
        device_key_algorithm: "Ed25519",
        device_public_key: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
      },
    });

    expect(result.valid).toBe(false);
  });

  it("rejects an unknown Worker protocol version", () => {
    const result = validateProtocol("worker", {
      ...envelope,
      protocol_version: 2,
      message_type: "heartbeat",
      payload: { worker_id: "worker_01", sent_at: "2026-10-18T00:00:00Z" },
    });

    expect(result.valid).toBe(false);
    expect(result.errors[0]?.code).toBe("PROTOCOL_VERSION_UNSUPPORTED");
  });
});
