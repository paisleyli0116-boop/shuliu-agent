// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button, DataTable, EmptyState, InputField, Modal, SelectField, StatusIndicator, Toast, Typography } from "../../src/renderer/components/design-system";

describe("shared Foundation design system", () => {
  it("provides semantic typography variants with consistent variant classes", () => {
    render(
      <div>
        <Typography variant="page-title">工作台</Typography>
        <Typography variant="section-title">运行状态</Typography>
        <Typography variant="body">页面正文</Typography>
        <Typography variant="caption">辅助说明</Typography>
      </div>,
    );

    expect(screen.getByRole("heading", { level: 1, name: "工作台" }).className).toContain("typography--page-title");
    expect(screen.getByRole("heading", { level: 2, name: "运行状态" }).className).toContain("typography--section-title");
    expect(screen.getByText("页面正文").className).toContain("typography--body");
    expect(screen.getByText("辅助说明").className).toContain("typography--caption");
  });

  it("keeps common controls accessible and stateful", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <div>
        <Button variant="primary" disabled>保存</Button>
        <InputField label="工作区名称" hint="用于显示当前工作区" />
        <SelectField label="模型 Provider"><option value="deepseek">DeepSeek</option></SelectField>
        <StatusIndicator tone="success" label="运行正常" />
        <EmptyState title="暂无任务" description="接入真实任务服务后会在这里显示。" />
        <DataTable caption="运行记录" columns={["状态"]} rows={[["已完成"]]} />
        <Toast tone="error">读取失败</Toast>
        <Modal open title="确认操作" onClose={onClose}><p>请确认</p></Modal>
      </div>,
    );

    expect(screen.getByRole("button", { name: "保存" })).toHaveProperty("disabled", true);
    const input = screen.getByRole("textbox", { name: /工作区名称/ });
    expect(input.getAttribute("aria-describedby")).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "模型 Provider" })).toBeTruthy();
    expect(screen.getByText("运行正常")).toBeTruthy();
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "暂无任务" })).toBeTruthy();
    expect(screen.getByRole("table", { name: "运行记录" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("读取失败");
    expect(screen.getByRole("dialog", { name: "确认操作" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "关闭对话框" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});




