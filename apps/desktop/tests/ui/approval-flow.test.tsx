// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../../src/renderer/App";
import { createFakeApi } from "./fakes";

describe("approval workspace", () => {
  it("does not expose in-process demo approvals as real approval decisions", async () => {
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
