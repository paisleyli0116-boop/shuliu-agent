// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../../src/renderer/App";
import { createFakeApi } from "./fakes";

describe("Foundation workspace capability boundaries", () => {
  it("provides the requested workspace navigation and labels unavailable backends honestly", async () => {
    const user = userEvent.setup();
    render(<App api={createFakeApi()} />);

    for (const item of [
      "智能工作台",
      "AI 对话",
      "岗位 Agent",
      "任务中心",
      "工作流",
      "知识库",
      "模型与连接",
      "工具与 Skills",
      "审批与运行记录",
      "系统设置",
    ]) {
      expect(screen.getByRole("button", { name: item })).toBeTruthy();
    }

    await user.click(screen.getByRole("button", { name: "AI 对话" }));
    expect((await screen.findAllByRole("heading", { name: "AI 对话" })).length).toBeGreaterThan(0);
    expect(screen.getByText(/Preload 目前只暴露 Provider 连接 canary/)).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "发送消息" })).toHaveProperty("disabled", true);
    expect(screen.queryByText(/示例对话|模拟回复/)).toBeNull();

    await user.click(screen.getByRole("button", { name: "岗位 Agent" }));
    expect(await screen.findByText(/没有已接入的 Agent 目录/)).toBeTruthy();
    expect(screen.queryByRole("article", { name: /采购 Agent|仓储 Agent|Boss Agent/ })).toBeNull();
  });

  it("shows only model profiles returned by the live Sidecar catalog", async () => {
    const user = userEvent.setup();
    const api = createFakeApi({
      providers: {
        list: vi.fn().mockResolvedValue({
          providers: [{
            id: "deepseek",
            name: "deepseek",
            models: [{ id: "deepseek/deepseek-flash", name: "deepseek/deepseek-flash", capabilities: ["fast_chat"] }],
            selected_model_id: "deepseek/deepseek-flash",
            configured: true,
          }],
        }),
      } as never,
    });
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "模型与连接" }));

    expect(await screen.findByRole("heading", { name: "运行时模型目录" })).toBeTruthy();
    expect(screen.getByRole("row", { name: /deepseek.*deepseek\/deepseek-flash.*已配置/ })).toBeTruthy();
    expect(screen.getByText(/最终模型路由由 Python Model Gateway 决定/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "添加模型" })).toBeNull();
    expect(api.providers.list).toHaveBeenCalled();
  });

  it("does not display or invoke the process-local task demo as a real task center", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "任务中心" }));

    expect(await screen.findByText(/没有接入真实任务持久化/)).toBeTruthy();
    expect(screen.queryByText("读取受控资源")).toBeNull();
    expect(screen.queryByRole("button", { name: /创建演示任务/ })).toBeNull();
    expect(api.tasks.list).not.toHaveBeenCalled();
    expect(api.tasks.create).not.toHaveBeenCalled();
  });

  it("does not display or invoke process-local approval demos as real approvals", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "审批与运行记录" }));

    expect(await screen.findByText(/真实审批服务与持久化运行审计尚未接入桌面/)).toBeTruthy();
    expect(screen.queryByText("读取受控资源")).toBeNull();
    expect(screen.queryByRole("button", { name: "演示批准" })).toBeNull();
    expect(screen.queryByRole("button", { name: "演示拒绝" })).toBeNull();
    expect(api.approvals.list).not.toHaveBeenCalled();
    expect(api.approvals.decide).not.toHaveBeenCalled();
  });
});
