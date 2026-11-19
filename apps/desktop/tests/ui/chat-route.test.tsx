// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { RendererApi } from "../../src/preload/api";
import { ChatRoute } from "../../src/renderer/routes/chat";
import { createFakeApi } from "./fakes";

function chatApi(result = {
  status: "succeeded" as const,
  provider_id: "deepseek",
  model: "deepseek/deepseek-flash",
  trace_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
  safe_error_code: null,
  request_status: "http_response_received" as const,
  failure_stage: "none" as const,
  http_status: 200,
  response: "OK",
  usage: { total_tokens: 3 },
  cost: 0.00001,
  latency_ms: 80,
}) {
  return createFakeApi({
    providers: {
      list: vi.fn().mockResolvedValue({ providers: [{
        id: "deepseek",
        name: "DeepSeek",
        enabled: true,
        models: [{ id: "deepseek/deepseek-flash", name: "DeepSeek Flash", capabilities: ["fast_chat"] }],
        configured: true,
        selected_model_id: "deepseek/deepseek-flash",
      }] }),
    } as unknown as RendererApi["providers"],
    chat: { complete: vi.fn().mockResolvedValue(result) },
  });
}

describe("real chat route", () => {
  it("loads only enabled, configured fast-chat profiles and requires explicit public-data consent", async () => {
    const api = chatApi();
    render(<ChatRoute api={api} runtimeReady />);

    expect(await screen.findByRole("option", { name: "DeepSeek · DeepSeek Flash" })).toBeTruthy();
    const send = screen.getByRole("button", { name: "发送消息" });
    await userEvent.setup().type(screen.getByRole("textbox", { name: "发送消息" }), "只测试文本");
    expect(send).toHaveProperty("disabled", true);
    expect(api.chat.complete).not.toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: /公开且非敏感/ })).toBeTruthy();
  });

  it("sends only on a manual click and renders the actual structured Gateway result", async () => {
    const api = chatApi();
    const user = userEvent.setup();
    render(<ChatRoute api={api} runtimeReady />);
    await screen.findByRole("option", { name: "DeepSeek · DeepSeek Flash" });
    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "Reply with exactly OK.");
    await user.click(screen.getByRole("checkbox", { name: /公开且非敏感/ }));
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(await screen.findByText("OK")).toBeTruthy();
    expect(api.chat.complete).toHaveBeenCalledWith(expect.objectContaining({
      providerId: "deepseek",
      modelId: "deepseek/deepseek-flash",
      messages: [{ role: "user", content: "Reply with exactly OK." }],
      externalProcessingConfirmed: true,
    }));
    expect(api.chat.complete).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("checkbox", { name: /公开且非敏感/ })).toHaveProperty("checked", false);
  });

  it("shows a safe error code and trace without pretending a missing response succeeded", async () => {
    const api = chatApi({
      status: "failed",
      provider_id: "deepseek",
      model: "deepseek/deepseek-flash",
      trace_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      safe_error_code: "MODEL_CREDENTIAL_REJECTED",
      request_status: "http_response_received",
      failure_stage: "provider_http",
      http_status: 401,
      response: null,
      usage: {},
      cost: null,
      latency_ms: 20,
    });
    const user = userEvent.setup();
    render(<ChatRoute api={api} runtimeReady />);
    await screen.findByRole("option", { name: "DeepSeek · DeepSeek Flash" });
    await user.type(screen.getByRole("textbox", { name: "发送消息" }), "hello");
    await user.click(screen.getByRole("checkbox", { name: /公开且非敏感/ }));
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("MODEL_CREDENTIAL_REJECTED");
    expect(alert.textContent).toContain("HTTP 401");
    expect(alert.textContent).toContain("请求已收到服务端响应");
    expect(alert.textContent).toContain("Trace ID: aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee");
    expect(screen.queryByText("OK")).toBeNull();
  });

  it("does not allow sending when the runtime is unavailable", async () => {
    const api = chatApi();
    render(<ChatRoute api={api} runtimeReady={false} />);
    await screen.findByRole("option", { name: "DeepSeek · DeepSeek Flash" });
    expect(screen.getByRole("button", { name: "发送消息" })).toHaveProperty("disabled", true);
    expect(api.chat.complete).not.toHaveBeenCalled();
  });
});
