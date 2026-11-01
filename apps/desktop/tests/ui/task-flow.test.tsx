// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../../src/renderer/App";
import { createFakeApi } from "./fakes";

describe("desktop task workspace", () => {
  it("keeps process-local demo tasks out of the real task center", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    render(<App api={api} />);

    await user.click(screen.getByRole("button", { name: "任务中心" }));

    expect(await screen.findByText(/没有接入真实任务持久化/)).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: /任务名称/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /创建/ })).toBeNull();
    expect(api.tasks.list).not.toHaveBeenCalled();
    expect(api.tasks.create).not.toHaveBeenCalled();
  });
});
