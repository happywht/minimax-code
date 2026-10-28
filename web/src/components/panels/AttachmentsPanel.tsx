import { useEffect, useState } from "react";
import { ipc } from "../../ipc";
import { useAttachmentStore, useSessionStore } from "../../stores";
import { Button } from "../../ui";
import type { AttachmentParserStatus } from "../../types/ipc";

const labels: Record<AttachmentParserStatus, string> = {
  parsed: "已解析", empty: "空文本", truncated: "预览已截断", parse_failed: "解析失败",
  type_mismatch: "类型不匹配", parser_unavailable: "解析器不可用", ocr_not_attempted: "未尝试 OCR",
};
export function AttachmentsPanel({ onClose }: { onClose: () => void }): JSX.Element {
  const projectId = useSessionStore((state) => state.currentProjectId);
  const project = useSessionStore((state) => state.projects.find((item) => item.id === projectId));
  const state = useAttachmentStore(), host = ipc.agentBaseUrl;
  const scoped = state.projectId === projectId && state.host === host;
  const [file, setFile] = useState<File | null>(null);
  useEffect(() => {
    setFile(null); void useAttachmentStore.getState().activate(projectId);
    return () => useAttachmentStore.getState().clear();
  }, [projectId, host]);
  return <section className="flex h-full flex-col gap-3 overflow-auto p-4" aria-label="项目附件">
    <div className="flex items-center justify-between">
      <h2 className="text-lg font-semibold">项目附件 · {project?.name ?? "选择项目"}</h2>
      <Button variant="ghost" onClick={onClose}>关闭</Button>
    </div>
    <p className="text-sm text-ink-1">最大 10 MiB。每次上传保留独立原件；同名文件创建新版本。</p>
    <p className="text-sm text-ink-1">TXT / CSV 严格 UTF-8 预览，最多 64 KiB / 2000 行；CSV 公式只作文本。可选 DOCX 解析仅包含正文段落和表格，保留位置标签；不含页眉页脚、修订、文本框、批注、脚注或图片文字。可选 PDF 解析按物理页序提取已有文字，最多 200 页，不保证版式或表格结构；扫描图片未尝试 OCR。XLSX 暂无解析器。</p>
    <p className="text-sm text-ink-1">原件保存、可解析、质量验证与交付是不同状态；当前质量均为未验证。附件不会自动加入对话上下文。</p>
    {ipc.isMock && <p role="status">演示模式：原件仅保存在本页内存，刷新后清空；Office 容器只标记候选 ZIP。PDF / DOCX 文字解析需要真实后端可选依赖。</p>}
    <form className="flex flex-wrap items-center gap-2" onSubmit={(event) => { event.preventDefault(); if (file) void state.upload(file); }}>
      <input aria-label="选择附件原件" type="file" disabled={state.busy} onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
      <Button type="submit" disabled={!scoped || !file || file.size > 10485760 || !state.serviceId || state.busy || state.loading}>上传原件</Button>
      <Button type="button" variant="secondary" disabled={state.busy} onClick={() => void state.activate(projectId)}>刷新</Button>
    </form>
    {file && file.size > 10485760 && <p role="alert">文件超过 10 MiB，未上传。</p>}
    {scoped && state.error && <p role="alert" className="text-status-error">{state.error}</p>}
    {scoped && state.loading && <p role="status">正在读取附件…</p>}
    {scoped && !state.loading && !state.attachments.length && <p>当前项目还没有附件原件。</p>}
    <ul className="space-y-1">{(scoped ? state.attachments : []).map((row) => <li key={row.id}>
      <button disabled={state.busy} className="max-w-full break-all rounded border border-line p-2 text-left hover:bg-surface-3" onClick={() => void state.select(row.id)}>
        {row.original_name} · v{row.version} · {row.size_bytes} 字节 · {labels[row.parser_status]} / 未验证
      </button>
    </li>)}</ul>
    {scoped && state.selected && <div className="space-y-2">
      <p className="break-all text-xs text-ink-1">声明类型: {state.selected.declared_type} · 识别候选: {state.selected.recognized_type} · {labels[state.selected.parser_status]} · 未验证</p>
      <p className="break-all text-xs text-ink-1">解析算法 v{state.selected.parser_version} · 范围 {state.selected.parser_scope} · 原因 {state.selected.parser_reason}</p>
      {state.selected.parser_reason === "no_extracted_body_text_no_ocr" && <p role="status">{state.selected.recognized_type === "pdf" ? "未提取到 PDF 页面文本；未尝试 OCR。原件可能仍包含其他内容。" : "未提取到正文文本；未尝试 OCR。原件可能仍包含其他内容。"}</p>}
      {state.selected.recognized_type === "pdf" && <p role="status">预览只包含已有文字；页码为物理页序号，空页标记不代表文件没有内容，未尝试 OCR。</p>}
      {state.selected.preview_truncated && <p role="status">预览已达到 64 KiB / 2000 行展示上限，后续内容未显示；可下载完整原件。</p>}
      <p className="break-all text-xs text-ink-1">SHA-256: {state.selected.sha256}</p>
      <Button disabled={state.busy} onClick={() => void state.download()}>下载附件原始字节</Button>
      {["parsed", "empty", "truncated"].includes(state.selected.parser_status)
        ? <pre className="whitespace-pre-wrap break-words rounded border border-line bg-surface-1 p-3" aria-label="附件纯文本预览">{state.preview}</pre>
        : <p role="status">当前状态不提供文本预览；可下载保存的原件。</p>}
    </div>}
  </section>;
}
