import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import type { AttachmentCsvPreview as CsvPreview, AttachmentOriginal, CsvDelimiter } from "../../types/ipc";
import { Button } from "../../ui";

export function AttachmentCsvPreview({ attachment, project, session, service, host }: {
  attachment: AttachmentOriginal; project: string | null; session: string | null; service: string | null; host: string;
}): JSX.Element | null {
  const [delimiter, setDelimiter] = useState<CsvDelimiter>("comma");
  const [result, setResult] = useState<CsvPreview | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0);
  useEffect(() => {
    ++epoch.current; setResult(null); setBusy(false); setError(""); setDelimiter("comma");
    return () => { ++epoch.current; };
  }, [project, session, service, host, attachment.id, attachment.sha256]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setResult(null); setBusy(false); setError("连接已变化，请重新读取。"); }), []);
  if (attachment.declared_type !== "csv" || attachment.recognized_type !== "utf8_text") return null;
  const read = async () => {
    if (!project || !session || !service || ipc.isMock) return;
    const ticket = ++epoch.current, id = attachment.id, sha = attachment.sha256, chosen = delimiter;
    const current = () => {
      const sessions = useSessionStore.getState(), attachments = useAttachmentStore.getState();
      return ticket === epoch.current && ipc.agentBaseUrl === host && sessions.currentProjectId === project &&
        sessions.currentSessionId === session && attachments.projectId === project && attachments.host === host &&
        attachments.serviceId === service && attachments.selected?.id === id && attachments.selected.sha256 === sha;
    };
    setBusy(true); setError(""); setResult(null);
    try {
      const response = await typedIPC.previewAttachmentCsv({ project_id: project, session_id: session,
        expected_service_id: service, attachment_id: id, expected_sha256: sha, delimiter: chosen });
      if (!current()) return;
      if (response.project_id !== project || response.session_id !== session || response.service_id !== service ||
          response.attachment_id !== id || response.sha256 !== sha || response.delimiter !== chosen)
        throw new Error("预览身份或分隔符不匹配");
      setResult(response);
    } catch (cause) { if (current()) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (current()) setBusy(false); }
  };
  return <section className="space-y-2 rounded border border-line p-3"><h3>CSV 表格预览</h3>
    <p>明确选择分隔符，不自动猜测；最多 1 MiB、1000 数据行、100 列及 5000 格。</p><p>仅显示最多 100 行和 64 KiB 的表格内容，不执行公式或修改原件。</p>
    <label>预览分隔符 <select aria-label="CSV 预览分隔符" value={delimiter} onChange={(event) => {
      ++epoch.current; setDelimiter(event.target.value as CsvDelimiter); setResult(null); setBusy(false); setError("");
    }}><option value="comma">逗号</option><option value="semicolon">分号</option><option value="tab">制表符</option></select></label>{" "}
    <Button type="button" variant="secondary" disabled={busy || !project || !session || !service || ipc.isMock} onClick={() => void read()}>读取 CSV 预览</Button>
    {busy && <p role="status">正在解析所选分隔符…</p>}{error && <p role="alert">{error}</p>}
    {result && <div><p>解析格式：{result.delimiter}；共 {result.total_rows} 数据行{result.truncated ? "，预览已截断" : ""}。内容与业务结果需自行核对。</p>
      <div className="max-h-64 overflow-auto"><table className="min-w-full border-collapse text-left text-xs"><thead><tr>{result.headers.map((cell, i) => <th className="border border-line p-1" key={i}>{cell}</th>)}</tr></thead><tbody>{result.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td className="whitespace-pre-wrap break-words border border-line p-1" key={j}>{cell}</td>)}</tr>)}</tbody></table></div></div>}
  </section>;
}
