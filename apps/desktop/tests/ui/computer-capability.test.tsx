// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../../src/renderer/App";
import { createFakeApi } from "./fakes";

describe("Windows Computer Runtime status", () => {
  it("shows the actual Sidecar capability without claiming AI task execution", async () => {
    const user = userEvent.setup();
    const api = createFakeApi({
      computer: {
        capability: vi.fn().mockResolvedValue({
          status: "available",
          safe_error_code: null,
          trace_id: "11111111-2222-4333-8444-555555555555",
        }),
        observeForeground: vi.fn().mockResolvedValue({
          status: "observed",
          title: "Calculator",
          elements: [{ element_ref: "e-1", role: "Text", name: "Result", sensitive: false, enabled: true }],
          safe_error_code: null,
          trace_id: "11111111-2222-4333-8444-555555555556",
        }),
      },
    });
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "工具与 Skills" }));

    expect(await screen.findByRole("heading", { name: "桌面自动化（Windows）" })).toBeTruthy();
    expect(await screen.findByText("桌面环境可用")).toBeTruthy();
    expect(screen.getByText(/AI 对话尚未接入桌面操作闭环/)).toBeTruthy();
    expect(api.computer.capability).toHaveBeenCalledOnce();
    const observeButton = screen.getByRole("button", { name: "观察当前前台窗口" });
    await user.click(observeButton);
    expect(await screen.findByText("当前窗口：Calculator")).toBeTruthy();
    expect(screen.getByText(/Result/)).toBeTruthy();
    expect(api.computer.observeForeground).toHaveBeenCalledOnce();
  });
});
