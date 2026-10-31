import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ipc, typedIPC } from "../../ipc";
import * as content from "../../ipc/attachmentContent";
import { useSessionStore } from "../../stores/sessionStore";
import { ProjectAttachmentImport } from "./ProjectAttachmentImport";

const session = { id: "s1", project_id: "p1", archived: false, title: "任务", created_at: 1, updated_at: 1, model_id: null };
const row = { id: `att_${"a".repeat(32)}`, project_id: "p1", service_id: "svc", snapshot_id: "snap", original_name: "report.pdf",
  declared_type: "pdf", recognized_type: "pdf", size_bytes: 3, sha256: "b".repeat(64), version: 1, parser_status: "parsed",
  parser_scope: "inert_text", parser_reason: "ok", parser_version: 2, preview_truncated: false, quality_status: "unverified", created_at: "now" };

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: "s1", currentProjectId: "p1", sessions: [session] as never });
  vi.spyOn(ipc, "onSideCar").mockReturnValue(() => {});
  vi.spyOn(typedIPC, "getServiceIdentity").mockResolvedValue({ service_id: "svc" });
  vi.spyOn(typedIPC, "getSession").mockResolvedValue({ session } as never);
  vi.spyOn(typedIPC, "getAttachment").mockResolvedValue({ attachment: row, preview_text: "fixture-secret-parsed-body" } as never);
  vi.spyOn(content, "uploadAttachment").mockResolvedValue(row as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("saves a document and adds an explicitly unread reference to the draft", async () => {
  const listener = vi.fn((event: Event) => { (event as CustomEvent<{ result?: string }>).detail.result = "已添加到当前会话草稿，尚未发送。"; });
  window.addEventListener("minimax:append-context", listener);
  try {
    render(<ProjectAttachmentImport sessionId="s1" projectId="p1" host={ipc.agentBaseUrl} open onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("选择要导入项目的附件"), { target: { files: [new File(["PDF"], "report.pdf")] } });
    fireEvent.click(screen.getByRole("button", { name: "保存到项目" }));
    await screen.findByRole("button", { name: "加入引用到草稿" });
    expect(screen.getByRole("status")).toHaveTextContent("不代表模型已读取原件");
    fireEvent.click(screen.getByRole("button", { name: "加入引用到草稿" }));
    await waitFor(() => expect(listener).toHaveBeenCalledOnce());
    const detail = (listener.mock.calls[0][0] as CustomEvent<{ text: string }>).detail;
    expect(detail.text).toContain("未读取原件");
    expect(detail.text).toContain(row.id);
    expect(detail.text).not.toContain("fixture-secret-parsed-body");
  } finally { window.removeEventListener("minimax:append-context", listener); }
});

it("does not add a reference after the attachment identity changes", async () => {
  vi.mocked(typedIPC.getAttachment).mockResolvedValue({ attachment: { ...row, sha256: "c".repeat(64) }, preview_text: "" } as never);
  const listener = vi.fn(); window.addEventListener("minimax:append-context", listener);
  try {
    render(<ProjectAttachmentImport sessionId="s1" projectId="p1" host={ipc.agentBaseUrl} open onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("选择要导入项目的附件"), { target: { files: [new File(["PDF"], "report.pdf")] } });
    fireEvent.click(screen.getByRole("button", { name: "保存到项目" }));
    fireEvent.click(await screen.findByRole("button", { name: "加入引用到草稿" }));
    await screen.findByText(/未加入引用/);
    expect(listener).not.toHaveBeenCalled();
  } finally { window.removeEventListener("minimax:append-context", listener); }
});
