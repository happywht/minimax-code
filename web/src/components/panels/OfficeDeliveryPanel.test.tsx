import { webcrypto } from "node:crypto";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OfficeGetResult } from "../../types/ipc";

const wire = vi.hoisted(() => ({ request: vi.fn(), host: "http://127.0.0.1:8765", mock: false, content: vi.fn() }));
vi.mock("../../ipc", async () => {
  const { bindTypedIPC } = await import("../../ipc/typed");
  const ipc = { get agentBaseUrl() { return wire.host; }, get isMock() { return wire.mock; }, request: wire.request };
  return { ipc, typedIPC: bindTypedIPC(ipc as never) };
});
vi.mock("../../ipc/attachmentContent", () => ({ fetchAttachmentContent: wire.content }));
vi.mock("../../stores", async () => {
  const { create } = await import("zustand");
  return { useSessionStore: create(() => ({ currentProjectId: "p" })) };
});
import { OfficeDeliveryPanel } from "./OfficeDeliveryPanel";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import { useSessionStore } from "../../stores";

const result: OfficeGetResult = {
  delivery: { id: "off_1", project_id: "p", service_id: "svc_1", attachment_id: "att_1", run_id: null,
    request_key: "key", request_hash: "request", input_sha256: "input", writer_version: "3.1.5", validator_version: 1,
    created_at: "now", validation_report: { version: 1, scope: "xlsx_plain_cells_v1", input_sha256: "input",
      file_sha256: "file", size_bytes: 3, sheet_name: "data", data_rows: 1, column_count: 1, cell_count: 2,
      formula_count: 0, external_relationship_count: 0, macro_count: 0, structure_status: "passed",
      business_quality: "unverified", render_status: "not_attempted", writer: { name: "openpyxl", version: "3.1.5" }, validator_version: 1 } },
  attachment: { id: "att_1", project_id: "p", service_id: "svc_1", snapshot_id: "snap", original_name: "清单.xlsx",
    declared_type: "xlsx", recognized_type: "xlsx", size_bytes: 3, sha256: "file", version: 1, parser_status: "parsed",
    parser_version: 2, parser_reason: "ok", parser_scope: "inert_text", preview_truncated: false, quality_status: "unverified", created_at: "now" },
  preview_text: "<script>window.officeExploit=true</script>\n=literal",
};
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  wire.host = "http://127.0.0.1:8765"; wire.mock = false; wire.request.mockReset(); wire.content.mockReset();
  useOfficeDeliveryStore.getState().clear(); useSessionStore.setState({ currentProjectId: "p" });
  wire.request.mockImplementation(async (method: string) => {
    if (method === "office.list") return { deliveries: [], service_id: "svc_1" };
    if (method === "office.generate_xlsx" || method === "office.get") return result;
    throw new Error("Unexpected method");
  });
});
afterEach(() => { cleanup(); useOfficeDeliveryStore.getState().clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("manual Office delivery", () => {
  it("sends explicit structured intent, escapes preview, downloads verified attachment and preserves retry key", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    vi.stubGlobal("URL", { createObjectURL: vi.fn().mockReturnValue("blob:office"), revokeObjectURL: vi.fn() });
    wire.content.mockResolvedValue(new Blob(["abc"]));
    const { container } = render(<OfficeDeliveryPanel onClose={vi.fn()} onText={vi.fn()} />);
    const button = screen.getByRole("button", { name: "生成并登记 XLSX" });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);
    await screen.findByLabelText("办公成果纯文本预览");
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByLabelText("办公成果纯文本预览")).toHaveTextContent("<script>window.officeExploit=true</script>");
    expect(screen.getByText(/清单.xlsx · v1/)).toHaveTextContent("业务未验证");
    const sent = wire.request.mock.calls.find(([method]) => method === "office.generate_xlsx")![1];
    expect(sent).toMatchObject({ project_id: "p", expected_service_id: "svc_1", filename: "清单.xlsx", run_id: null });
    expect(sent.spec.rows[1][0]).toBe("=原样文本");
    expect(Object.keys(sent).sort()).toEqual(["expected_service_id", "filename", "project_id", "request_key", "run_id", "spec"]);
    fireEvent.click(button);
    await waitFor(() => expect(wire.request.mock.calls.filter(([method]) => method === "office.generate_xlsx")).toHaveLength(2));
    expect(wire.request.mock.calls.filter(([method]) => method === "office.generate_xlsx")[1][1].request_key).toBe(sent.request_key);
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(screen.getByText("下载 XLSX 原始字节"));
    await waitFor(() => expect(click).toHaveBeenCalledOnce());
    expect(wire.content).toHaveBeenCalledWith(result.attachment, wire.host, expect.any(AbortSignal));
    fireEvent.click(screen.getByText("生成同名新版本"));
    await waitFor(() => expect(wire.request.mock.calls.filter(([method]) => method === "office.generate_xlsx")).toHaveLength(3));
    expect(wire.request.mock.calls.filter(([method]) => method === "office.generate_xlsx")[2][1].request_key).not.toBe(sent.request_key);
  });

  it("rejects malformed input locally and keeps failure recovery visible without fabricating a receipt", async () => {
    render(<OfficeDeliveryPanel onClose={vi.fn()} onText={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("生成并登记 XLSX")).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText("结构化表格 JSON"), { target: { value: "{bad" } });
    fireEvent.click(screen.getByText("生成并登记 XLSX"));
    await screen.findByRole("alert");
    expect(wire.request.mock.calls.some(([method]) => method === "office.generate_xlsx")).toBe(false);
    fireEvent.change(screen.getByLabelText("结构化表格 JSON"), { target: { value: '{"sheet_name":"data","columns":[],"rows":[]}' } });
    wire.request.mockRejectedValueOnce(new Error("generation unavailable"));
    fireEvent.click(screen.getByText("生成并登记 XLSX"));
    expect(await screen.findByRole("alert")).toHaveTextContent("同一请求重试不会新建版本");
    expect(useOfficeDeliveryStore.getState().entries).toEqual([]);
  });

  it("blocks mock generation and explains its actual capability", async () => {
    wire.mock = true;
    render(<OfficeDeliveryPanel onClose={vi.fn()} onText={vi.fn()} />);
    await waitFor(() => expect(useOfficeDeliveryStore.getState().busy).toBe(false));
    expect(screen.getByText("生成并登记 XLSX")).toBeDisabled();
    expect(screen.getByText(/演示模式不生成 Office 文件/)).toBeVisible();
  });

  it("discards late project generation and host lists", async () => {
    await useOfficeDeliveryStore.getState().activate("p");
    const pending = deferred<OfficeGetResult>();
    wire.request.mockReturnValueOnce(pending.promise);
    const operation = useOfficeDeliveryStore.getState().generate({ filename: "x.xlsx", request_key: "key", spec: { sheet_name: "data", columns: [], rows: [] } });
    await useOfficeDeliveryStore.getState().activate("other");
    await act(async () => { pending.resolve(result); await operation; });
    expect(useOfficeDeliveryStore.getState().selected).toBeNull();
    const list = deferred<{ deliveries: never[]; service_id: string }>();
    wire.request.mockReturnValueOnce(list.promise);
    const old = useOfficeDeliveryStore.getState().activate("p");
    wire.host = "http://127.0.0.1:9000";
    list.resolve({ deliveries: [], service_id: "old" }); await old;
    expect(useOfficeDeliveryStore.getState().serviceId).toBeNull();
    await useOfficeDeliveryStore.getState().generate({ filename: "x.xlsx", request_key: "key", spec: { sheet_name: "data", columns: [], rows: [] } });
    expect(useOfficeDeliveryStore.getState().projectId).toBeNull();
  });

  it("rejects inconsistent returned association and never downloads it", async () => {
    await useOfficeDeliveryStore.getState().activate("p");
    wire.request.mockResolvedValueOnce({ ...result, attachment: { ...result.attachment, service_id: "foreign" } });
    await useOfficeDeliveryStore.getState().select(result.delivery.id);
    expect(useOfficeDeliveryStore.getState().error).toMatch(/association changed/);
    await useOfficeDeliveryStore.getState().download();
    expect(wire.content).not.toHaveBeenCalled();
  });

  it("aborts an in-flight verified download on project switch", async () => {
    await useOfficeDeliveryStore.getState().activate("p");
    await useOfficeDeliveryStore.getState().select(result.delivery.id);
    const pending = deferred<Blob>(); wire.content.mockReturnValueOnce(pending.promise);
    const operation = useOfficeDeliveryStore.getState().download();
    const signal = wire.content.mock.calls[0][2] as AbortSignal;
    await useOfficeDeliveryStore.getState().activate("other");
    expect(signal.aborted).toBe(true);
    pending.resolve(new Blob(["abc"])); await operation;
    expect(useOfficeDeliveryStore.getState().selected).toBeNull();
  });
});
