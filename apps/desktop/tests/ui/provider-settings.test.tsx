// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../../src/renderer/App";
import { createFakeApi } from "./fakes";

describe("provider settings", () => {
  it("marks connection checks as simulated and clears the API key input", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "模型与连接" }));
    const keyInput = await screen.findByLabelText(/API Key/);
    await user.type(keyInput, "sk-demo-should-not-leak");
    await user.click(screen.getByRole("button", { name: "测试连接" }));

    expect(await screen.findByText(/模拟连接测试已通过/)).toBeTruthy();
    await waitFor(() => expect((keyInput as HTMLInputElement).value).toBe(""));
    expect(api.providers.testConnection).toHaveBeenCalledWith({
      providerId: "openai-compatible",
      modelId: "fast-chat-demo",
    });
    expect(document.body.textContent).not.toContain("sk-demo-should-not-leak");
    expect(localStorage.length).toBe(0);
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
    expect(await screen.findByText(/凭据已安全保存/)).toBeTruthy();
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
    expect(await screen.findByText(/凭据已安全保存/)).toBeTruthy();
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
