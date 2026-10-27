import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AttachmentOriginal, AttachmentListResult, AttachmentGetResult } from "../../types/ipc";
const wire = vi.hoisted(() => ({ request: vi.fn(), host: "http://127.0.0.1:8765" }));
vi.mock("../../ipc", async () => {
  const { bindTypedIPC } = await import("../../ipc/typed");
  const ipc = { get agentBaseUrl() { return wire.host; }, isMock: false, ping: vi.fn(), request: wire.request };
  return { ipc, typedIPC: bindTypedIPC(ipc as never) };
});
vi.mock("../../ipc/attachmentContent", () => ({ uploadAttachment: vi.fn(), fetchAttachmentContent: vi.fn() }));
vi.mock("../../stores", async () => {
  const { create } = await import("zustand");
  const { useAttachmentStore } = await import("../../stores/attachmentStore");
  const useSessionStore = create(() => ({ currentProjectId: "p", projects: [{ id: "p", name: "Project" }, { id: "other", name: "Other" }] }));
  return { useAttachmentStore, useSessionStore };
});
import { AttachmentsPanel } from "./AttachmentsPanel";
import { useAttachmentStore, useSessionStore } from "../../stores";
import { uploadAttachment, fetchAttachmentContent } from "../../ipc/attachmentContent";

const row: AttachmentOriginal = {
  id: `att_${"1".repeat(32)}`, project_id: "p", service_id: "svc_test", snapshot_id: "1".repeat(32),
  original_name: "report.txt", declared_type: "txt", recognized_type: "utf8_text", size_bytes: 10,
  sha256: "a".repeat(64), version: 1, parser_status: "parsed", parser_version: 1, parser_reason: "legacy_v1", parser_scope: "legacy_v1", preview_truncated: false, quality_status: "unverified", created_at: "now",
};
const preview = "<script>window.exploit=true</script>\n=1+1";
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };
beforeEach(() => {
  wire.host = "http://127.0.0.1:8765"; wire.request.mockReset();
  vi.mocked(uploadAttachment).mockReset().mockResolvedValue(row);
  vi.mocked(fetchAttachmentContent).mockReset().mockResolvedValue(new Blob(["original\r\n"]));
  useAttachmentStore.getState().clear(); useSessionStore.setState({ currentProjectId: "p" });
  wire.request.mockImplementation(async (method: string) => {
    if (method === "attachment.list") return { attachments: [], service_id: "svc_test" };
    if (method === "attachment.get") return { attachment: row, preview_text: preview };
    throw new Error("Unexpected method");
  });
});
afterEach(() => { cleanup(); useAttachmentStore.getState().clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
describe("project attachment panel and dispatch epochs", () => {
  it("uploads selected raw file, escapes preview, separates quality and downloads original", async () => {
    const createURL = vi.fn().mockReturnValue("blob:original");
    vi.stubGlobal("URL", { createObjectURL: createURL, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const { container } = render(<AttachmentsPanel onClose={vi.fn()} />);
    await screen.findByText("当前项目还没有附件原件。");
    const file = new File(["original\r\n"], "report.txt");
    fireEvent.change(screen.getByLabelText("选择附件原件"), { target: { files: [file] } });
    fireEvent.click(screen.getByText("上传原件"));
    await screen.findByLabelText("附件纯文本预览");
    expect(uploadAttachment).toHaveBeenCalledWith(file, "p", "svc_test", wire.host, expect.any(AbortSignal));
    expect(screen.getByLabelText("附件纯文本预览").textContent).toBe(preview);
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/v1.*已解析 \/ 未验证/)).toBeTruthy();
    fireEvent.click(screen.getByText("下载附件原始字节"));
    await waitFor(() => expect(click).toHaveBeenCalledOnce());
    expect(createURL).toHaveBeenCalledOnce();
  });
  it("shows unavailable/failed originals without pretending empty preview is successful", async () => {
    wire.request.mockImplementation(async (method: string) => method === "attachment.list"
      ? { attachments: [{ ...row, parser_status: "parse_failed" }], service_id: "svc_test" }
      : { attachment: { ...row, parser_status: "parse_failed" }, preview_text: "" });
    render(<AttachmentsPanel onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /report.txt/ }));
    await screen.findByText("当前状态不提供文本预览；可下载保存的原件。");
    expect(screen.queryByLabelText("附件纯文本预览")).toBeNull();
    expect(screen.getByText("下载附件原始字节")).toBeTruthy();
  });
  it("labels DOCX body-only empty extraction and retains original download", async () => {
    const empty = { ...row, original_name: "empty.docx", declared_type: "docx", recognized_type: "docx", parser_version: 2, parser_status: "empty", parser_scope: "docx_body_paragraphs_tables", parser_reason: "no_extracted_body_text_no_ocr" };
    wire.request.mockImplementation(async (method: string) => method === "attachment.list"
      ? { attachments: [empty], service_id: "svc_test" } : { attachment: empty, preview_text: "" });
    render(<AttachmentsPanel onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /empty.docx/ }));
    await screen.findByText("未提取到正文文本；未尝试 OCR。原件可能仍包含其他内容。");
    expect(screen.getByText(/解析算法 v2.*docx_body_paragraphs_tables.*no_extracted_body_text_no_ocr/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "下载附件原始字节" })).toBeTruthy();
  });
  it("drops stale project and host lists, and rejects foreign service associations", async () => {
    const pending = deferred<AttachmentListResult>(); wire.request.mockReturnValue(pending.promise);
    const first = useAttachmentStore.getState().activate("p");
    wire.host = "http://127.0.0.1:9999";
    pending.resolve({ attachments: [row], service_id: "svc_test" }); await first;
    expect(useAttachmentStore.getState().attachments).toEqual([]);
    wire.request.mockResolvedValue({ attachments: [row], service_id: "foreign" });
    await useAttachmentStore.getState().activate("p");
    expect(useAttachmentStore.getState().error).toMatch(/association changed/);
    wire.request.mockImplementation((_method: string, params: { project_id: string }) => params.project_id === "p"
      ? pending.promise : Promise.resolve({ attachments: [], service_id: "other-service" }));
    const stale = useAttachmentStore.getState().activate("p");
    await useAttachmentStore.getState().activate("other"); await stale;
    expect(useAttachmentStore.getState().serviceId).toBe("other-service");
    expect(useAttachmentStore.getState().attachments).toEqual([]);
  });
  it("refuses upload/select/download before dispatch after a host switch", async () => {
    await useAttachmentStore.getState().activate("p"); await useAttachmentStore.getState().select(row.id);
    const old = { ...useAttachmentStore.getState() }; wire.host = "http://127.0.0.1:9999"; wire.request.mockClear();
    for (const action of [() => useAttachmentStore.getState().upload(new File(["x"], "x.txt")), () => useAttachmentStore.getState().select(row.id), () => useAttachmentStore.getState().download()]) {
      useAttachmentStore.setState(old); await action(); expect(useAttachmentStore.getState().error).toMatch(/context changed/);
    }
    expect(wire.request).not.toHaveBeenCalled(); expect(uploadAttachment).not.toHaveBeenCalled(); expect(fetchAttachmentContent).not.toHaveBeenCalled();
  });
  it("rejects replacement service at same URL after upload or get", async () => {
    await useAttachmentStore.getState().activate("p");
    vi.mocked(uploadAttachment).mockResolvedValue({ ...row, service_id: "replacement" });
    await useAttachmentStore.getState().upload(new File(["x"], "x.txt"));
    expect(useAttachmentStore.getState().error).toMatch(/association changed/);
    expect(useAttachmentStore.getState().attachments).toEqual([]);
    wire.request.mockResolvedValue({ attachment: { ...row, service_id: "replacement" }, preview_text: "" });
    await useAttachmentStore.getState().select(row.id);
    expect(useAttachmentStore.getState().error).toMatch(/association changed/);
    expect(useAttachmentStore.getState().selected).toBeNull();
  });
  it("aborts slow upload and suppresses its result on project change", async () => {
    await useAttachmentStore.getState().activate("p");
    const pending = deferred<AttachmentOriginal>(); vi.mocked(uploadAttachment).mockReturnValue(pending.promise);
    const operation = useAttachmentStore.getState().upload(new File(["x"], "x.txt"));
    const signal = vi.mocked(uploadAttachment).mock.calls[0][4];
    await useAttachmentStore.getState().activate("other"); expect(signal.aborted).toBe(true);
    pending.resolve(row); await operation;
    expect(useAttachmentStore.getState().attachments).toEqual([]);
  });
  it("ignores earlier preview and aborts stale download without creating Blob URL", async () => {
    await useAttachmentStore.getState().activate("p");
    const earlier = deferred<AttachmentGetResult>(); const next = { ...row, id: `att_${"2".repeat(32)}` };
    wire.request.mockImplementation((_method: string, params: { attachment_id: string }) => params.attachment_id === row.id
      ? earlier.promise : Promise.resolve({ attachment: next, preview_text: "new" }));
    const old = useAttachmentStore.getState().select(row.id); await useAttachmentStore.getState().select(next.id);
    earlier.resolve({ attachment: row, preview_text: "old" }); await old;
    expect(useAttachmentStore.getState().preview).toBe("new");
    const pending = deferred<Blob>(); vi.mocked(fetchAttachmentContent).mockReturnValue(pending.promise);
    const createURL = vi.fn(); vi.stubGlobal("URL", { createObjectURL: createURL });
    const download = useAttachmentStore.getState().download(); useAttachmentStore.getState().clear();
    expect(vi.mocked(fetchAttachmentContent).mock.calls[0][2].aborted).toBe(true);
    pending.resolve(new Blob(["new"])); await download; expect(createURL).not.toHaveBeenCalled();
  });
});
