import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import type { OfficeGetResult } from "../../types/ipc";
import { Button } from "../../ui";
import { parseOfficeCsv, serializeOfficeCsv, type CsvTable } from "./officeCsv";

export function OfficeDeliveryReuse({ selected, project, session, service, host, draftVersion, filePending, onApply }: {
  selected: OfficeGetResult; project: string | null; session: string | null; service: string | null; host: string;
  draftVersion: string; filePending: boolean; onApply: (text: string, table: CsvTable, filename: string) => void;
}): JSX.Element {
  const [candidate, setCandidate] = useState<{ csv: string; table: CsvTable; draft: string } | null>(null);
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0), draft = useRef(draftVersion), applyRef = useRef(onApply);
  draft.current = draftVersion; applyRef.current = onApply;
  const { attachment, delivery } = selected;
  useEffect(() => {
    const invalidate = () => { epoch.current++; setCandidate(null); setBusy(false); };
    invalidate(); setNotice("");
    const off = ipc.onSideCar(invalidate);
    return () => { epoch.current++; off(); };
  }, [project, session, service, host, attachment.id, attachment.sha256]);
  const current = (ticket: number) => {
    const s = useSessionStore.getState(), o = useOfficeDeliveryStore.getState();
    return ticket === epoch.current && ipc.agentBaseUrl === host && s.currentProjectId === project &&
      s.currentSessionId === session && o.projectId === project && o.host === host && o.serviceId === service &&
      o.selected?.delivery.id === delivery.id && o.selected.attachment.id === attachment.id && o.selected.attachment.sha256 === attachment.sha256;
  };
  const read = async (ticket: number) => {
    if (!project || !session || !service || !current(ticket)) throw new Error("项目、会话或服务已变化");
    const result = await typedIPC.extractAttachmentXlsx({ project_id: project, session_id: session, expected_service_id: service,
      attachment_id: attachment.id, expected_sha256: attachment.sha256, sheet_name: delivery.validation_report.sheet_name });
    if (!current(ticket)) throw new Error("上下文已变化，请重新选择成果");
    if (result.project_id !== project || result.session_id !== session || result.service_id !== service || result.attachment_id !== attachment.id ||
        result.sha256 !== attachment.sha256 || result.sheet_name !== delivery.validation_report.sheet_name || !result.rows.length)
      throw new Error("成果身份或工作表已变化");
    const csv = serializeOfficeCsv({ headers: result.rows[0], rows: result.rows.slice(1) });
    return { csv, table: parseOfficeCsv(csv) };
  };
  const prepare = async () => {
    if (busy || filePending) return;
    const ticket = ++epoch.current, before = draft.current; setBusy(true); setCandidate(null); setNotice("");
    try {
      const value = await read(ticket);
      if (!current(ticket)) return;
      if (draft.current !== before) throw new Error("草稿在读取期间发生变化，请重新准备");
      setCandidate({ ...value, draft: before });
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const apply = async () => {
    if (!candidate || busy || filePending) return;
    const ticket = ++epoch.current; setBusy(true); setNotice("");
    try {
      const value = await read(ticket);
      if (!current(ticket)) return;
      if (value.csv !== candidate.csv || draft.current !== candidate.draft) throw new Error("成果或草稿已变化，请重新准备");
      applyRef.current(value.csv, value.table, attachment.original_name);
      setCandidate(null); setNotice("已载入表格草稿并设置同名输出。请展开‘从 CSV 创建表格输入’，编辑并转换为表格输入后再生成新版本；原件未修改。");
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-2">
    <p>读取工作表到草稿，原件不变。单元格转为文本；样式、公式和图表等不保留。业务内容未经核验。</p>
    <Button type="button" variant="secondary" disabled={busy || filePending || !project || !session || !service || ipc.isMock} onClick={() => void prepare()}>准备继续编辑此成果</Button>
    {candidate && <div className="space-y-2"><p>将替换 CSV/表格编辑草稿并把输出文件名设为 {attachment.original_name}；已转换的 JSON、运行关联和汇总配置仍保留，请检查后再生成。请先核对前 10 行：</p>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words">{serializeOfficeCsv({ headers: candidate.table.headers, rows: candidate.table.rows.slice(0, 10) })}</pre>
      <p>共 {candidate.table.rows.length} 数据行；转换后请检查全部内容和列类型。</p>
      <Button type="button" variant="secondary" disabled={busy || filePending} onClick={() => void apply()}>确认替换表格草稿</Button>
      <Button type="button" variant="ghost" disabled={busy} onClick={() => setCandidate(null)}>取消</Button></div>}
    {busy && <p role="status">正在读取工作表…</p>}{notice && <p role="status">{notice}</p>}
  </div>;
}
