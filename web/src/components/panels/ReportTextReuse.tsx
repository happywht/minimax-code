import { useEffect, useRef, useState } from "react";
import { ipc, typedIPC } from "../../ipc";
import { fetchAttachmentContent } from "../../ipc/attachmentContent";
import { useAttachmentStore, useSessionStore } from "../../stores";
import type { AttachmentOriginal } from "../../types/ipc";
import { Button } from "../../ui";

export function ReportTextReuse({ attachment, project, session, service, host, draftVersion, onApply, onAppend }: {
  attachment: AttachmentOriginal; project: string | null; session: string | null;
  service: string | null; host: string; draftVersion: string; onApply: (text: string) => void; onAppend: (text: string) => boolean;
}): JSX.Element | null {
  const [candidate, setCandidate] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0), draft = useRef(draftVersion), preparedDraft = useRef(""); draft.current = draftVersion;
  useEffect(() => {
    ++epoch.current; setCandidate(null); setNotice(""); setBusy(false);
    const off = ipc.onSideCar(() => { ++epoch.current; setCandidate(null); setBusy(false); setNotice("连接变化，请重新准备。"); });
    return () => { ++epoch.current; off(); };
  }, [attachment.id, attachment.sha256, project, session, service, host]);
  if (attachment.declared_type !== "txt" || attachment.recognized_type !== "utf8_text") return null;

  const current = () => {
    const s = useSessionStore.getState(), a = useAttachmentStore.getState();
    return ipc.agentBaseUrl === host && s.currentProjectId === project && s.currentSessionId === session &&
      a.projectId === project && a.host === host && a.serviceId === service &&
      a.selected?.id === attachment.id && a.selected.sha256 === attachment.sha256;
  };
  const prepare = async () => {
    if (!project || !service || !current()) return;
    const ticket = ++epoch.current, expectedDraft = draft.current;
    const unchanged = () => ticket === epoch.current && current() && draft.current === expectedDraft;
    setBusy(true); setCandidate(null); setNotice("");
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!unchanged() || identity.service_id !== service) throw new Error("上下文或报告草稿已变化，请重新准备");
      const blob = await fetchAttachmentContent(attachment, host, new AbortController().signal);
      if (!unchanged()) throw new Error("上下文或报告草稿已变化，请重新准备");
      const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(await blob.arrayBuffer());
      if (!unchanged()) throw new Error("上下文或报告草稿已变化，请重新准备");
      const normalized = text.replace(/\r\n/g, "\n");
      const paragraphs = normalized.split("\n");
      // eslint-disable-next-line no-control-regex
      if (!normalized.trim() || normalized.startsWith("\ufeff") || /[\x00-\x08\x0b-\x1f\ufffe\uffff]/.test(normalized) ||
          paragraphs.length > 100 || paragraphs.some((line) => Array.from(line).length > 4096) ||
          new TextEncoder().encode(normalized).length > 256 * 1024) {
        throw new Error("文本为空或包含不支持的字符，或超过 100 段、每段 4096 字或总量 256 KiB；原草稿未更改");
      }
      preparedDraft.current = expectedDraft; setCandidate(normalized);
      setNotice("已读取并校验完整原件。每行转换为一个段落，CRLF 换行转换为段落边界；请核对预览。标题、来源与原有章节不会从文本中推断。");
    } catch (error) { if (ticket === epoch.current && current()) setNotice(`无法准备文本：${error instanceof Error ? error.message : String(error)}`); }
    finally { if (ticket === epoch.current) setBusy(false); }
  };
  return <div className="space-y-2 rounded border border-line p-2">
    <p>将完整 UTF-8 TXT 作为报告正文复用，或附带附件身份追加到普通正文或选定章节。原件保持不变；</p><p>文本按行转换为段落，追加时跳过空行，报告使用新排版。</p>
    <Button type="button" variant="secondary" disabled={busy || !project || !service} onClick={() => void prepare()}>{busy ? "正在读取原件…" : "准备复用完整文本"}</Button>
    {notice && <p role="status">{notice}</p>}
    {candidate !== null && <><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words">{candidate}</pre>
      <Button type="button" variant="secondary" onClick={() => {
        if (!current() || draft.current !== preparedDraft.current) { setCandidate(null); setNotice("上下文或报告草稿已变化，请重新准备。"); return; }
        if (!window.confirm("替换当前普通正文？请先保存需要保留的草稿。")) return;
        if (!current() || draft.current !== preparedDraft.current) { setCandidate(null); setNotice("上下文或报告草稿已变化，请重新准备。"); return; }
        onApply(candidate); setCandidate(null); setNotice("文本已替换普通正文并切换至普通段落；请核对标题、来源和引用后生成新文件。");
      }}>替换普通正文并继续编辑</Button>
      <Button type="button" variant="secondary" onClick={() => {
        if (!current() || draft.current !== preparedDraft.current) { setCandidate(null); setNotice("上下文或报告草稿已变化，请重新准备。"); return; }
        if (!onAppend(candidate)) { setNotice("未追加；请检查目标章节和报告长度限制。"); return; }
        setCandidate(null); setNotice("已追加文本和附件身份；请核对原件、引用与事实，尚未生成文件。");
      }}>追加文本到报告</Button>
      <Button type="button" variant="ghost" onClick={() => { setCandidate(null); setNotice("已取消，草稿未更改。"); }}>取消</Button></>}
  </div>;
}
