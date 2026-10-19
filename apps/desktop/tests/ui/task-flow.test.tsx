// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../../src/renderer/App";
import { createFakeApi } from "./fakes";

describe("desktop task workspace", () => {
  it("creates a generic task through the typed desktop API", async () => {
    const user = userEvent.setup();
    const api = createFakeApi();
    render(<App api={api} />);

    await user.type(await screen.findByRole("textbox", { name: /任务名称/ }), "检查本周运行状态");
    await user.click(screen.getByRole("button", { name: "创建任务" }));

    expect(await screen.findByText("检查本周运行状态")).toBeTruthy();
    expect(api.tasks.create).toHaveBeenCalledWith({ title: "检查本周运行状态" });
  });

  it("shows a recoverable error when task loading fails", async () => {
    const api = createFakeApi({
      tasks: { list: async () => { throw new Error("opaque transport failure"); } } as never,
    });
    render(<App api={api} />);

    expect((await screen.findByRole("alert")).textContent).toContain("任务暂时无法加载");
    expect(screen.getByRole("button", { name: "重新加载" })).toBeTruthy();
  });
});
