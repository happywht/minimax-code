import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import { Button } from "../../ui";
import type { AttachmentOriginal, AttachmentOcrParams, AttachmentOcrResult } from "../../types/ipc";

export function AttachmentImageOcr({ attachment, project, session, service, host }: {
  attachment: AttachmentOriginal; project: string | null; session: string | null; service: string | null; host: string;
}): JSX.Element | null {
  const [language, setLanguage] = useState<AttachmentOcrParams["language"]>("eng+chi_sim");
  const [result, setResult] = useState<AttachmentOcrResult | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0);
  useEffect(() => { ++epoch.current; setResult(null); setError(""); setBusy(false); return () => { ++epoch.current; }; },
    [project, session, service, host, attachment.id, attachment.sha256]);
  useEffect(() => ipc.onSideCar(() => { ++epoch.current; setResult(null); setBusy(false); setError("连接变化，请重新选择附件。"); }), []);
  const eligible = ["png", "jpeg"].includes(attachment.recognized_type) && attachment.declared_type === attachment.recognized_type;
  if (!eligible) return null;
  const run = async () => {
    if (!project || !session || !service || busy || ipc.isMock) return;
    const ticket = ++epoch.current, id = attachment.id, sha = attachment.sha256, selectedLanguage = language;
    const current = () => {
      const sessions = useSessionStore.getState(), attachments = useAttachmentStore.getState();
      return ticket === epoch.current && ipc.agentBaseUrl === host && sessions.currentProjectId === project &&
        sessions.currentSessionId === session && attachments.projectId === project && attachments.host === host &&
        attachments.serviceId === service && attachments.selected?.id === id && attachments.selected.sha256 === sha;
    };
    setBusy(true); setError(""); setResult(null);
    try {
      const value = await typedIPC.ocrAttachmentImage({ project_id: project, session_id: session, expected_service_id: service,
        attachment_id: id, expected_sha256: sha, language: selectedLanguage });
      if (!current()) return;
      if (value.ok !== true || value.project_id !== project || value.session_id !== session || value.service_id !== service ||
          value.attachment_id !== id || value.sha256 !== sha || value.language !== selectedLanguage || value.quality !== "unverified" ||
          typeof value.text !== "string" || value.text.length > 32000 || typeof value.truncated !== "boolean")
        throw new Error("返回的附件身份或识别结果不匹配，请重新选择。");
      setResult(value);
    } catch (e) { if (current()) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current()) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-3" aria-label="本地图片 OCR">
    <h4 className="font-medium">手动本地图片 OCR</h4>
    <p className="text-xs text-ink-1">仅静态 PNG/JPEG，最多 1200 万像素；结果可能有错漏，未经核验。识别结果仅临时显示，不自动加入对话或修改原件。需要本机安装 Pillow、tesserocr、Tesseract 和对应语言数据；不会自动下载。</p>
    <label>识别语言 <select aria-label="OCR 识别语言" value={language} disabled={busy} onChange={(e) => { setLanguage(e.target.value as AttachmentOcrParams["language"]); setResult(null); }}>
      <option value="eng+chi_sim">简体中文 + 英文</option><option value="chi_sim">简体中文</option><option value="eng">英文</option>
    </select></label>{" "}
    <Button type="button" variant="secondary" disabled={!project || !session || !service || busy || ipc.isMock} onClick={() => void run()}>开始本地识别</Button>
    {ipc.isMock && <p role="status">演示模式不支持 OCR。</p>}
    {busy && <p role="status">正在本地识别，最多等待 15 秒（排队另计）…</p>}
    {error && <p role="alert">OCR 失败：{error}</p>}
    {result && <div><p>识别结果 · {result.language} · 未核验{result.truncated ? " · 已截断" : ""}</p>
      <pre className="whitespace-pre-wrap break-words rounded border border-line bg-surface-1 p-3">{result.text || "（未识别到文字，不代表图片没有文字）"}</pre></div>}
  </div>;
}
