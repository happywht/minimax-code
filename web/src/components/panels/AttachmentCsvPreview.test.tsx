import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { AttachmentOriginal } from "../../types/ipc";

const state = vi.hoisted(() => ({ request: vi.fn(), selected: null as unknown }));
vi.mock("../../ipc", () => ({
  ipc: { agentBaseUrl: "host", isMock: false, onSideCar: () => () => {} },
  typedIPC: { previewAttachmentCsv: state.request },
}));
vi.mock("../../stores", () => ({
  useSessionStore: { getState: () => ({ currentProjectId: "p", currentSessionId: "s" }) },
  useAttachmentStore: { getState: () => ({ projectId: "p", host: "host", serviceId: "svc", selected: state.selected }) },
}));
import { AttachmentCsvPreview } from "./AttachmentCsvPreview";

it("discards a pending result when the delimiter changes", async () => {
  const row = { id: "att_1", sha256: "a".repeat(64), declared_type: "csv", recognized_type: "utf8_text" } as AttachmentOriginal;
  state.selected = row;
  let resolve!: (value: unknown) => void;
  state.request.mockReset().mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  render(<AttachmentCsvPreview attachment={row} project="p" session="s" service="svc" host="host" />);
  fireEvent.click(screen.getByRole("button", { name: "读取 CSV 预览" }));
  fireEvent.change(screen.getByLabelText("CSV 预览分隔符"), { target: { value: "semicolon" } });
  await act(async () => resolve({ project_id: "p", session_id: "s", service_id: "svc", attachment_id: row.id,
    sha256: row.sha256, delimiter: "comma", headers: ["old"], rows: [], total_rows: 0, truncated: false }));
  expect(screen.queryByText(/解析格式：comma/)).toBeNull();
  state.request.mockResolvedValueOnce({ project_id: "p", session_id: "s", service_id: "svc", attachment_id: row.id,
    sha256: row.sha256, delimiter: "semicolon", headers: ["new"], rows: [["value"]], total_rows: 1, truncated: false });
  fireEvent.click(screen.getByRole("button", { name: "读取 CSV 预览" }));
  expect(await screen.findByText(/解析格式：semicolon/)).toBeTruthy();
  expect(state.request).toHaveBeenLastCalledWith(expect.objectContaining({ delimiter: "semicolon" }));
});
