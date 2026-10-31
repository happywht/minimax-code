/**
 * MessageInput — the bottom chat composer.
 *
 * This is a thin composition layer: draft state lives in
 * ``useComposerDraft``, attachments in ``useAttachments``, voice
 * dictation in ``useVoiceInput``, and the `@agent` autocomplete in
 * ``useMentionPicker``. Presentational pieces are split into
 * ``AttachmentRows``, ``MentionPickerDropdown`` and ``ComposerToolbar``.
 */
import { Mic, MicOff, Paperclip, Send, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { useChat, useSessionStore } from "../../stores";
import { Button, DropdownMenu, IconButton } from "../../ui";
import type { AgentInfo, ContentPart } from "../../types/ipc";
import { ACCEPTED_EXTS, MAX_INPUT_CHARS } from "./constants";
import { useComposerDraft } from "./useComposerDraft";
import { useAttachments } from "./useAttachments";
import { useVoiceInput } from "./useVoiceInput";
import { useMentionPicker } from "./useMentionPicker";
import { useMentionContext } from "./useMentionContext";
import { AttachmentRows } from "./AttachmentRows";
import { MentionPickerDropdown } from "./MentionPickerDropdown";
import { ComposerSafetyControls, ComposerToolbar } from "./ComposerToolbar";
import { useQueuedTask } from "./useQueuedTask";
import { extractMentionContext } from "../../lib/mentions";
import { ProjectAttachmentImport } from "./ProjectAttachmentImport";
import { strings } from "../../ui/strings";
import { CODEX_IMAGE_HANDOFF, type CodexImageHandoff } from "../../lib/codexImageHandoff";

function serviceLabel(value: string): string {
  try { return new URL(value).origin; } catch { return "未知"; }
}

function describeQueuedContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "原请求内容不可读，请核对时间线。";
  return content.map((part: unknown) => {
    if (!part || typeof part !== "object") return "[无法识别的内容]";
    const value = part as { type?: unknown; text?: unknown; media_type?: unknown };
    if (value.type === "text" && typeof value.text === "string") return value.text;
    if (value.type === "image" && typeof value.media_type === "string") return `[图片 ${value.media_type}]`;
    return "[无法识别的内容]";
  }).join("\n");
}

function readNetworkRestriction(key: string): boolean {
  try { return window.localStorage.getItem(key) === "true"; } catch { return false; }
}

export interface MessageInputProps {
  testId?: string;
  /** Override the agent list fetcher (for tests). */
  loadAgents?: () => Promise<AgentInfo[]>;
}

export function MessageInput({
  testId = "message-input",
  loadAgents,
}: MessageInputProps): JSX.Element {
  const status = useChat((s) => s.status);
  const send = useChat((s) => s.send);
  const cancel = useChat((s) => s.cancel);
  const executionMode = useChat((s) => s.executionMode);
  const setExecutionMode = useChat((s) => s.setExecutionMode);
  const captureFileChanges = useChat((s) => s.captureFileChanges);
  const setCaptureFileChanges = useChat((s) => s.setCaptureFileChanges);
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const session = useSessionStore((s) => s.sessions.find((item) => item.id === s.currentSessionId));
  const selectedProjectId = useSessionStore((s) => s.currentProjectId);
  const projectId = sessionId ? session?.project_id : selectedProjectId;
  const project = useSessionStore((s) => s.projects.find((item) => item.id === projectId));
  const [budgetScope, setBudgetScope] = useState(() => ({ host: ipc.agentBaseUrl, session: sessionId, value: "" }));
  const [budgetHost, setBudgetHost] = useState(ipc.agentBaseUrl);
  useEffect(() => ipc.onSideCar(() => {
    const host = ipc.agentBaseUrl; setBudgetHost(host);
    setBudgetScope((previous) => ({ ...previous, host, value: "" }));
  }), []);
  if (budgetScope.host !== budgetHost || budgetScope.session !== sessionId) {
    setBudgetScope({ host: budgetHost, session: sessionId, value: "" });
  }
  const budgetText = budgetScope.host === budgetHost && budgetScope.session === sessionId ? budgetScope.value : "";
  const budgetNumber = budgetText ? Number(budgetText) : undefined;
  const budgetValid = !budgetText || (/^[1-9]\d*$/.test(budgetText) && budgetNumber !== undefined && Number.isSafeInteger(budgetNumber));
  const [projectFileOpen, setProjectFileOpen] = useState(false);
  const optionsRef = useRef<HTMLDetailsElement>(null);
  const [optionsPosition, setOptionsPosition] = useState({ left: 12, bottom: 80, width: 320, maxHeight: 240 });
  const placeOptions = () => {
    const anchor = optionsRef.current?.querySelector("summary")?.getBoundingClientRect();
    if (!anchor) return;
    const width = Math.min(360, Math.max(0, window.innerWidth - 24));
    setOptionsPosition({ left: Math.max(12, Math.min(anchor.right - width, window.innerWidth - width - 12)),
      bottom: Math.max(12, window.innerHeight - anchor.top + 8), width,
      maxHeight: Math.max(80, Math.min(window.innerHeight * 0.55, anchor.top - 16)) });
  };
  useEffect(() => {
    const reposition = () => { if (optionsRef.current?.open) placeOptions(); };
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => { window.removeEventListener("resize", reposition); window.removeEventListener("scroll", reposition, true); };
  }, []);
  useEffect(() => { if (!budgetValid) placeOptions(); }, [budgetValid]);
  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      const options = optionsRef.current;
      if (budgetValid && options?.open && event.target instanceof Node && !options.contains(event.target)) options.open = false;
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [budgetValid]);

  const codex = useSessionStore((s) => sessionId
    ? s.sessions.find((session) => session.id === sessionId)?.engine === "codex"
    : s.newSessionEngine === "codex");
  const restrictionKey = `minimax-code:network-restriction:${budgetHost}:${sessionId ?? "new"}`;
  const [restriction, setRestriction] = useState(() => ({ key: restrictionKey, value: readNetworkRestriction(restrictionKey) }));
  if (restriction.key !== restrictionKey) setRestriction({ key: restrictionKey, value: readNetworkRestriction(restrictionKey) });
  const restrictNetworkTools = restriction.key === restrictionKey && restriction.value;
  const restrictionRef = useRef(restrictNetworkTools);
  restrictionRef.current = restrictNetworkTools;
  const changeRestriction = (value: boolean) => {
    restrictionRef.current = value;
    setRestriction({ key: restrictionKey, value });
    try { if (value) window.localStorage.setItem(restrictionKey, "true"); else window.localStorage.removeItem(restrictionKey); } catch { /* in-memory only */ }
  };
  const draft = useComposerDraft(sessionId);
  const attachments = useAttachments(draft.ref, draft.setValue);
  const latestAttachments = useRef(attachments);
  latestAttachments.current = attachments;
  const liveDraft = useRef(draft);
  liveDraft.current = draft;
  useEffect(() => {
    const receive = (event: Event) => {
      const detail = (event as CustomEvent<CodexImageHandoff>).detail;
      const state = useSessionStore.getState();
      const current = state.sessions.find((item) => item.id === state.currentSessionId);
      const status = useChat.getState().status;
      if (!detail || ipc.agentBaseUrl !== detail.host || state.currentSessionId !== detail.session ||
          state.currentProjectId !== detail.project || (current?.project_id ?? null) !== detail.project ||
          current?.engine !== "codex" || liveDraft.current.getCurrent().id !== detail.session ||
          status === "sending" || status === "streaming" || status === "cancelling") {
        if (detail) detail.result = "当前服务、会话、项目或任务状态已变化，未加入图片。";
        return;
      }
      detail.result = latestAttachments.current.addSavedImage(detail.image);
    };
    window.addEventListener(CODEX_IMAGE_HANDOFF, receive);
    return () => window.removeEventListener(CODEX_IMAGE_HANDOFF, receive);
  }, []);
  const voice = useVoiceInput(draft.setValue);
  const mention = useMentionPicker({
    loadAgents,
    value: draft.value,
    setValue: draft.setValue,
    textareaRef: draft.ref,
  });
  const context = useMentionContext();
  const queue = useQueuedTask(sessionId);
  const pendingQueue = queue.ready ? queue.pending : null;
  const [queueMessage, setQueueMessage] = useState("");
  const [legacyDraftState, setLegacyDraftState] = useState({ key: "", text: "" });
  const legacyKey = sessionId ? `minimax-code:queued-input:${encodeURIComponent(budgetHost)}:${encodeURIComponent(sessionId)}` : "";
  const legacyQueueDraft = legacyDraftState.key === legacyKey ? legacyDraftState.text : "";
  useEffect(() => {
    setQueueMessage("");
    try { setLegacyDraftState({ key: legacyKey, text: legacyKey ? localStorage.getItem(legacyKey) ?? "" : "" }); }
    catch { setLegacyDraftState({ key: legacyKey, text: "" }); }
  }, [legacyKey]);
  const queueAdmission = useRef<string | null>(null);
  const admissionScope = () => JSON.stringify([ipc.agentBaseUrl, useSessionStore.getState().currentSessionId]);
  const admissionInCurrentScope = () => queueAdmission.current === admissionScope();
  const queueSnapshot = useRef<{ text: string; session: string | null; project: string | null; host: string;
    files: typeof attachments.attachedFiles; images: typeof attachments.attachedImages; config: string } | null>(null);
  useEffect(() => { queueSnapshot.current = null; }, [sessionId, budgetHost]);
  const configValue = JSON.stringify([budgetText, executionMode, restrictNetworkTools, captureFileChanges]);
  const configRef = useRef(configValue); configRef.current = configValue;
  const clearAcceptedQueueDraft = (snapshot: NonNullable<typeof queueSnapshot.current>) => {
    const current = useSessionStore.getState(), live = liveDraft.current.getCurrent();
    if (ipc.agentBaseUrl !== snapshot.host || current.currentSessionId !== snapshot.session || current.currentProjectId !== snapshot.project ||
        live.id !== snapshot.session || live.text !== snapshot.text || configRef.current !== snapshot.config ||
        latestAttachments.current.attachedFiles !== snapshot.files || latestAttachments.current.attachedImages !== snapshot.images) return false;
    liveDraft.current.clear(); latestAttachments.current.clearAll(); mention.closePicker(); changeRestriction(false);
    return true;
  };
  const streaming = status === "streaming" || status === "sending" || status === "cancelling";
  const cancelling = status === "cancelling";
  const disabled = cancelling || context.loading;

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    draft.setValue(next); setQueueMessage("");
    const caret = e.target.selectionStart ?? next.length;
    if (!codex) mention.updateForInput(next, caret);
  };

  const handleSubmit = async (e?: React.FormEvent | React.KeyboardEvent) => {
    e?.preventDefault();
    if (disabled) return;
    if (draft.overLimit || !budgetValid) return;
    if (pendingQueue || queue.busy || (!!sessionId && queue.blocked) || admissionInCurrentScope() || (!!sessionId && !queue.ready)) { setQueueMessage("请先核对待确认的排队请求，当前草稿不会发送。"); return; }
    if (streaming) {
      if (!sessionId) { setQueueMessage("会话尚未确定，请稍后再发送下一任务。"); return; }
      if (codex && restrictNetworkTools) { setQueueMessage("当前联网限制不支持 Codex，请先关闭限制或等待切换会话。"); return; }
      if (!codex) {
        const mentions = extractMentionContext(draft.value);
        if (mentions.hasRepo || mentions.files.length) { setQueueMessage("排队暂不解析 @repo 或 #file；请移除引用或等待当前任务结束，草稿未发送。"); return; }
      }
      const images = attachments.attachedImages;
      const imageBytes = images.reduce((total, image) => total + Math.floor(image.data.length * 3 / 4) - (image.data.endsWith("==") ? 2 : image.data.endsWith("=") ? 1 : 0), 0);
      if (codex && (images.length > 5 || imageBytes > 4 * 1024 * 1024 || images.some((image) => !["image/png", "image/jpeg", "image/gif", "image/webp"].includes(image.media_type)))) {
        setQueueMessage("Codex 支持 PNG、JPEG、GIF、WebP，最多 5 张、合计 4 MiB；请调整图片，草稿未发送。"); return;
      }
      if (!draft.value.trim() && images.length === 0) return;
      const content: string | ContentPart[] = images.length ? [...(draft.value.trim() ? [{ type: "text" as const, text: draft.value.trim() }] : []), ...images] : draft.value;
      const snapshot = { text: draft.value, session: sessionId, project: selectedProjectId, host: ipc.agentBaseUrl,
        files: attachments.attachedFiles, images: attachments.attachedImages, config: configValue };
      queueSnapshot.current = snapshot; setQueueMessage(""); const ticket = admissionScope(); queueAdmission.current = ticket;
      try {
        const result = await queue.submit({ content, draft: draft.value, budgetTokens: budgetNumber,
          mode: executionMode, restrictNetworkTools, captureFileChanges });
        if (result === "accepted") {
          if (!clearAcceptedQueueDraft(snapshot)) setQueueMessage("任务已接收；草稿或上下文已变化，当前输入已保留。");
          queueSnapshot.current = null;
        }
      } finally { if (queueAdmission.current === ticket) queueAdmission.current = null; }
      return;
    }

    const selectedRestriction = restrictNetworkTools;
    const rawText = draft.value;
    const hasText = rawText.trim().length > 0;
    const hasImages = attachments.attachedImages.length > 0;
    if (!hasText && !hasImages) return;

    if (codex) {
      if (restrictNetworkTools) {
        useChat.setState({ error: "当前联网工具限制仅支持 Native；请关闭该限制或选择 Native 后再发送。" });
        return;
      }
      const images = attachments.attachedImages;
      const totalBytes = images.reduce((total, image) => total + Math.floor(image.data.length * 3 / 4) - (image.data.endsWith("==") ? 2 : image.data.endsWith("=") ? 1 : 0), 0);
      if (images.some((image) => !["image/png", "image/jpeg", "image/gif", "image/webp"].includes(image.media_type)) || totalBytes > 4 * 1024 * 1024) {
        useChat.setState({ error: "Codex 支持 PNG、JPEG、GIF、WebP，最多 5 张、总计 4 MiB；请移除不符合要求的图片。" });
        return;
      }
      const content: string | ContentPart[] = hasImages
        ? [...(hasText ? [{ type: "text" as const, text: rawText.trim() }] : []), ...images]
        : rawText;
      draft.clear();
      attachments.clearAll();
      mention.closePicker();
      await send(content, budgetNumber);
      return;
    }
    // Resolve @repo / #file mentions into codebase context.
    const original = useSessionStore.getState();
    const origin = ipc.agentBaseUrl;
    const originalProject = original.currentProjectId;
    const originalSession = original.currentSessionId;
    const originalFiles = attachments.attachedFiles;
    const originalImages = attachments.attachedImages;
    const originalRecord = original.sessions.find((item) => item.id === originalSession);
    const originalProjectRecord = original.projects.find((item) => item.id === (originalRecord?.project_id ?? originalProject));
    let scopeChanged = false;
    const unsubscribe = useSessionStore.subscribe((state) => {
      const record = state.sessions.find((item) => item.id === originalSession);
      const projectRecord = state.projects.find((item) => item.id === (originalRecord?.project_id ?? originalProject));
      if (state.currentSessionId !== originalSession || state.currentProjectId !== originalProject ||
          (!originalSession && state.newSessionEngine !== original.newSessionEngine) ||
          record?.project_id !== originalRecord?.project_id || record?.workspace_path !== originalRecord?.workspace_path ||
          record?.workspace_mode !== originalRecord?.workspace_mode || record?.engine !== originalRecord?.engine ||
          projectRecord?.root_path !== originalProjectRecord?.root_path) scopeChanged = true;
    });
    const stopSidecar = ipc.onSideCar(() => { scopeChanged = true; });
    let resolved;
    try { resolved = await context.buildContext(rawText); }
    finally { unsubscribe(); stopSidecar(); }
    const current = useSessionStore.getState();
    const liveDraft = draft.getCurrent();
    const currentStatus = useChat.getState().status;
    if (scopeChanged || ipc.agentBaseUrl !== origin || current.currentSessionId !== originalSession ||
        current.currentProjectId !== originalProject || liveDraft.id !== originalSession || liveDraft.text !== rawText ||
        latestAttachments.current.attachedFiles !== originalFiles || latestAttachments.current.attachedImages !== originalImages ||
        restrictionRef.current !== selectedRestriction || currentStatus === "sending" || currentStatus === "streaming" || currentStatus === "cancelling") {
      useChat.setState({ error: "准备任务期间服务、会话、项目或草稿发生变化；未发送，请核对当前执行位置后重试。" });
      return;
    }
    if (!resolved) return;
    const { text: finalText, hasContext } = resolved;

    if (hasImages) {
      // Build multimodal ContentPart array
      const parts: ContentPart[] = [];
      if (hasText) {
        parts.push({ type: "text", text: finalText.trim() });
      }
      parts.push(...attachments.attachedImages);
      draft.clear();
      attachments.clearAll();
      mention.closePicker();
      changeRestriction(false);
      await send(parts, budgetNumber, selectedRestriction);
    } else {
      draft.clear();
      attachments.clearAll();
      mention.closePicker();
      changeRestriction(false);
      await send(hasContext ? finalText : rawText, budgetNumber, selectedRestriction);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "?") {
      e.stopPropagation();
    }
    if (!codex && mention.handleKeyDown(e)) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSubmit(e);
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      data-testid={testId}
      data-floating="false"
      className="max-h-[55%] shrink-0 overflow-y-auto bg-surface-0 px-3 pb-3 pt-1 sm:px-6 sm:pb-5"
    >
      <div
        onDragOver={attachments.handleDragOver}
        onDragLeave={attachments.handleDragLeave}
        onDrop={attachments.handleDrop}
        className={
          "relative mx-auto w-full max-w-[780px] rounded-2xl border bg-surface-2 shadow-sm transition-colors duration-150 " +
          (attachments.dragging
            ? "border-accent ring-2 ring-accent/30"
            : "border-line focus-within:border-line-strong")
        }
      >
        {/* Drop zone overlay */}
        {attachments.dragging && (
          <div
            data-testid="message-input-dropzone"
            className="absolute inset-0 z-10 flex items-center justify-center rounded-md bg-accent-subtle"
          >
            <span className="text-xs font-medium text-accent">{strings.chat.input.dropFilesHere}</span>
          </div>
        )}
        <AttachmentRows
          attachedFiles={attachments.attachedFiles}
          attachedImages={attachments.attachedImages}
          onRemoveFile={attachments.removeFile}
          onRemoveImage={attachments.removeImage}
        />
        <div className="flex flex-wrap items-center gap-1 px-3 pb-2 pt-3">
          <textarea
            ref={draft.ref}
            aria-label={strings.chat.input.messageLabel}
            name="message"
            autoComplete="off"
            value={draft.value}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder={streaming ? "输入下一任务，将在当前任务之后执行…" : "描述目标、相关资料和期望结果…"}
            rows={2}
            title="Enter 发送 · Shift + Enter 换行"
            data-testid="message-input-textarea"
            disabled={disabled}
            className="min-h-[64px] w-full resize-none bg-transparent px-1 py-1 text-[15px] leading-6 text-ink-0 placeholder:text-ink-2 focus:outline-none"
          />
          <DropdownMenu align="left" testId="message-attach-btn" trigger={
            <IconButton aria-label={strings.chat.input.attachFile} title="添加文件" disabled={disabled} className="h-8 w-8"><Paperclip size={14} /></IconButton>
          } items={[
            { id: "text", label: "文本文件（内容加入草稿）", onClick: () => attachments.fileInputRef.current?.click() },
            { id: "image", label: "图片", onClick: () => attachments.imageInputRef.current?.click() },
            { id: "document", label: "PDF、Word、Excel 等文件", onClick: () => setProjectFileOpen(true) },
          ]} />
          <ProjectAttachmentImport sessionId={sessionId} projectId={projectId} host={budgetHost} open={projectFileOpen} onClose={() => setProjectFileOpen(false)} />
          <input
            ref={attachments.imageInputRef}
            type="file"
            accept={codex ? "image/png,image/jpeg,image/gif,image/webp" : "image/*"}
            multiple
            className="hidden"
            data-testid="message-image-input"
            onChange={attachments.handleImageSelect}
          />
          <input
            ref={attachments.fileInputRef}
            type="file"
            multiple
            accept={ACCEPTED_EXTS}
            className="hidden"
            data-testid="message-file-input"
            onChange={attachments.handleFileSelect}
          />

        <label className="flex items-center gap-1">
          <select
            aria-label="任务模式"
            data-testid="execution-mode"
            value={executionMode}
            disabled={disabled}
            onChange={(event) => setExecutionMode(event.target.value as "execute" | "plan")}
            className="rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs text-ink-1 hover:bg-surface-3"
          >
            <option value="execute">执行</option>
            <option value="plan">只读规划</option>
          </select>
        </label>
        <ComposerToolbar
          valueLength={draft.value.length}
          overLimit={draft.overLimit}
          usagePct={draft.usagePct}
          nearLimit={draft.nearLimit}
          critical={draft.critical}
          cancelling={cancelling}
        />
          <details ref={optionsRef} className="relative" open={!budgetValid ? true : undefined} onToggle={(event) => { if (event.currentTarget.open) placeOptions(); }} onKeyDown={(event) => {
            if (event.key === "Escape" && budgetValid) { event.stopPropagation(); event.currentTarget.open = false; event.currentTarget.querySelector<HTMLElement>("summary")?.focus(); }
          }}>
            <summary className="cursor-pointer list-none rounded-lg px-2 py-1.5 text-xs text-ink-1 hover:bg-surface-3" title="项目、目录与任务设置">任务设置{restrictNetworkTools ? " · 限制联网" : ""}{budgetText ? ` · ${budgetText} tokens` : ""}</summary>
            <div style={optionsPosition} className="fixed z-50 overflow-y-auto rounded-xl border border-line bg-surface-1 p-3 shadow-pop">
              <ComposerSafetyControls />
              {codex && <p className="mt-2 text-[11px] text-ink-2">Codex 使用独立模型配置；会话历史与外部线程绑定，如需修改历史，请新建会话并交接上下文。文本文件内容会插入草稿；移除文件标签不会删除文字。图片最多 5 张、总计 4 MiB，随请求持久保存在本地数据库及 Codex 线程中；模型是否支持图片由 Codex 决定。@ 和 # 按普通文本发送。</p>}
        <div className="space-y-2 border-b border-line py-3 text-xs" aria-label="任务提交位置">
          <p className="font-medium text-ink-0">任务上下文</p>
          <dl className="grid grid-cols-[4rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-ink-2">
            <dt>服务地址</dt><dd className="break-all text-ink-1">{serviceLabel(ipc.agentBaseUrl)}</dd>
            <dt>项目</dt><dd className="break-all text-ink-1">{project?.name ?? (projectId ? "加载中或未知" : "未指定")}</dd>
            <dt>会话</dt><dd className="break-all text-ink-1">{sessionId ? session?.title ?? "加载中或未知" : "发送时新建"}</dd>
            <dt>引擎</dt><dd className="text-ink-1">{sessionId && !session ? "加载中或未知" : (sessionId ? session?.engine === "codex" : codex) ? "Codex" : "Native"}{!sessionId ? "（新会话）" : ""}</dd>
            {sessionId && <><dt>模型</dt><dd className="break-all text-ink-1">{session?.engine === "codex" ? "由 Codex CLI 配置；实际模型以运行详情为准" : session?.model_id || "会话未记录；实际模型以运行详情为准"}</dd></>}
            <dt>目录配置</dt><dd className="break-all text-ink-1">{session?.workspace_path || project?.root_path || "未指定或未知"}</dd>
          </dl>
          <p className="text-ink-2">实际执行主机和目录以任务运行详情为准。</p>
        </div>
        <div className="border-t border-line py-2 text-xs text-ink-2">
        <div className="min-w-0 space-y-2">
        {(!codex || restrictNetworkTools) && <label className="block py-2 text-xs text-ink-2"><input type="checkbox" checked={restrictNetworkTools} disabled={disabled} onChange={(event) => changeRestriction(event.target.checked)} /> 限制联网工具：仅允许读取已保存来源、提问及按写权限新建 Markdown/TXT；禁用 shell、MCP 等。模型服务仍可能联网，并非网络隔离。{codex && "此限制不支持 Codex，请先关闭或选择 Native。"}</label>}
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={captureFileChanges} disabled={disabled}
            onChange={(event) => setCaptureFileChanges(event.target.checked)} />
          采集任务前后文件变化（受限于 Git 可见文件）
        </label>
        <label className="flex flex-wrap items-center gap-2 px-2 pb-1 text-xs text-ink-1">
          下一任务 token 上限
          <input aria-label="下一任务 token 上限" inputMode="numeric" pattern="[0-9]*" value={budgetText} maxLength={16}
            onChange={(event) => setBudgetScope({ host: budgetHost, session: sessionId, value: event.target.value })}
            placeholder="空白：服务默认" className="w-36 rounded border border-line bg-surface-0 px-2 py-1 text-ink-0" />
          <span className="text-ink-2">留空使用服务默认。</span>
        </label>
        {!budgetValid && <p role="alert" className="px-2 text-xs text-status-error">请输入安全范围内的正整数，或留空使用服务默认。</p>}
        <p className="px-2 pb-1 text-[11px] text-ink-2">Token 上限不等于费用上限，也不能保证严格限制用量。</p>
        </div>
        </div>
            </div>
          </details>

          {streaming && <Button variant="danger" size="sm" type="button" data-testid="message-input-cancel"
            onClick={() => { if (queue.busy || admissionInCurrentScope()) return;
              const warning = pendingQueue || queue.blocked
                ? "仍有下一任务的接收结果未确认，它可能在停止后才被接收；请核对时间线。停止此会话会请求取消当前运行和已接收的排队任务，已执行操作不会撤销。确定停止吗？"
                : "停止此会话将同时取消其排队任务。已执行的操作不会撤销，确定停止吗？";
              if (window.confirm(warning)) void cancel(); }}
            disabled={queue.busy} loading={cancelling} icon={<Square size={12} />} className="px-2" title={queue.busy ? "接单请求正在确认，请完成后再停止" : "停止会话并取消排队任务"}
            aria-label={cancelling ? strings.chat.input.stopping : "停止会话并取消排队任务"}>{cancelling ? "停止中" : "停止"}</Button>}
          <IconButton aria-label={voice.listening ? strings.chat.input.stopVoice : strings.chat.input.startVoice}
            data-testid="message-voice-btn" onClick={voice.toggleVoice} className={"h-8 w-8 " + (voice.listening ? "animate-pulse bg-[var(--status-error-subtle)] text-status-error" : "")}
            title={voice.listening ? strings.chat.input.listening : strings.chat.input.voiceInput}>
            {voice.listening ? <MicOff size={14} /> : <Mic size={14} />}
          </IconButton>
          <Button variant="primary" size="sm" type="submit" data-testid="message-input-send"
            disabled={(!draft.value.trim() && attachments.attachedImages.length === 0) || draft.overLimit || !budgetValid || context.loading || cancelling || queue.busy || !!pendingQueue || (!!sessionId && queue.blocked) || (!!sessionId && !queue.ready)}
            loading={context.loading || queue.busy} icon={<Send size={14} />} className="px-2"
            title={streaming ? "发送为独立的下一任务，不会中途追加" : strings.chat.input.send}
            aria-label={streaming ? "发送下一任务" : strings.chat.input.send}>{streaming ? "发送下一任务" : "发送"}</Button>
        </div>
        {codex && (attachments.attachedFiles.length > 0 || attachments.attachedImages.length > 0) && <p className="px-2.5 pb-2 text-[11px] text-ink-2">文本附件内容会插入草稿，移除标签不会删除文字。图片最多 5 张、合计 4 MiB，将持久保存在本地数据库和 Codex 线程中；模型可能不支持图片。</p>}
        {!codex && <MentionPickerDropdown
          picker={mention.picker}
          filtered={mention.filtered}
          agentsError={mention.agentsError}
          onSelect={(option) => void mention.select(option)}
        />}
        {cancelling && <p role="status" className="px-3 pb-2 text-xs text-ink-2">正在停止会话及排队任务…</p>}
        {!!sessionId && !queue.ready && <p role="status" className="px-3 pb-2 text-xs text-ink-2">正在检查此会话的待确认请求…</p>}
        {queue.busy && streaming && <p role="status" className="px-3 pb-2 text-xs text-ink-2">下一任务接单正在确认；完成后可停止会话。</p>}
        {queueMessage && <p role="status" className="break-words px-3 pb-2 text-xs text-status-warning">{queueMessage}</p>}
        {queue.notice && <p role="status" className="break-words px-3 pb-2 text-xs text-status-warning">{queue.notice}</p>}
        {!!sessionId && queue.blocked && !pendingQueue && <div className="mx-3 mb-2 text-xs text-status-warning">本地待确认记录无法读取或保存；请先核对任务时间线。
          <Button type="button" size="sm" variant="ghost" disabled={queue.busy} onClick={() => {
            if (window.confirm("请先核对任务时间线。移除本地记录不会取消已接收任务，确定移除？")) { queue.forget(); queueSnapshot.current = null; }
          }}>核对后移除本地记录</Button></div>}
        {legacyQueueDraft && <details className="mx-3 mb-2 text-xs text-ink-1"><summary className="cursor-pointer">有旧版排队草稿</summary>
          <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words">{legacyQueueDraft}</pre>
          <Button type="button" size="sm" variant="secondary" onClick={() => {
            const current = liveDraft.current.getCurrent();
            const next = current.text ? `${current.text}\n\n${legacyQueueDraft}` : legacyQueueDraft;
            if (current.id !== sessionId || next.length > MAX_INPUT_CHARS) { setQueueMessage("无法加入旧草稿：会话已变化或超过输入上限。"); return; }
            liveDraft.current.setValue(next); setQueueMessage("旧草稿已加入输入框，尚未发送；原记录仍保留。");
          }}>加入输入框</Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => {
            if (!window.confirm("仅移除旧版本地排队草稿，不影响已接收任务。确定移除？")) return;
            try { localStorage.removeItem(legacyKey); setLegacyDraftState({ key: legacyKey, text: "" }); } catch { setQueueMessage("无法移除旧草稿，请检查浏览器存储。"); }
          }}>移除旧草稿</Button></details>}
        {pendingQueue && <div className="mx-3 mb-2 space-y-1 rounded-lg border border-status-warning/30 bg-[var(--status-warning-subtle)] p-2 text-xs text-ink-1">
          <p>上一条下一任务的接收结果待确认。请先核对时间线；重试使用原请求，不会发送当前新草稿。</p>
          <details><summary className="cursor-pointer">查看原请求内容与设置</summary>
            <p>引擎：{pendingQueue.request.engine ?? "未记录"} · 模式：{pendingQueue.request.mode ?? "未记录"} · token 上限：{pendingQueue.request.budget_tokens ?? "服务默认（接收时确定）"}{pendingQueue.request.restrict_network_tools ? " · 已限制联网工具" : ""}</p>
            <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words">{describeQueuedContent(pendingQueue.request.content)}</pre>
          </details>
          <div className="flex flex-wrap gap-2"><Button type="button" size="sm" variant="secondary" disabled={queue.busy} onClick={() => void (async () => {
            const snapshot = queueSnapshot.current, pending = pendingQueue; if (!pending || admissionInCurrentScope()) return; setQueueMessage("");
            const ticket = admissionScope(); queueAdmission.current = ticket;
            try {
              const result = await queue.retry();
              if (result === "accepted") {
                if (!snapshot || snapshot.text !== pending.draft || !clearAcceptedQueueDraft(snapshot)) setQueueMessage("原请求已接收；当前草稿已保留，请核对后再发送。");
                queueSnapshot.current = null;
              }
            } finally { if (queueAdmission.current === ticket) queueAdmission.current = null; }
          })()}>重试同一请求</Button>
          <Button type="button" size="sm" variant="ghost" disabled={queue.busy} onClick={() => {
            if (window.confirm("仅移除本地待确认记录，已接收的任务不会取消。请先核对时间线；确定移除？")) { queue.forget(); queueSnapshot.current = null; }
          }}>移除本地记录</Button></div>
        </div>}


      </div>
    </form>
  );
}
