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
  it("loads a UTF-8 file into an empty draft and generates selected types", async () => {
    render(<OfficeDeliveryPanel onClose={vi.fn()} onText={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("生成并登记 XLSX")).not.toBeDisabled());
    fireEvent.click(screen.getByText("从 CSV 创建表格输入"));
    const file = new File(["\uFEFF编号,数量\n00123,12"], "清单.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("选择本地 CSV 文件"), { target: { files: [file] } });
    await screen.findByLabelText("第 2 列类型");
    expect(screen.getByLabelText("粘贴 CSV")).toHaveValue("编号,数量\n00123,12");
    fireEvent.change(screen.getByLabelText("第 2 列类型"), { target: { value: "number" } });
    fireEvent.click(screen.getByText("转换为表格输入"));
    fireEvent.click(screen.getByText("生成并登记 XLSX"));
    await screen.findByLabelText("办公成果纯文本预览");
    const sent = wire.request.mock.calls.find(([method]) => method === "office.generate_xlsx")![1];
    expect(sent.spec.rows).toEqual([["00123", 12]]);
  });

  it("retains existing drafts for invalid files and asks before replacing CSV", async () => {
    render(<OfficeDeliveryPanel onClose={vi.fn()} onText={vi.fn()} />);
    fireEvent.click(screen.getByText("从 CSV 创建表格输入"));
    const csv = screen.getByLabelText("粘贴 CSV"), editor = screen.getByLabelText("结构化表格 JSON") as HTMLTextAreaElement;
    const originalJson = editor.value;
    fireEvent.change(csv, { target: { value: "旧列\n旧值" } });
    const input = screen.getByLabelText("选择本地 CSV 文件");
    fireEvent.change(input, { target: { files: [new File([new Uint8Array([0xff])], "bad.csv")] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("不是有效的 UTF-8");
    expect(csv).toHaveValue("旧列\n旧值");
    fireEvent.change(input, { target: { files: [new File(["新列\n新值"], "new.csv")] } });
    await screen.findByRole("button", { name: "使用此文件替换 CSV 草稿" });
    expect(csv).toHaveValue("旧列\n旧值");
    fireEvent.click(screen.getByText("使用此文件替换 CSV 草稿"));
    expect(csv).toHaveValue("新列\n新值");
    expect(editor.value).toBe(originalJson);
    expect(screen.getByLabelText("第 1 列类型")).toHaveValue("text");
  });

  it("rejects oversized files before reading and ignores stale file reads", async () => {
    const readers: FileReader[] = [];
    const read = vi.spyOn(FileReader.prototype, "readAsArrayBuffer").mockImplementation(function (this: FileReader) { readers.push(this); });
    render(<OfficeDeliveryPanel onClose={vi.fn()} onText={vi.fn()} />);
    await waitFor(() => expect(useOfficeDeliveryStore.getState().busy).toBe(false));
    fireEvent.click(screen.getByText("从 CSV 创建表格输入"));
    const input = screen.getByLabelText("选择本地 CSV 文件"), csv = screen.getByLabelText("粘贴 CSV");
    fireEvent.change(input, { target: { files: [new File([new Uint8Array(1048577)], "large.csv")] } });
    expect(screen.getByRole("alert")).toHaveTextContent("超过 1 MiB");
    expect(read).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { files: [new File(["a\nold"], "old.csv")] } });
    fireEvent.click(input); // Opening a new picker invalidates an earlier read even if cancelled.
    fireEvent.change(csv, { target: { value: "草稿\n保留" } });
    Object.defineProperty(readers[0], "result", { value: new TextEncoder().encode("a\nold").buffer });
    act(() => readers[0].onload?.(new ProgressEvent("load") as ProgressEvent<FileReader>));
    expect(csv).toHaveValue("草稿\n保留");
    expect(screen.queryByText("使用此文件替换 CSV 草稿")).toBeNull();
  });

  it("stages a file if the user starts a draft during the read", async () => {
    const readers: FileReader[] = [];
    vi.spyOn(FileReader.prototype, "readAsArrayBuffer").mockImplementation(function (this: FileReader) { readers.push(this); });
    render(<OfficeDeliveryPanel onClose={vi.fn()} onText={vi.fn()} />);
    await waitFor(() => expect(useOfficeDeliveryStore.getState().busy).toBe(false));
    fireEvent.click(screen.getByText("从 CSV 创建表格输入"));
    fireEvent.change(screen.getByLabelText("选择本地 CSV 文件"), { target: { files: [new File(["a\n1"], "new.csv")] } });
    fireEvent.change(screen.getByLabelText("粘贴 CSV"), { target: { value: "正在编辑" } });
    const buffer = new ArrayBuffer(3); new Uint8Array(buffer).set([97, 10, 49]);
    Object.defineProperty(readers[0], "result", { value: buffer });
    act(() => readers[0].onload?.(new ProgressEvent("load") as ProgressEvent<FileReader>));
    expect(screen.getByLabelText("粘贴 CSV")).toHaveValue("正在编辑");
    expect(screen.getByText("使用此文件替换 CSV 草稿")).toBeVisible();
  });

  it("converts pasted CSV with selected types into a generation request", async () => {
    render(<OfficeDeliveryPanel onClose={vi.fn()} onText={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("生成并登记 XLSX")).not.toBeDisabled());
    fireEvent.click(screen.getByText("从 CSV 创建表格输入"));
    fireEvent.change(screen.getByLabelText("粘贴 CSV"), { target: { value: "编号,数量,有效\n00123,12,true\n=literal,3,false" } });
    fireEvent.click(screen.getByText("读取 CSV 列"));
    fireEvent.change(screen.getByLabelText("第 2 列类型"), { target: { value: "number" } });
    fireEvent.change(screen.getByLabelText("第 3 列类型"), { target: { value: "boolean" } });
    fireEvent.click(screen.getByText("转换为表格输入"));
    fireEvent.click(screen.getByText("生成并登记 XLSX"));
    await screen.findByLabelText("办公成果纯文本预览");
    const sent = wire.request.mock.calls.find(([method]) => method === "office.generate_xlsx")![1];
    expect(sent.spec.rows).toEqual([["00123", 12, true], ["=literal", 3, false]]);
  });

  it("identifies a conversion error without overwriting the editor", async () => {
    render(<OfficeDeliveryPanel onClose={vi.fn()} onText={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("生成并登记 XLSX")).not.toBeDisabled());
    const editor = screen.getByLabelText("结构化表格 JSON") as HTMLTextAreaElement;
    const original = editor.value;
    fireEvent.click(screen.getByText("从 CSV 创建表格输入"));
    fireEvent.change(screen.getByLabelText("粘贴 CSV"), { target: { value: "编号\n1234567890123456" } });
    fireEvent.click(screen.getByText("读取 CSV 列"));
    fireEvent.change(screen.getByLabelText("第 1 列类型"), { target: { value: "number" } });
    fireEvent.click(screen.getByText("转换为表格输入"));
    expect(screen.getByRole("alert")).toHaveTextContent("第 2 行「编号」");
    expect(editor.value).toBe(original);
    expect(wire.request.mock.calls.some(([method]) => method === "office.generate_xlsx")).toBe(false);
  });

  it("edits invalid cells and removes empty rows before generating, while preserving the CSV draft", async () => {
    render(<OfficeDeliveryPanel onClose={vi.fn()} onText={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("生成并登记 XLSX")).not.toBeDisabled());
    fireEvent.click(screen.getByText("从 CSV 创建表格输入"));
    const draft = "编号,数量\n001,wrong\n,\n002,5";
    fireEvent.change(screen.getByLabelText("粘贴 CSV"), { target: { value: draft } });
    fireEvent.click(screen.getByText("读取 CSV 列"));
    fireEvent.change(screen.getByLabelText("第 2 列类型"), { target: { value: "number" } });
    fireEvent.click(screen.getByText("转换为表格输入"));
    expect(screen.getByRole("alert")).toHaveTextContent("第 2 行「数量」");
    fireEvent.change(screen.getByLabelText("表格第 2 行第 2 列"), { target: { value: "12" } });
    expect(screen.getByLabelText("粘贴 CSV")).toHaveValue(draft);
    expect(screen.getByLabelText("粘贴 CSV")).toHaveAttribute("readonly");
    fireEvent.click(screen.getByRole("button", { name: "移除全空行（1）" }));
    fireEvent.click(screen.getByText("转换为表格输入"));
    fireEvent.click(screen.getByText("生成并登记 XLSX"));
    await screen.findByLabelText("办公成果纯文本预览");
    const sent = wire.request.mock.calls.find(([method]) => method === "office.generate_xlsx")![1];
    expect(sent.spec.rows).toEqual([["001", 12], ["002", 5]]);
    fireEvent.click(screen.getByRole("button", { name: "恢复读取时的表格" }));
    expect(screen.getByLabelText("表格第 2 行第 2 列")).toHaveValue("wrong");
    expect(screen.getByLabelText("粘贴 CSV")).not.toHaveAttribute("readonly");
  });

  it("edits and deletes the correct row beyond the first page", async () => {
    render(<OfficeDeliveryPanel onClose={vi.fn()} onText={vi.fn()} />);
    fireEvent.click(screen.getByText("从 CSV 创建表格输入"));
    fireEvent.change(screen.getByLabelText("粘贴 CSV"), { target: { value: `编号\n${Array.from({ length: 21 }, (_, i) => `id${i}`).join("\n")}` } });
    fireEvent.click(screen.getByText("读取 CSV 列"));
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByLabelText("表格第 22 行第 1 列")).toHaveValue("id20");
    fireEvent.change(screen.getByLabelText("表格第 22 行第 1 列"), { target: { value: "edited" } });
    fireEvent.click(screen.getByText("转换为表格输入"));
    const editor = screen.getByLabelText("结构化表格 JSON") as HTMLTextAreaElement;
    expect(JSON.parse(editor.value).rows[20]).toEqual(["edited"]);
    fireEvent.click(screen.getByRole("button", { name: "删除表格第 22 行" }));
    fireEvent.click(screen.getByText("转换为表格输入"));
    expect(JSON.parse(editor.value).rows).toHaveLength(20);
    expect(JSON.parse(editor.value).rows[19]).toEqual(["id19"]);
  });

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

  it("binds saved verification input to the generated receipt, not the edited form or another receipt", async () => {
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const create = vi.fn().mockReturnValue("blob:office");
    vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: vi.fn() });
    render(<OfficeDeliveryPanel onClose={vi.fn()} onText={vi.fn()} />);
    const button = screen.getByRole("button", { name: "生成并登记 XLSX" });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);
    await screen.findByRole("button", { name: "下载此成果的输入 JSON" });
    fireEvent.change(screen.getByLabelText("结构化表格 JSON"), { target: { value: '{"sheet_name":"changed","columns":[],"rows":[]}' } });
    fireEvent.click(screen.getByRole("button", { name: "下载此成果的输入 JSON" }));
    const blob = create.mock.calls[0][0] as Blob;
    const text = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(reader.result as string); reader.onerror = reject; reader.readAsText(blob);
    });
    expect(JSON.parse(text).sheet_name).toBe("清单");
    const other = { ...result, delivery: { ...result.delivery, id: "off_2" } };
    wire.request.mockResolvedValueOnce(other);
    await act(async () => { await useOfficeDeliveryStore.getState().select("off_2"); });
    expect(screen.queryByRole("button", { name: "下载此成果的输入 JSON" })).toBeNull();
    expect(screen.getByText(/当前编辑器内容不一定匹配/)).toBeVisible();
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
