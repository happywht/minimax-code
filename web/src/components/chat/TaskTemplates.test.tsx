import { fireEvent, render, screen } from "@testing-library/react";
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
vi.mock("./ComposerToolbar", () => ({ ComposerToolbar: () => null }));
vi.mock("./MentionPickerDropdown", () => ({ MentionPickerDropdown: () => null }));
import { MessageInput } from "./MessageInput";

describe("task templates in the composer", () => {
  beforeEach(() => { window.localStorage.clear(); state.send.mockClear(); state.status = "idle"; state.engine = "native"; });

  it("adds an editable template without sending or replacing the existing draft", () => {
    render(<MessageInput />);
    fireEvent.click(screen.getByText("任务模板与资料"));
    const input = screen.getByTestId("message-input-textarea") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "已有目标：研究水务" } });
    fireEvent.click(screen.getByRole("button", { name: "专题研究" }));
    expect(input.value).toContain("已有目标：研究水务\n\n");
    expect(input.value).toContain("读取相关网页原文");
    expect(input.value).toContain("来源标题、链接和访问时间");
    expect(state.send).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: input.value.replace("（请补充）", "水务政策") } });
    expect(input.value).toContain("水务政策");
  });

  it("disables template insertion while a run is active", () => {
    state.status = "streaming";
    render(<MessageInput />);
    fireEvent.click(screen.getByText("任务模板与资料"));
    expect(screen.getByRole("button", { name: "专题研究" })).toBeDisabled();
    expect(state.send).not.toHaveBeenCalled();
  });

  it("offers office templates and preserves the current engine", () => {
    state.engine = "codex";
    render(<MessageInput />);
    fireEvent.click(screen.getByText("任务模板与资料"));
    expect(screen.getByText("当前引擎的附件和办公工具能力可能不同")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "资料成稿" }));
    fireEvent.click(screen.getByRole("button", { name: "表格处理" }));
    const value = (screen.getByTestId("message-input-textarea") as HTMLTextAreaElement).value;
    expect(value).toContain("DOCX 和 PDF");
    expect(value).toContain("CSV 或 XLSX");
    expect(value).toContain("柱状图或折线图");
    expect(state.send).not.toHaveBeenCalled();
    expect(state.engine).toBe("codex");
  });
});
