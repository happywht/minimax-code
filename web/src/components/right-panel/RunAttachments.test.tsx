import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { AgentRun, AgentRunStep, AttachmentOriginal, AttachmentGetResult } from "../../types/ipc";
import { RunAttachments } from "./RunAttachments";

const hash = "a".repeat(64), id = `att_${"1".repeat(32)}`;
const run: AgentRun = { id: "r1", session_id: "s1", mode: "execute", status: "completed", title: "Task",
  created_at: "now", metadata: { project_id: "p1" } };
const step: AgentRunStep = { id: "step", run_id: "r1", session_id: "s1", kind: "observation", status: "completed",
  title: "成果", summary: "", started_at: "now", ordinal: 1,
  payload: { source: "run_attachment_v1", attachment_id: id, project_id: "p1", service_id: "svc1", sha256: hash, filename: "report.docx" } };
const attachment: AttachmentOriginal = { id, project_id: "p1", service_id: "svc1", snapshot_id: "b".repeat(32),
  original_name: "report.docx", declared_type: "docx", recognized_type: "docx", size_bytes: 1, sha256: hash,
  version: 1, parser_status: "parsed", parser_version: 1, parser_reason: "legacy_v1", parser_scope: "legacy_v1",
  preview_truncated: false, quality_status: "unverified", created_at: "now" };
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: "s1", currentProjectId: "p1" });
  vi.spyOn(ipc, "onSideCar").mockReturnValue(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows the verified linked attachment and escaped preview", async () => {
  vi.spyOn(typedIPC, "getAttachment").mockResolvedValue({ attachment, preview_text: "<script>plain</script>" });
  const { container } = render(<RunAttachments run={run} steps={[step]} />);
  fireEvent.click(screen.getByRole("button", { name: "report.docx" }));
  expect(await screen.findByLabelText("任务成果预览")).toHaveTextContent("<script>plain</script>");
  expect(container.querySelector("script")).toBeNull();
  expect(screen.getByText(/内容未验证/)).toBeVisible();
  expect(screen.getByRole("button", { name: "下载原件" })).toBeVisible();
});

it("ignores other run records and rejects wrong service metadata", async () => {
  vi.spyOn(typedIPC, "getAttachment").mockResolvedValue({ attachment: { ...attachment, service_id: "other" }, preview_text: "foreign" });
  render(<RunAttachments run={run} steps={[{ ...step, id: "foreign", run_id: "r2" }, step]} />);
  expect(screen.getAllByRole("button", { name: "report.docx" })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "report.docx" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("不匹配");
  expect(screen.queryByText("foreign")).toBeNull();
});

it("drops a late preview after session switch", async () => {
  const pending = deferred<AttachmentGetResult>();
  vi.spyOn(typedIPC, "getAttachment").mockReturnValue(pending.promise);
  render(<RunAttachments run={run} steps={[step]} />);
  fireEvent.click(screen.getByRole("button", { name: "report.docx" }));
  act(() => useSessionStore.setState({ currentSessionId: "s2", currentProjectId: "p2" }));
  await act(async () => { pending.resolve({ attachment, preview_text: "old" }); await pending.promise; });
  expect(screen.queryByLabelText("任务成果预览")).toBeNull();
});
