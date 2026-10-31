import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ send: vi.fn(), engine: "native", status: "idle", sessionId: "s1" }));
vi.mock("../../stores", () => ({
  useChat: Object.assign((select: (v: unknown) => unknown) => select({ status: state.status, send: state.send, cancel: vi.fn(), executionMode: "execute", setExecutionMode: vi.fn() }), { setState: vi.fn() }),
  useSessionStore: (select: (v: unknown) => unknown) => select({ currentSessionId: state.sessionId, currentProjectId: "p1", projects: [{ id: "p1", name: "项目" }], sessions: [{ id: state.sessionId, project_id: "p1", engine: state.engine }] }),
}));
vi.mock("./useAttachments", () => ({ useAttachments: () => ({
  attachedFiles: [], attachedImages: [], dragging: false, fileInputRef: { current: null }, imageInputRef: { current: null },
  handleDragOver: vi.fn(), handleDragLeave: vi.fn(), handleDrop: vi.fn(), handleFileSelect: vi.fn(), handleImageSelect: vi.fn(), clearAll: vi.fn(), removeFile: vi.fn(), removeImage: vi.fn(),
}) }));
vi.mock("./useVoiceInput", () => ({ useVoiceInput: () => ({ listening: false, toggleVoice: vi.fn() }) }));
vi.mock("./useMentionPicker", () => ({ useMentionPicker: () => ({ picker: null, filtered: [], agentsError: null, updateForInput: vi.fn(), handleKeyDown: vi.fn(), closePicker: vi.fn(), select: vi.fn() }) }));
vi.mock("./useMentionContext", () => ({ useMentionContext: () => ({ loading: false, buildContext: vi.fn() }) }));
vi.mock("./ComposerToolbar", () => ({ ComposerToolbar: () => null, ComposerSafetyControls: () => null }));
vi.mock("./MentionPickerDropdown", () => ({ MentionPickerDropdown: () => null }));
import { MessageInput } from "./MessageInput";
import { TaskTemplates } from "./TaskTemplates";

describe("task templates in the composer", () => {
  beforeEach(() => { window.localStorage.clear(); state.send.mockClear(); state.status = "idle"; state.engine = "native"; });

  it("preserves the reusable templates without presenting a template workflow in the composer", () => {
    const select = vi.fn();
    const view = render(<TaskTemplates disabled={false} codex={false} onSelect={select} />);
    fireEvent.click(screen.getByRole("button", { name: "专题研究" }));
    expect(select).toHaveBeenCalledWith(expect.stringContaining("读取相关网页原文"));
    view.unmount();
    render(<MessageInput />);
    fireEvent.click(screen.getByText("任务设置"));
    expect(screen.queryByText("任务模板与资料")).not.toBeInTheDocument();
    expect(screen.getByTestId("message-attach-btn")).toBeEnabled();
  });

  it("does not insert a reusable template when disabled", () => {
    const select = vi.fn();
    render(<TaskTemplates disabled codex={false} onSelect={select} />);
    expect(screen.getByRole("button", { name: "专题研究" })).toBeDisabled();
    expect(select).not.toHaveBeenCalled();
  });

  it("keeps Codex history and attachment limits available in context options", () => {
    state.engine = "codex";
    render(<MessageInput />);
    const note = screen.getByText(/Codex 使用独立模型配置/);
    expect(note).not.toBeVisible();
    fireEvent.click(screen.getByText("任务设置"));
    expect(note).toBeVisible();
    expect(note).toHaveTextContent("历史与外部线程绑定");
    expect(note).toHaveTextContent("图片最多 5 张");
  });

  it("keeps reusable office templates available independently", () => {
    const select = vi.fn();
    render(<TaskTemplates disabled={false} codex onSelect={select} />);
    fireEvent.click(screen.getByRole("button", { name: "资料成稿" }));
    fireEvent.click(screen.getByRole("button", { name: "表格处理" }));
    expect(select.mock.calls[0][0]).toContain("DOCX 和 PDF");
    expect(select.mock.calls[1][0]).toContain("CSV 或 XLSX");
  });
});


it("shows one clear scope summary for the selected session", () => {
  state.engine = "native";
  render(<MessageInput />);
  fireEvent.click(screen.getByText("任务设置"));
  const scope = within(screen.getByLabelText("任务提交位置"));
  expect(scope.getByText("服务地址")).toBeVisible();
  expect(scope.getByText("项目", { selector: "dt" })).toBeVisible();
  expect(scope.getByText("会话")).toBeVisible();
  expect(scope.getByText("目录配置")).toBeVisible();
  expect(scope.getByText("Native")).toBeVisible();
});
