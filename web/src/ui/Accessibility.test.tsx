import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Modal } from "./Modal";
import { DropdownMenu } from "./DropdownMenu";
import { InspectorDrawer } from "../components/layout/InspectorDrawer";

describe("shared keyboard accessibility", () => {
  it("names a dialog from its visible heading", () => {
    render(<Modal title="核对内容" onClose={vi.fn()}><p>正文</p></Modal>);
    expect(screen.getByRole("dialog", { name: "核对内容" })).toBeInTheDocument();
  });

  it("moves through enabled menu items with arrows and restores focus on Escape", async () => {
    render(<DropdownMenu trigger={<button type="button">操作</button>} items={[
      { id: "one", label: "第一项", onClick: vi.fn() },
      { id: "disabled", label: "不可用", disabled: true, onClick: vi.fn() },
      { id: "last", label: "最后一项", onClick: vi.fn() },
    ]} />);
    const trigger = screen.getByRole("button", { name: "操作" });
    trigger.focus(); fireEvent.click(trigger);
    const first = screen.getByRole("menuitem", { name: "第一项" });
    const last = screen.getByRole("menuitem", { name: "最后一项" });
    await waitFor(() => expect(first).toHaveFocus());
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(last).toHaveFocus();
    fireEvent.keyDown(last, { key: "Escape" });
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("makes the narrow inspector dismissible from the keyboard", () => {
    const close = vi.fn();
    render(<InspectorDrawer open onClose={close}><button type="button">面板内容</button></InspectorDrawer>);
    expect(screen.getByRole("dialog")).toContainElement(screen.getByRole("button", { name: "关闭检查器" }));
    fireEvent.keyDown(screen.getByRole("button", { name: "关闭检查器" }), { key: "Escape" });
    expect(close).toHaveBeenCalledOnce();
  });

  it("skips autofocus targets hidden in a closed disclosure", () => {
    render(<Modal title="测试" onClose={vi.fn()}><details><summary>更多</summary><input data-autofocus aria-label="隐藏输入" /></details><button>下一步</button></Modal>);
    expect(screen.getByRole("button", { name: "关闭对话框" })).toHaveFocus();
  });
});
