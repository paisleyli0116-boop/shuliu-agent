import { vi } from "vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import type { RendererApi } from "../../src/preload/api";

afterEach(() => cleanup());

export function createFakeApi(overrides: Partial<RendererApi> = {}): RendererApi {
  const api = {
    tasks: {
      list: vi.fn().mockResolvedValue([]),
      get: vi.fn().mockResolvedValue(null),
      create: vi.fn().mockImplementation(async (input) => ({
        task_id: "task-created",
        title: input.title,
        status: "QUEUED",
        created_at: "刚刚",
      })),
    },
    approvals: {
      list: vi.fn().mockResolvedValue([
        { approval_id: "approval-1", action: "读取受控资源", risk_level: "HIGH", status: "PENDING" },
      ]),
      decide: vi.fn().mockImplementation(async (input) => ({
        approval_id: input.approvalId,
        action: "读取受控资源",
        risk_level: "HIGH",
        status: input.decision === "approve" ? "APPROVED" : "DENIED",
      })),
    },
    runtime: { health: { get: vi.fn().mockResolvedValue({ status: "healthy" }) } },
    providers: {
      list: vi.fn().mockResolvedValue({
        providers: [
          {
            id: "openai-compatible",
            name: "OpenAI Compatible",
            models: [
              { id: "fast-chat-demo", name: "Fast Chat · Demo", capabilities: ["fast_chat"] },
              { id: "reasoning-demo", name: "Reasoning · Demo", capabilities: ["reasoning_high"] },
            ],
            configured: false,
          },
        ],
      }),
      testConnection: vi.fn().mockResolvedValue({
        status: "connected",
        provider_id: "openai-compatible",
        model: "openai/gpt-4.1-mini",
        capability: "fast_chat",
        latency_ms: 42,
        trace_id: "trace-test",
        safe_error_code: null,
        request_status: "http_response_received",
        failure_stage: "none",
        http_status: 200,
      }),
      saveProfile: vi.fn().mockResolvedValue({ profile_id: "openai-compatible", configured: true, storage: "OS_SECURE_STORAGE" }),
      replaceCredential: vi.fn().mockResolvedValue({ profile_id: "openai-compatible", configured: true, storage: "OS_SECURE_STORAGE" }),
    },
    chat: {
      complete: vi.fn().mockResolvedValue({
        status: "blocked",
        provider_id: "openai-compatible",
        model: "fast-chat-demo",
        trace_id: "11111111-2222-4333-8444-555555555555",
        safe_error_code: "MODEL_PROVIDER_NOT_CONFIGURED",
        request_status: "not_sent",
        failure_stage: "provider_profile",
        http_status: null,
        response: null,
        usage: {},
        cost: null,
        latency_ms: 0,
      }),
    },
    computer: {
      capability: vi.fn().mockResolvedValue({
        status: "unknown",
        safe_error_code: "COMPUTER_RUNTIME_UNAVAILABLE",
        trace_id: "11111111-2222-4333-8444-555555555555",
      }),
      observeForeground: vi.fn().mockResolvedValue({
        status: "blocked",
        title: null,
        elements: [],
        safe_error_code: "COMPUTER_RUNTIME_UNAVAILABLE",
        trace_id: "11111111-2222-4333-8444-555555555555",
      }),
    },
  } as unknown as RendererApi;

  for (const key of Object.keys(overrides) as Array<keyof RendererApi>) {
    Object.assign(api[key] as object, overrides[key]);
  }
  return api;
}
