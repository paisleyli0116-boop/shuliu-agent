// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../../src/renderer/App";
import { createFakeApi } from "./fakes";

describe("runtime health workspace", () => {
  it("shows Safe Mode and refreshes health through the allowed API", async () => {
    const user = userEvent.setup();
    const health = vi.fn()
      .mockResolvedValueOnce({ status: "safe_mode", code: "SIDECAR_PROTOCOL_UNSUPPORTED" })
      .mockResolvedValueOnce({ status: "healthy" });
    const api = createFakeApi({ runtime: { health: { get: health } } as never });
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "系统设置" }));
    expect(screen.getByText("Safe Mode 已启用")).toBeTruthy();
    expect(screen.getAllByText(/新任务已暂停/).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "重新检查" }));
    expect((await screen.findAllByText("运行正常")).length).toBeGreaterThan(0);
    expect(health).toHaveBeenCalledTimes(2);
  });
});
