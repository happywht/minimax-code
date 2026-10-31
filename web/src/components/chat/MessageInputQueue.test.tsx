import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const fixture = vi.hoisted(() => {
  const session = { currentSessionId: "s1", currentProjectId: "p1", newSessionEngine: "native", projects: [{ id: "p1", name: "项目" }], sessions: [{ id: "s1", project_id: "p1", engine: "native" }] };
  return { session, status: "streaming", send: vi.fn(), cancel: vi.fn(), submit: vi.fn(), retry: vi.fn(), forget: vi.fn(), images: [] as unknown[], files: [] as unknown[], pending: null as unknown, ready: true, blocked: false };
});
vi.mock("../../stores", () => ({
  useChat: Object.assign((select: (v: unknown) => unknown) => select({ status: fixture.status, send: fixture.send, cancel: fixture.cancel, executionMode: "execute", setExecutionMode: vi.fn(), captureFileChanges: false, setCaptureFileChanges: vi.fn() }), { setState: vi.fn(), getState: () => ({ status: fixture.status }) }),
  useSessionStore: Object.assign((select: (v: unknown) => unknown) => select(fixture.session), { getState: () => fixture.session, subscribe: () => () => {} }),
}));
vi.mock("./useQueuedTask", () => ({ useQueuedTask: () => ({ pending: fixture.pending, busy: false, blocked: fixture.blocked, ready: fixture.ready, notice: null, submit: fixture.submit, retry: fixture.retry, forget: fixture.forget }) }));
vi.mock("./useAttachments", () => ({ useAttachments: () => ({ attachedFiles: fixture.files, attachedImages: fixture.images, dragging: false,
  fileInputRef: { current: null }, imageInputRef: { current: null }, handleDragOver: vi.fn(), handleDragLeave: vi.fn(), handleDrop: vi.fn(),
  handleFileSelect: vi.fn(), handleImageSelect: vi.fn(), clearAll: vi.fn(), removeFile: vi.fn(), removeImage: vi.fn() }) }));
vi.mock("./useVoiceInput", () => ({ useVoiceInput: () => ({ listening: false, toggleVoice: vi.fn() }) }));
vi.mock("./useMentionPicker", () => ({ useMentionPicker: () => ({ picker: null, filtered: [], agentsError: null, updateForInput: vi.fn(), handleKeyDown: vi.fn(), closePicker: vi.fn(), select: vi.fn() }) }));
vi.mock("./useMentionContext", () => ({ useMentionContext: () => ({ loading: false, buildContext: vi.fn() }) }));
vi.mock("./ComposerToolbar", () => ({ ComposerToolbar: () => null, ComposerSafetyControls: () => null }));
vi.mock("./ProjectAttachmentImport", () => ({ ProjectAttachmentImport: () => null }));
vi.mock("./MentionPickerDropdown", () => ({ MentionPickerDropdown: () => null }));
import { MessageInput } from "./MessageInput";

beforeEach(() => { localStorage.clear(); fixture.status = "streaming"; fixture.pending = null; fixture.ready = true; fixture.blocked = false; fixture.images = []; fixture.files = []; vi.clearAllMocks(); });
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });

it("uses the same editable composer to admit the next independent task", async () => {
  fixture.submit.mockResolvedValue("accepted");
  render(<MessageInput />);
  const input = screen.getByTestId("message-input-textarea");
  expect(input).toBeEnabled();
  fireEvent.change(input, { target: { value: "下一个任务" } });
  fireEvent.click(screen.getByRole("button", { name: "发送下一任务" }));
  await waitFor(() => expect(fixture.submit).toHaveBeenCalledWith(expect.objectContaining({ content: "下一个任务", draft: "下一个任务", mode: "execute" })));
  await waitFor(() => expect(input).toHaveValue(""));
  expect(fixture.send).not.toHaveBeenCalled();
});

it("preserves edits made while admission is pending", async () => {
  let resolve!: (value: string) => void;
  fixture.submit.mockReturnValue(new Promise((done) => { resolve = done; }));
  render(<MessageInput />);
  const input = screen.getByTestId("message-input-textarea");
  fireEvent.change(input, { target: { value: "原任务" } });
  fireEvent.click(screen.getByRole("button", { name: "发送下一任务" }));
  fireEvent.change(input, { target: { value: "后来修改的草稿" } });
  await act(async () => { resolve("accepted"); });
  expect(input).toHaveValue("后来修改的草稿");
  expect(screen.getByText(/当前输入已保留/)).toBeVisible();
});

it("queues existing image content with the text as a single request", async () => {
  fixture.images = [{ type: "image", media_type: "image/png", data: "YWJj" }];
  fixture.submit.mockResolvedValue("accepted");
  render(<MessageInput />);
  fireEvent.change(screen.getByTestId("message-input-textarea"), { target: { value: "检查图片" } });
  fireEvent.click(screen.getByRole("button", { name: "发送下一任务" }));
  await waitFor(() => expect(fixture.submit).toHaveBeenCalledWith(expect.objectContaining({ content: [
    { type: "text", text: "检查图片" }, { type: "image", media_type: "image/png", data: "YWJj" },
  ] })));
  expect(fixture.send).not.toHaveBeenCalled();
});

it("warns that stopping also cancels queued tasks", () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<MessageInput />);
  fireEvent.click(screen.getByRole("button", { name: "停止会话并取消排队任务" }));
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("同时取消其排队任务"));
  expect(fixture.cancel).toHaveBeenCalledOnce();
});


it("does not stop the session while the next-task admission is in flight", async () => {
  let resolve!: (value: string) => void;
  fixture.submit.mockReturnValue(new Promise((done) => { resolve = done; }));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<MessageInput />);
  fireEvent.change(screen.getByTestId("message-input-textarea"), { target: { value: "下一任务" } });
  fireEvent.click(screen.getByRole("button", { name: "发送下一任务" }));
  fireEvent.click(screen.getByRole("button", { name: "停止会话并取消排队任务" }));
  expect(confirm).not.toHaveBeenCalled();
  expect(fixture.cancel).not.toHaveBeenCalled();
  await act(async () => { resolve("accepted"); });
  fireEvent.click(screen.getByRole("button", { name: "停止会话并取消排队任务" }));
  expect(fixture.cancel).toHaveBeenCalledOnce();
});


it("keeps current edits when retrying an older uncertain request", async () => {
  fixture.pending = { project: "p1", draft: "原请求", request: { content: "原请求", idempotency_key: "same" } };
  fixture.retry.mockResolvedValue("accepted");
  render(<MessageInput />);
  const input = screen.getByTestId("message-input-textarea");
  fireEvent.change(input, { target: { value: "当前新草稿" } });
  expect(screen.getByRole("button", { name: "发送下一任务" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "重试同一请求" }));
  await waitFor(() => expect(fixture.retry).toHaveBeenCalledOnce());
  expect(input).toHaveValue("当前新草稿");
  expect(await screen.findByText(/当前草稿已保留/)).toBeVisible();
  expect(fixture.send).not.toHaveBeenCalled();
});

it("does not submit while the session's pending state has not loaded", () => {
  fixture.ready = false;
  fixture.pending = { project: "old", draft: "旧请求", request: { content: "旧请求", idempotency_key: "old" } };
  render(<MessageInput />);
  fireEvent.change(screen.getByTestId("message-input-textarea"), { target: { value: "待发送" } });
  expect(screen.getByRole("button", { name: "发送下一任务" })).toBeDisabled();
  expect(screen.getByText(/正在检查此会话的待确认请求/)).toBeVisible();
  expect(screen.queryByText(/上一条下一任务的接收结果待确认/)).toBeNull();
  expect(fixture.submit).not.toHaveBeenCalled();
});

it("does not silently send unresolved repository mentions in a queued task", () => {
  render(<MessageInput />);
  fireEvent.change(screen.getByTestId("message-input-textarea"), { target: { value: "请检查 @repo" } });
  fireEvent.click(screen.getByRole("button", { name: "发送下一任务" }));
  expect(screen.getByText(/排队暂不解析/)).toBeVisible();
  expect(fixture.submit).not.toHaveBeenCalled();
});


it("warns that an uncertain queue request can arrive after stopping", () => {
  fixture.pending = { project: "p1", draft: "原请求", request: { content: "原请求", idempotency_key: "same" } };
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<MessageInput />);
  fireEvent.click(screen.getByRole("button", { name: "停止会话并取消排队任务" }));
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("可能在停止后才被接收"));
  expect(fixture.cancel).not.toHaveBeenCalled();
});
