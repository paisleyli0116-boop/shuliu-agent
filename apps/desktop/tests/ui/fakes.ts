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
        simulated: true,
        outcome: "success",
        message: "模拟连接测试已通过；未向真实 Provider 发请求。",
      }),
      saveProfile: vi.fn().mockResolvedValue({ configured: true, simulated: true }),
      replaceCredential: vi.fn().mockResolvedValue({ configured: true, simulated: true }),
    },
  } as unknown as RendererApi;

  for (const key of Object.keys(overrides) as Array<keyof RendererApi>) {
    Object.assign(api[key] as object, overrides[key]);
  }
  return api;
}
