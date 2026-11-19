import { describe, expect, it, vi } from "vitest";
import { RuntimeChatService } from "../../src/main/ipc/chat";
import { RuntimeIpcError } from "../../src/main/runtime/ndjson-channel";

const input = {
  providerId: "deepseek",
  modelId: "deepseek/deepseek-flash",
  messages: [{ role: "user" as const, content: "hello" }],
  requestId: "11111111-2222-4333-8444-555555555555",
  traceId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
  externalProcessingConfirmed: true as const,
};

describe("RuntimeChatService", () => {
  it("sends the exact selected model and bounded manual request to Sidecar", async () => {
    const request = vi.fn().mockResolvedValue({
      status: "succeeded", provider_id: "deepseek", model: "deepseek/deepseek-flash", trace_id: input.traceId,
      safe_error_code: null, request_status: "http_response_received", failure_stage: "none", http_status: 200,
      response: "OK", usage: { total_tokens: 3 }, cost: 0.00001, latency_ms: 80,
    });
    const service = new RuntimeChatService(request);

    await expect(service.complete(input)).resolves.toMatchObject({ status: "succeeded", response: "OK" });
    expect(request).toHaveBeenCalledWith("chat.complete", {
      provider_id: "deepseek",
      model_id: "deepseek/deepseek-flash",
      messages: [{ role: "user", content: "hello" }],
      request_id: input.requestId,
      trace_id: input.traceId,
      external_processing_confirmed: true,
    }, { timeoutMs: 60_000, traceId: input.traceId });
  });

  it("accepts a valid provider response when the SDK does not expose an HTTP status", async () => {
    const request = vi.fn().mockResolvedValue({
      status: "succeeded", provider_id: "deepseek", model: "deepseek/deepseek-flash", trace_id: input.traceId,
      safe_error_code: null, request_status: "http_response_received", failure_stage: "none", http_status: null,
      response: "OK", usage: { total_tokens: 3 }, cost: null, latency_ms: 80,
    });
    const result = await new RuntimeChatService(request).complete(input);

    expect(result).toMatchObject({ status: "succeeded", response: "OK", http_status: null });
  });

  it("preserves known request state and safe error code when Sidecar disconnects", async () => {
    const request = vi.fn().mockRejectedValue(new RuntimeIpcError("SIDECAR_EXITED", "sensitive exception", {
      traceId: input.traceId,
      requestStatus: "unknown",
      failureStage: "sidecar_ipc",
      httpStatus: null,
    }));
    const service = new RuntimeChatService(request);

    const result = await service.complete(input);
    expect(result).toMatchObject({
      status: "failed", safe_error_code: "SIDECAR_EXITED", request_status: "unknown",
      failure_stage: "sidecar_ipc", trace_id: input.traceId,
    });
    expect(JSON.stringify(result)).not.toContain("sensitive exception");
  });

  it("preserves the safe unknown-completion code without exposing exception details", async () => {
    const request = vi.fn().mockResolvedValue({
      status: "failed", provider_id: input.providerId, model: input.modelId, trace_id: input.traceId,
      safe_error_code: "MODEL_CHAT_FAILED", request_status: "unknown", failure_stage: "unknown", http_status: null,
      response: null, usage: {}, cost: null, latency_ms: 0,
    });
    const result = await new RuntimeChatService(request).complete(input);

    expect(result).toMatchObject({
      status: "failed", safe_error_code: "MODEL_CHAT_FAILED", request_status: "unknown",
      failure_stage: "unknown", trace_id: input.traceId,
    });
  });

  it("does not pass through malformed sidecar text or invalid result shapes", async () => {
    const request = vi.fn().mockResolvedValue({
      status: "failed", provider_id: input.providerId, model: input.modelId, trace_id: input.traceId,
      safe_error_code: "raw API key marker", request_status: "unknown", failure_stage: "unknown", http_status: null,
      response: "secret response", usage: {}, cost: null, latency_ms: 0,
    });
    const result = await new RuntimeChatService(request).complete(input);

    expect(result.safe_error_code).toBe("IPC_REQUEST_FAILED");
    expect(result.response).toBeNull();
    expect(JSON.stringify(result)).not.toContain("raw API key marker");
    expect(JSON.stringify(result)).not.toContain("secret response");
  });
});
