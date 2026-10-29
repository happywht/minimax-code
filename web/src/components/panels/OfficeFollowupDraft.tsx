import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import type { OfficeGetResult } from "../../types/ipc";
import { Button } from "../../ui";
import { parseXlsxProjection } from "./XlsxTablePreview";

/** Recheck the selected delivery before adding its reference to a chat draft. */
export function OfficeFollowupDraft({ selected }: { selected: OfficeGetResult }): JSX.Element {
  const project = useSessionStore((state) => state.currentProjectId);
  const session = useSessionStore((state) => state.currentSessionId);
  const host = ipc.agentBaseUrl;
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [added, setAdded] = useState(false);
  const [prepared, setPrepared] = useState<string | null>(null);
  const epoch = useRef(0);

  useEffect(() => {
    ++epoch.current; setBusy(false); setNotice(""); setAdded(false); setPrepared(null);
    return () => { ++epoch.current; };
  }, [project, session, host, selected.delivery.id, selected.delivery.service_id, selected.attachment.id, selected.attachment.sha256]);
  useEffect(() => ipc.onSideCar(() => {
    ++epoch.current; setBusy(false); setAdded(false); setPrepared(null); setNotice("连接已变化，请重新选择表格。");
  }), []);

  const read = async (ticket: number): Promise<string> => {
    if (!project || !session) throw new Error("请先选择项目和会话。");
    const { delivery, attachment } = selected;
    let changed = false;
    const stopSession = useSessionStore.subscribe((state) => {
      if (state.currentProjectId !== project || state.currentSessionId !== session) changed = true;
    });
    const stopOffice = useOfficeDeliveryStore.subscribe((state) => {
      if (state.projectId !== project || state.host !== host || state.serviceId !== delivery.service_id ||
          state.selected?.delivery.id !== delivery.id || state.selected.attachment.sha256 !== attachment.sha256) changed = true;
    });
    const stopConnection = ipc.onSideCar(() => { changed = true; });
    const current = () => {
      const scope = useSessionStore.getState(), office = useOfficeDeliveryStore.getState();
      return !changed && ticket === epoch.current && ipc.agentBaseUrl === host &&
        scope.currentProjectId === project && scope.currentSessionId === session &&
        office.projectId === project && office.host === host && office.serviceId === delivery.service_id &&
        office.selected?.delivery.id === delivery.id && office.selected.attachment.sha256 === attachment.sha256;
    };
    try {
      const [identity, sessionResult, result] = await Promise.all([
        typedIPC.getServiceIdentity(), typedIPC.getSession(session), typedIPC.getOfficeDelivery(project, delivery.id),
      ]);
      const row = result.attachment, record = result.delivery, report = record.validation_report;
      const [afterIdentity, afterSession] = await Promise.all([typedIPC.getServiceIdentity(), typedIPC.getSession(session)]);
      if (!current() || identity.service_id !== delivery.service_id || afterIdentity.service_id !== delivery.service_id ||
          afterSession.session.id !== session || afterSession.session.project_id !== project || sessionResult.session.id !== session ||
          sessionResult.session.project_id !== project || record.id !== delivery.id || record.project_id !== project ||
          record.service_id !== delivery.service_id || record.attachment_id !== attachment.id ||
          record.run_id !== delivery.run_id || record.input_sha256 !== delivery.input_sha256 ||
          row.id !== attachment.id || row.project_id !== project || row.service_id !== delivery.service_id ||
          row.sha256 !== attachment.sha256 || row.snapshot_id !== attachment.snapshot_id || row.size_bytes !== attachment.size_bytes ||
          row.version !== attachment.version || row.original_name !== attachment.original_name ||
          report.file_sha256 !== row.sha256 || report.size_bytes !== row.size_bytes ||
          report.input_sha256 !== record.input_sha256 || report.scope !== "xlsx_plain_cells_v1" ||
          report.business_quality !== "unverified" || report.render_status !== "not_attempted") {
        throw new Error("表格、会话或服务归属已变化，请刷新并重新选择。");
      }
      if (typeof result.preview_text !== "string" || row.parser_version !== 2 || row.parser_scope !== "inert_text" ||
          row.declared_type !== "xlsx" || row.recognized_type !== "xlsx" ||
          !["parsed", "truncated", "empty"].includes(row.parser_status)) throw new Error("表格预览不可用，请下载原件查看。");
      const sheets = parseXlsxProjection(result.preview_text);
      if (!sheets.length && row.parser_status !== "empty") throw new Error("表格预览缺少工作表。");
      const shorten = (value: string, limit: number) => {
        const chars = Array.from(value);
        return chars.length > limit ? `${chars.slice(0, limit).join("")}（截断）` : value;
      };
      const records: string[] = [];
      for (const sheet of sheets) {
        for (const cell of sheet.cells.values()) {
          const value = cell.kind === "formula"
            ? `公式=${cell.formula}；缓存（未核验）=${cell.cacheState === "missing" ? "缺失" : cell.cacheState === "unknown" ? "不确定" : cell.cachedRaw}`
            : `${cell.kind}=${cell.raw}`;
          records.push(`${JSON.stringify(shorten(sheet.name, 80))} [${sheet.state}] ${cell.coordinate}: ${JSON.stringify(shorten(value, 240))}`);
        }
      }
      if (!records.length) throw new Error("预览中没有可引用的单元格，请下载原件查看。");
      const excerpt = Array.from(records.join("\n"));
      return ["办公表格成果摘录（尚未发送，内容及业务结果未验证）：",
        `文件名：${row.original_name} · 版本：${row.version}`, `附件 ID：${row.id}`,
        `成果 ID：${record.id} · SHA-256：${row.sha256}`, `来源运行：${record.run_id ?? "未关联"}`,
        `结构校验范围：${report.scope} · 单元格数（含表头）：${report.cell_count}`,
        "以下是解析预览中的存储记录，最多 1200 字；长表名和单元格会截断，未展示区域不能推断为空。数字为原值，公式不执行，缓存可能过期。",
        row.preview_truncated ? "后端预览已截断。" : "后端预览仍受范围和数量限制。",
        excerpt.slice(0, 1200).join("") + (excerpt.length > 1200 ? "\n（摘录截断）" : "")].join("\n");
    } finally { stopSession(); stopOffice(); stopConnection(); }
  };

  const prepare = async () => {
    if (busy) return;
    const ticket = ++epoch.current; setBusy(true); setNotice(""); setAdded(false); setPrepared(null);
    try { const text = await read(ticket); if (ticket === epoch.current) setPrepared(text); }
    catch (error) { if (ticket === epoch.current) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  const add = async () => {
    if (!prepared || !project || !session || busy) return;
    const expected = prepared, ticket = ++epoch.current; setBusy(true); setNotice("");
    try {
      const text = await read(ticket);
      if (ticket !== epoch.current) return;
      if (text !== expected) { setPrepared(null); throw new Error("表格内容或身份已变化，请重新准备。"); }
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project, session, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setNotice(detail.result ?? "当前聊天输入区未打开；请先切换到聊天，再重新准备。");
      setAdded(detail.result === "已添加到当前会话草稿，尚未发送。");
      if (detail.result === "已添加到当前会话草稿，尚未发送。") setPrepared(null);
    } catch (error) { if (ticket === epoch.current) setNotice(error instanceof Error ? error.message : String(error)); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };

  return <div className="space-y-2">
    <Button type="button" variant="secondary" disabled={!project || !session || busy} loading={busy} onClick={() => void prepare()}>准备表格摘录草稿</Button>
    <p className="text-xs text-ink-2">读取有界单元格预览，核对后追加到聊天草稿；不会发送或执行。</p>
    {prepared && <div className="space-y-2 rounded border border-line p-2"><p>请核对摘录；追加前将重新读取。</p>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded bg-surface-1 p-2">{prepared}</pre>
      <div className="flex gap-2"><Button type="button" disabled={busy} onClick={() => void add()}>追加到当前会话草稿</Button>
        <Button type="button" variant="ghost" disabled={busy} onClick={() => setPrepared(null)}>取消</Button></div></div>}
    {added && <Button type="button" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}
    {notice && <p role="status" className="text-xs text-ink-1">{notice}</p>}
  </div>;
}
