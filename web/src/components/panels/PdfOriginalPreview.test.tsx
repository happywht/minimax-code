import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ipc } from "../../ipc";
import { fetchAttachmentContent } from "../../ipc/attachmentContent";
import { useSessionStore } from "../../stores";
import type { AttachmentOriginal } from "../../types/ipc";
import { PdfOriginalPreview } from "./PdfOriginalPreview";

vi.mock("../../ipc/attachmentContent", () => ({ fetchAttachmentContent: vi.fn() }));
const row: AttachmentOriginal = { id: `att_${"1".repeat(32)}`, project_id: "p1", service_id: "svc1", snapshot_id: "b".repeat(32),
  original_name: "report.pdf", declared_type: "pdf", recognized_type: "pdf", size_bytes: 42, sha256: "a".repeat(64),
  version: 1, parser_status: "parsed", parser_version: 2, parser_reason: "ok", parser_scope: "inert_text",
  preview_truncated: false, quality_status: "unverified", created_at: "now" };
const fakePdf = { slice: () => ({ text: async () => "%PDF-" }) } as unknown as Blob;
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: "s1", currentProjectId: "p1" });
  Object.defineProperty(navigator, "pdfViewerEnabled", { configurable: true, value: true });
  vi.spyOn(ipc, "isMock", "get").mockReturnValue(false);
  vi.spyOn(ipc, "onSideCar").mockReturnValue(() => {});
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:pdf"), revokeObjectURL: vi.fn() }));
  vi.mocked(fetchAttachmentContent).mockReset().mockResolvedValue(fakePdf);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("opens verified bytes only on request and revokes them on session change", async () => {
  render(<PdfOriginalPreview attachment={row} />);
  expect(fetchAttachmentContent).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "查看 PDF 原件" }));
  expect(await screen.findByTitle("PDF 原件预览")).toHaveAttribute("src", "blob:pdf");
  expect(screen.getByText(/并非隔离环境/)).toBeVisible();
  act(() => useSessionStore.setState({ currentSessionId: "s2", currentProjectId: "p2" }));
  expect(screen.queryByTitle("PDF 原件预览")).toBeNull();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:pdf");
});

it("aborts pending bytes on project change and never opens a late response", async () => {
  const pending = deferred<Blob>();
  vi.mocked(fetchAttachmentContent).mockReturnValue(pending.promise);
  render(<PdfOriginalPreview attachment={row} />);
  fireEvent.click(screen.getByRole("button", { name: "查看 PDF 原件" }));
  const signal = vi.mocked(fetchAttachmentContent).mock.calls[0][2];
  act(() => useSessionStore.setState({ currentSessionId: "s2", currentProjectId: "p2" }));
  await act(async () => { pending.resolve(fakePdf); await pending.promise; });
  expect(signal.aborted).toBe(true);
  expect(screen.queryByTitle("PDF 原件预览")).toBeNull();
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});

it("leaves oversized and failed PDFs for download", () => {
  const view = render(<PdfOriginalPreview attachment={{ ...row, size_bytes: 6 * 1024 * 1024 }} />);
  expect(screen.getByText(/超过 5 MiB 预览上限/)).toBeVisible();
  expect(screen.getByRole("button", { name: "查看 PDF 原件" })).toBeDisabled();
  view.rerender(<PdfOriginalPreview attachment={{ ...row, parser_status: "parse_failed" }} />);
  expect(screen.getByText(/文本解析不可用或失败/)).toBeVisible();
  expect(fetchAttachmentContent).not.toHaveBeenCalled();
});

it("reports a verification failure without creating a viewer", async () => {
  vi.mocked(fetchAttachmentContent).mockRejectedValue(new Error("Attachment byte hash changed"));
  render(<PdfOriginalPreview attachment={row} />);
  fireEvent.click(screen.getByRole("button", { name: "查看 PDF 原件" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("hash changed");
  expect(screen.queryByTitle("PDF 原件预览")).toBeNull();
});

it("closes and revokes a preview when the service reconnects", async () => {
  let notify = () => {};
  vi.mocked(ipc.onSideCar).mockImplementation((callback) => {
    notify = () => callback({ status: "started" } as never);
    return () => {};
  });
  render(<PdfOriginalPreview attachment={row} />);
  fireEvent.click(screen.getByRole("button", { name: "查看 PDF 原件" }));
  await screen.findByTitle("PDF 原件预览");
  act(() => notify());
  expect(screen.queryByTitle("PDF 原件预览")).toBeNull();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:pdf");
});
