import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import { useOfficeDeliveryStore } from "../../stores/officeDeliveryStore";
import type { OfficeGetResult } from "../../types/ipc";
import { Button } from "../../ui";

/** Recheck the selected delivery before adding its reference to a chat draft. */
export function OfficeFollowupDraft({ selected }: { selected: OfficeGetResult }): JSX.Element {
  const project = useSessionStore((state) => state.currentProjectId);
  const session = useSessionStore((state) => state.currentSessionId);
  const host = ipc.agentBaseUrl;
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [added, setAdded] = useState(false);
  const epoch = useRef(0);

  useEffect(() => {
    ++epoch.current; setBusy(false); setNotice(""); setAdded(false);
    return () => { ++epoch.current; };
  }, [project, session, host, selected.delivery.id, selected.attachment.sha256]);
  useEffect(() => ipc.onSideCar(() => {
    ++epoch.current; setBusy(false); setAdded(false); setNotice("连接已变化，请重新选择表格。");
  }), []);

  const add = async () => {
    if (!project || !session || busy) return;
    const ticket = ++epoch.current;
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
    setBusy(true); setNotice(""); setAdded(false);
    try {
      const [identity, sessionResult, result] = await Promise.all([
        typedIPC.getServiceIdentity(), typedIPC.getSession(session), typedIPC.getOfficeDelivery(project, delivery.id),
      ]);
      if (!current()) return;
      const row = result.attachment, record = result.delivery, report = record.validation_report;
      if (identity.service_id !== delivery.service_id || sessionResult.session.id !== session ||
          sessionResult.session.project_id !== project || record.id !== delivery.id || record.project_id !== project ||
          record.service_id !== delivery.service_id || record.attachment_id !== attachment.id ||
          row.id !== attachment.id || row.project_id !== project || row.service_id !== delivery.service_id ||
          row.sha256 !== attachment.sha256 || row.version !== attachment.version || row.original_name !== attachment.original_name ||
          report.file_sha256 !== row.sha256 || report.size_bytes !== row.size_bytes ||
          report.input_sha256 !== record.input_sha256 || report.scope !== "xlsx_plain_cells_v1" ||
          report.business_quality !== "unverified" || report.render_status !== "not_attempted") {
        throw new Error("表格、会话或服务归属已变化，请刷新并重新选择。");
      }
      const text = ["办公表格成果引用（仅元数据加入草稿，尚未发送；业务内容未验证）：",
        `文件名：${row.original_name} · 版本：${row.version}`, `附件 ID：${row.id}`,
        `成果 ID：${record.id}`, `SHA-256：${row.sha256}`, `来源运行：${record.run_id ?? "未关联"}`,
        `结构校验范围：${report.scope} · 单元格数（含表头）：${report.cell_count}`,
        "此引用不包含原件或单元格内容；如需分析，请使用当前环境支持的附件读取工具，或提供相关数据。"].join("\n");
      if (!current()) return;
      const detail: { host: string; project: string; session: string; text: string; result?: string } = { host, project, session, text };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      setNotice(detail.result ?? "当前聊天输入区未打开；请先切换到聊天，再重新选择表格。");
      setAdded(detail.result === "已添加到当前会话草稿，尚未发送。");
    } catch (error) {
      if (current()) setNotice(`未添加表格：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      stopSession(); stopOffice(); stopConnection();
      if (ticket === epoch.current) setBusy(false);
    }
  };

  return <div className="space-y-2">
    <div className="flex flex-wrap gap-2">
      <Button type="button" variant="secondary" disabled={!project || !session || busy} loading={busy} onClick={() => void add()}>加入当前会话草稿</Button>
      {added && <Button type="button" variant="secondary" onClick={() => window.dispatchEvent(new Event("minimax:open-chat"))}>打开聊天</Button>}
    </div>
    <p className="text-xs text-ink-2">只追加已核对的表格身份和校验范围，不发送，也不包含文件内容。</p>
    {notice && <p role="status" className="text-xs text-ink-1">{notice}</p>}
  </div>;
}
