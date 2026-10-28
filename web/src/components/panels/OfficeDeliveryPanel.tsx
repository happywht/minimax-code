import { useEffect, useState } from "react";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import type { OfficeTableSpec } from "../../types/ipc";
import { Button, Input, Textarea } from "../../ui";

const example = JSON.stringify({ sheet_name: "清单", columns: [
  { name: "名称", kind: "text" }, { name: "数量", kind: "number" },
], rows: [["钢材", 12], ["=原样文本", 3]] }, null, 2);

function saveJson(value: unknown, name: string): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function OfficeDeliveryPanel({ onClose, onText }: { onClose: () => void; onText: () => void }): JSX.Element {
  const project = useSessionStore((state) => state.currentProjectId), host = ipc.agentBaseUrl;
  const state = useOfficeDeliveryStore();
  const [filename, setFilename] = useState("清单.xlsx"), [runId, setRunId] = useState("");
  const [source, setSource] = useState(example), [key, setKey] = useState(() => crypto.randomUUID());
  const [formError, setFormError] = useState<string | null>(null);
  const scoped = state.projectId === project && state.host === host;
  useEffect(() => {
    setKey(crypto.randomUUID()); setFormError(null); setRunId("");
    void useOfficeDeliveryStore.getState().activate(project);
    return () => useOfficeDeliveryStore.getState().clear();
  }, [project, host]);
  const edited = () => { setKey(crypto.randomUUID()); setFormError(null); };
  const parsed = () => {
    if (new TextEncoder().encode(source).length > 1048576) throw new Error("结构化输入超过 1 MiB");
    const spec = JSON.parse(source) as OfficeTableSpec;
    if (!spec || typeof spec !== "object" || !Array.isArray(spec.columns) || !Array.isArray(spec.rows)) throw new Error("需要 sheet_name、columns、rows 的表格 JSON");
    return spec;
  };
  const submit = (requestKey: string) => {
    try {
      const spec = parsed(); setFormError(null);
      void state.generate({ filename, spec, run_id: runId || null, request_key: requestKey });
    } catch (error) { setFormError(error instanceof Error ? error.message : "表格 JSON 无效"); }
  };
  return <section className="flex h-full flex-col gap-3 overflow-auto p-4" aria-label="办公成果">
    <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">办公成果 · 纯数据 XLSX</h2>
      <div className="flex gap-2"><Button variant="secondary" onClick={onText}>文本成果</Button><Button variant="ghost" onClick={onClose}>关闭</Button></div></div>
    <p className="break-all text-xs text-ink-1">当前项目 {project ?? "未选择"} · Agent {host} · 服务 {scoped ? state.serviceId : "刷新中"}</p>
    <p>仅新建单表 XLSX，不读取或覆盖原文件。1–20 列、最多 1000 数据行，含表头最多 5000 格；输入 1 MiB、文件 2 MiB。空串作为空白；数字最多 15 有效位，长编号请用 text。</p>
    <p>不生成或计算公式、宏、外链。不做 PDF/OCR、版式、打印分页或业务核验；文本预览不还原 Excel 排版。</p>
    <p>在当前 Agent 服务登记不可变新文件；关联已有 run 只作来源引用，不改变其状态。切换页面只停止本页等待，已提交结果可刷新找回。</p>
    {ipc.isMock && <p role="status">演示模式不生成 Office 文件；请连接真实后端及 attachments 可选依赖。</p>}
    <form className="flex flex-col gap-2" onSubmit={(event) => { event.preventDefault(); submit(key); }}>
      <Input aria-label="XLSX 文件名" value={filename} onChange={(event) => { setFilename(event.target.value); edited(); }} />
      <Input aria-label="关联已有 Run ID（可选）" value={runId} onChange={(event) => { setRunId(event.target.value); edited(); }} placeholder="可留空，仅支持本项目普通 native host run" />
      <Textarea aria-label="结构化表格 JSON" rows={12} value={source} onChange={(event) => { setSource(event.target.value); edited(); }} />
      <div className="flex flex-wrap gap-2"><Button type="submit" disabled={!scoped || !state.serviceId || state.busy || ipc.isMock}>生成并登记 XLSX</Button>
        <Button type="button" variant="secondary" disabled={state.busy} onClick={() => void state.activate(project)}>刷新</Button>
        <Button type="button" variant="secondary" onClick={() => { try { saveJson(parsed(), "office-input.json"); } catch { setFormError("请先输入有效表格 JSON"); } }}>保存输入 JSON</Button></div>
    </form>
    {(formError || scoped && state.error) && <p role="alert" className="text-status-error">{formError || state.error}</p>}
    {state.busy && <p role="status">正在生成、核对或读取成果…</p>}
    <ul>{(scoped ? state.entries : []).map((entry) => <li key={entry.delivery.id}><button className="rounded border border-line p-2" disabled={state.busy} onClick={() => void state.select(entry.delivery.id)}>
      {entry.attachment.original_name} · v{entry.attachment.version} · 已登记 / 结构校验通过 · 业务未验证
    </button></li>)}</ul>
    {scoped && state.selected && <div className="space-y-2">
      <p>校验范围 {state.selected.delivery.validation_report.scope} · 全部 {state.selected.delivery.validation_report.cell_count} 格（含表头） · 未做版式或业务验收</p>
      <p>来源 run: {state.selected.delivery.run_id ?? "手动操作，无 run 关联"}</p>
      <p className="break-all text-xs">SHA-256: {state.selected.attachment.sha256}</p>
      <div className="flex flex-wrap gap-2"><Button disabled={state.busy} onClick={() => void state.download()}>下载 XLSX 原始字节</Button>
        <Button variant="secondary" onClick={() => saveJson(state.selected?.delivery.validation_report, "office-verification.json")}>下载校验报告</Button>
        <Button variant="secondary" disabled={state.busy || ipc.isMock} onClick={() => { const next = crypto.randomUUID(); setKey(next); submit(next); }}>生成同名新版本</Button></div>
      <p>下载后可用保留的输入 JSON 独立全值复验；校验报告不是数字签名。空串已按空白规范化。</p>
      {state.selected.attachment.preview_truncated && <p role="status">预览已截断（A1:T100 / 1500 格 / 64 KiB）；全文件值比较已另行完成，可下载完整文件。</p>}
      <pre className="whitespace-pre-wrap break-words rounded border border-line p-3" aria-label="办公成果纯文本预览">{state.selected.preview_text}</pre>
    </div>}
  </section>;
}
