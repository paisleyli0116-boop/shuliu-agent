// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../../src/renderer/App";
import { createFakeApi } from "./fakes";

describe("provider settings", () => {
  it("shows a safe explicit state when no provider profiles are configured", async () => {
    const user = userEvent.setup();
    const api = createFakeApi({
      providers: { list: vi.fn().mockResolvedValue({ providers: [] }) } as never,
    });
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "模型与连接" }));

    expect(await screen.findByText(/尚未配置可用模型/)).toBeTruthy();
  });

  it("reports the real connection test result without sending an unsaved key", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "模型与连接" }));
    await user.click(screen.getByRole("button", { name: "测试连接" }));

    expect(await screen.findByText(/真实 Provider 连接成功/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("模拟模式");
    expect(document.body.textContent).not.toContain("真实 Provider 尚未接入");
    expect(api.providers.testConnection).toHaveBeenCalledWith({
      providerId: "openai-compatible",
      capability: "fast_chat",
    });
    expect(localStorage.length).toBe(0);
  });

  it.each([
    ["MODEL_CREDENTIAL_REJECTED", "API Key 或凭据未通过验证"],
    ["MODEL_BALANCE_INSUFFICIENT", "DeepSeek 账户余额不足"],
    ["MODEL_RATE_LIMITED", "请求过于频繁"],
    ["MODEL_TIMEOUT", "连接超时"],
    ["MODEL_NETWORK_ERROR", "网络连接失败"],
    ["MODEL_REQUEST_REJECTED", "服务商拒绝了请求参数"],
    ["MODEL_PROVIDER_SERVER_ERROR", "服务商暂时异常"],
    ["MODEL_NOT_FOUND", "服务商未识别该模型"],
    ["UNRECOGNIZED_INTERNAL_CODE", "原因待确认"],
  ])("shows a safe localized message and trace ID for %s", async (safeErrorCode, expectedMessage) => {
    const user = userEvent.setup();
    const api = createFakeApi({
      providers: {
        testConnection: vi.fn().mockResolvedValue({
          status: "failed",
          provider_id: "openai-compatible",
          model: "openai/gpt-4.1-mini",
          capability: "fast_chat",
          latency_ms: 15,
          trace_id: "trace-safe-diagnostic",
          safe_error_code: safeErrorCode,
        }),
      } as never,
    });
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "模型与连接" }));
    await user.click(screen.getByRole("button", { name: "测试连接" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(expectedMessage);
    expect(alert.textContent).toContain("trace-safe-diagnostic");
    expect(alert.textContent).not.toContain(safeErrorCode);
  });

  it("sends a new credential only through the controlled save API and displays masked status", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "模型与连接" }));
    const keyInput = await screen.findByLabelText(/API Key/);
    await user.type(keyInput, "sk-demo-secret-value");
    await user.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() => expect(api.providers.saveProfile).toHaveBeenCalledWith({
      providerId: "openai-compatible",
      modelId: "fast-chat-demo",
      apiKey: "sk-demo-secret-value",
    }));
    await waitFor(() => expect((keyInput as HTMLInputElement).value).toBe(""));
    expect(await screen.findByText(/凭据已写入系统安全存储/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("sk-demo-secret-value");
    expect(localStorage.length).toBe(0);
    expect(log.mock.calls.flat().join(" ")).not.toContain("sk-demo-secret-value");
    log.mockRestore();
  });

  it("replaces a configured credential without echoing the old value", async () => {
    const user = userEvent.setup();
    const api = createFakeApi({
      providers: {
        list: vi.fn().mockResolvedValue({
          providers: [{
            id: "openai-compatible",
            name: "OpenAI Compatible",
            models: [{ id: "fast-chat-demo", name: "Fast Chat · Demo", capabilities: ["fast_chat"] }],
            configured: true,
          }],
        }),
      } as never,
    });
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "模型与连接" }));
    expect(await screen.findByText("已配置凭据 · 仅显示状态")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "更换密钥" }));
    const keyInput = screen.getByLabelText(/API Key/);
    await user.type(keyInput, "sk-new-credential");
    await user.click(screen.getByRole("button", { name: "保存新密钥" }));

    expect(api.providers.replaceCredential).toHaveBeenCalledWith({
      profileId: "openai-compatible",
      apiKey: "sk-new-credential",
    });
    expect(await screen.findByText(/凭据已写入系统安全存储/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "更换密钥" }));
    expect((await screen.findByLabelText(/API Key/) as HTMLInputElement).value).toBe("");
    expect(document.body.textContent).not.toContain("sk-new-credential");
  });

  it("does not render a raw provider error that could contain a credential", async () => {
    const user = userEvent.setup();
    const api = createFakeApi({
      providers: {
        saveProfile: vi.fn().mockRejectedValue(new Error("store failed with sk-sensitive-error")),
      } as never,
    });
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "模型与连接" }));
    await user.type(await screen.findByLabelText(/API Key/), "sk-sensitive-error");
    await user.click(screen.getByRole("button", { name: "保存配置" }));

    expect((await screen.findByRole("alert")).textContent).toContain("凭据保存失败");
    expect(document.body.textContent).not.toContain("sk-sensitive-error");
  });
});
