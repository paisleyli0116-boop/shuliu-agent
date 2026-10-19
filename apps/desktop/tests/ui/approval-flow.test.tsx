// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../../src/renderer/App";
import { createFakeApi } from "./fakes";

describe("approval workspace", () => {
  it("lets the human deny a pending approval and shows the resulting state", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "审批" }));
    expect(await screen.findByText("读取受控资源")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "拒绝" }));

    expect(await screen.findByText("已拒绝")).toBeTruthy();
    expect(api.approvals.decide).toHaveBeenCalledWith({ approvalId: "approval-1", decision: "deny" });
    expect(screen.getByText(/演示审批/)).toBeTruthy();
    expect(screen.queryByText("1")).toBeNull();
  });
});
