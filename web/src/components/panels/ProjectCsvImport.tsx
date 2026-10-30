import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { fetchAttachmentContent } from "../../ipc/attachmentContent";
import { useSessionStore } from "../../stores";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import type { AttachmentOriginal } from "../../types/ipc";
import { Button, Input } from "../../ui";
import { parseOfficeCsv, type CsvTable } from "./officeCsv";

type Candidate = { row: AttachmentOriginal; text: string; table: CsvTable; revision: number };
export function ProjectCsvImport({ project, session, service, host, draftVersion, disabled, onApply }: {
  project: string | null; session: string | null; service: string | null; host: string; draftVersion: string; disabled: boolean;
  onApply: (text: string, table: CsvTable) => void;
}): JSX.Element {
  const [query, setQuery] = useState(""), [applied, setApplied] = useState(""), [rows, setRows] = useState<AttachmentOriginal[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null), [candidate, setCandidate] = useState<Candidate | null>(null);
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const epoch = useRef(0), abort = useRef<AbortController | null>(null), callback = useRef(onApply), blocked = useRef(disabled);
  const draft = useRef({ value: draftVersion, revision: 0 });
  callback.current = onApply; blocked.current = disabled;
  if (draft.current.value !== draftVersion) draft.current = { value: draftVersion, revision: draft.current.revision + 1 };
  useEffect(() => {
    const invalidate = () => { epoch.current++; abort.current?.abort(); setRows([]); setCursor(null); setLoaded(false); setCandidate(null); setBusy(false); };
    invalidate(); setQuery(""); setApplied(""); setNotice(""); const off = ipc.onSideCar(invalidate);
    return () => { epoch.current++; abort.current?.abort(); off(); };
  }, [project, session, service, host]);
  const current = (ticket: number) => {
    const active = useSessionStore.getState(), office = useOfficeDeliveryStore.getState();
    return ticket === epoch.current && ipc.agentBaseUrl === host && active.currentProjectId === project && active.currentSessionId === session &&
      office.projectId === project && office.host === host && office.serviceId === service;
  };
  const check = async (ticket: number) => {
    if (!project || !session || !service || !current(ticket)) throw new Error("项目、会话或服务已变化");
    const [identity, bound] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session)]);
    if (!current(ticket) || identity.service_id !== service || bound.session.id !== session || bound.session.project_id !== project)
      throw new Error("项目、会话或服务已变化");
  };
  const list = async (more: boolean) => {
    if (!project || !session || !service || disabled || busy) return;
    const ticket = ++epoch.current, term = more ? applied : query.trim(), next = more ? cursor : null;
    setBusy(true); setNotice("");
    try {
      await check(ticket);
      const result = await typedIPC.listAttachments(project, next ?? undefined, term || undefined);
      await check(ticket);
      if (result.service_id !== service || result.attachments.length > 200 || result.attachments.some((row) => row.project_id !== project || row.service_id !== service) ||
          (result.next_cursor != null && (result.next_cursor === next || (term ? !result.next_cursor.startsWith("q_") : !/^att_[a-f0-9]{32}$/.test(result.next_cursor)))))
        throw new Error("附件列表或游标不匹配");
      const csv = result.attachments.filter((row) => row.declared_type === "csv" && row.recognized_type === "utf8_text" && row.size_bytes <= 1048576);
      setRows((previous) => more ? [...previous, ...csv.filter((row) => !previous.some((item) => item.id === row.id))] : csv);
      setCursor(result.next_cursor ?? null); setApplied(term); setCandidate(null); setLoaded(true);
    } catch (error) { if (current(ticket)) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (current(ticket)) setBusy(false); }
  };
  const read = async (row: AttachmentOriginal, ticket: number, revision: number): Promise<{ text: string; table: CsvTable }> => {
    if (!project || !service || !current(ticket) || draft.current.revision !== revision || blocked.current) throw new Error("表格草稿或归属已变化");
    await check(ticket);
    const fresh = (await typedIPC.getAttachment(project, row.id)).attachment;
    if (!current(ticket) || draft.current.revision !== revision || blocked.current || fresh.project_id !== project || fresh.service_id !== service ||
        fresh.id !== row.id || fresh.sha256 !== row.sha256 || fresh.original_name !== row.original_name || fresh.version !== row.version ||
        fresh.size_bytes !== row.size_bytes || fresh.declared_type !== "csv" || fresh.recognized_type !== "utf8_text" || fresh.size_bytes > 1048576)
      throw new Error("附件身份或表格草稿已变化");
    abort.current?.abort(); const controller = new AbortController(); abort.current = controller;
    const blob = await fetchAttachmentContent(fresh, host, controller.signal);
    const text = new TextDecoder("utf-8", { fatal: true }).decode(await blob.arrayBuffer()), table = parseOfficeCsv(text);
    await check(ticket);
    if (!current(ticket) || draft.current.revision !== revision || blocked.current) throw new Error("表格草稿或归属已变化");
    return { text, table };
  };
  const prepare = async (row: AttachmentOriginal) => {
    if (busy || disabled) return;
    const ticket = ++epoch.current, revision = draft.current.revision; setBusy(true); setNotice(""); setCandidate(null);
    try { const value = await read(row, ticket, revision); if (current(ticket)) setCandidate({ row, ...value, revision }); }
    catch (error) { if (ticket === epoch.current) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const apply = async () => {
    if (!candidate || busy || disabled) return;
    const ticket = ++epoch.current; setBusy(true); setNotice("");
    try {
      const value = await read(candidate.row, ticket, candidate.revision);
      if (value.text !== candidate.text) throw new Error("原件内容已变化，请重新准备");
      if (!current(ticket) || draft.current.revision !== candidate.revision || blocked.current) throw new Error("表格草稿或归属已变化");
      callback.current(value.text, value.table); setCandidate(null);
      setNotice("已替换 CSV 和表格草稿；展开下方 CSV 编辑区并转换后才能生成新成果。原附件未修改。");
    } catch (error) { if (ticket === epoch.current) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <details className="space-y-2 rounded border border-line p-3"><summary>从项目 CSV 附件继续处理</summary>
    <p>从已登记附件中读取完整 UTF-8 CSV 原件，首行为表头；最多 1 MiB、1000 数据行、20 列和 5000 格。默认列类型为文本，不执行公式；列名需唯一才能转换生成。原件不修改；内容和处理口径需核对。名称搜索区分大小写，每次加载一页附件后筛选 CSV。</p>
    <div className="flex gap-2"><Input aria-label="搜索项目 CSV 附件" value={query} maxLength={240} onChange={(event) => setQuery(event.target.value)} placeholder="按附件名称搜索" />
      <Button type="button" variant="secondary" disabled={busy || disabled || !project || !session || !service || ipc.isMock} onClick={() => void list(false)}>查找项目附件</Button></div>
    {rows.map((row) => <div key={row.id} className="break-all rounded border border-line p-2">{row.original_name} · v{row.version} · {row.id} · {row.size_bytes} 字节
      <Button type="button" size="sm" variant="secondary" disabled={busy || disabled} onClick={() => void prepare(row)}>读取并预览</Button></div>)}
    {loaded && !rows.length && <p>{cursor ? "当前加载页没有受支持的 CSV，可继续加载更早附件。" : "没有匹配的受支持 CSV 附件。"}</p>}
    {cursor && <Button type="button" variant="secondary" disabled={busy || disabled} onClick={() => void list(true)}>加载更早附件</Button>}
    {candidate && <div className="space-y-2 rounded border border-line p-2"><p className="break-all">已读取完整原件：{candidate.row.original_name} · v{candidate.row.version} · {candidate.row.id} · SHA-256 {candidate.row.sha256}。共 {candidate.table.rows.length} 数据行；下方预览表头和前 5 行。</p>
      <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-words">{[candidate.table.headers, ...candidate.table.rows.slice(0, 5)].map((row) => JSON.stringify(row)).join("\n")}</pre>
      <p>确认会替换当前 CSV 和表格编辑草稿；已转换 JSON、文件名、运行关联、汇总配置和处理说明保持不变。请编辑后重新转换，并自行更新处理说明。</p>
      <Button type="button" variant="secondary" disabled={busy || disabled} onClick={() => void apply()}>确认替换表格草稿</Button>
      <Button type="button" variant="ghost" disabled={busy} onClick={() => setCandidate(null)}>取消预览</Button></div>}
    {busy && <p role="status">正在读取并核对…</p>}{notice && <p role="status">{notice}</p>}
  </details>;
}
