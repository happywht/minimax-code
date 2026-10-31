/**
 * MessageInput — the bottom chat composer.
 *
 * This is a thin composition layer: draft state lives in
 * ``useComposerDraft``, attachments in ``useAttachments``, voice
 * dictation in ``useVoiceInput``, and the `@agent` autocomplete in
 * ``useMentionPicker``. Presentational pieces are split into
 * ``AttachmentRows``, ``MentionPickerDropdown`` and ``ComposerToolbar``.
 */
import { Camera, Mic, MicOff, Paperclip, Send, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ipc } from "../../ipc";
import { useChat, useSessionStore } from "../../stores";
import { Button, IconButton } from "../../ui";
import type { AgentInfo, ContentPart } from "../../types/ipc";
import { ACCEPTED_EXTS } from "./constants";
import { useComposerDraft } from "./useComposerDraft";
import { useAttachments } from "./useAttachments";
import { useVoiceInput } from "./useVoiceInput";
import { useMentionPicker } from "./useMentionPicker";
import { useMentionContext } from "./useMentionContext";
import { AttachmentRows } from "./AttachmentRows";
import { MentionPickerDropdown } from "./MentionPickerDropdown";
import { ComposerToolbar } from "./ComposerToolbar";
import { TaskTemplates } from "./TaskTemplates";
import { DocumentTaskTemplate } from "./DocumentTaskTemplate";
import { SpreadsheetTaskTemplate } from "./SpreadsheetTaskTemplate";
import { ResearchTaskTemplate } from "./ResearchTaskTemplate";
import { QueuedTaskInput } from "./QueuedTaskInput";
import { ProjectAttachmentImport } from "./ProjectAttachmentImport";
import { strings } from "../../ui/strings";
import { CODEX_IMAGE_HANDOFF, type CodexImageHandoff } from "../../lib/codexImageHandoff";

function serviceLabel(value: string): string {
  try { return new URL(value).origin; } catch { return "未知"; }
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

  const disabled =
    status === "sending" || status === "streaming" || status === "cancelling" || context.loading;
  const streaming = status === "streaming" || status === "sending" || status === "cancelling";
  const cancelling = status === "cancelling";

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    draft.setValue(next);
    const caret = e.target.selectionStart ?? next.length;
    if (!codex) mention.updateForInput(next, caret);
  };

  const handleSubmit = async (e?: React.FormEvent | React.KeyboardEvent) => {
    e?.preventDefault();
    if (disabled) return;
    if (draft.overLimit || !budgetValid) return;

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
      className="max-h-[55%] shrink-0 overflow-y-auto border-t border-line bg-surface-0/92 px-2 py-2 backdrop-blur supports-[backdrop-filter]:bg-surface-0/78 sm:px-4 sm:py-3"
    >
      <div
        onDragOver={attachments.handleDragOver}
        onDragLeave={attachments.handleDragLeave}
        onDrop={attachments.handleDrop}
        className={
          "relative mx-auto w-full max-w-[780px] rounded-lg border bg-surface-1/95 shadow-pop backdrop-blur transition-colors duration-150 supports-[backdrop-filter]:bg-surface-1/85 " +
          (attachments.dragging
            ? "border-accent ring-2 ring-accent/30"
            : "border-line focus-within:border-line-strong")
        }
      >
        <details className="border-b border-line px-2.5 py-1 text-xs text-ink-2">
          <summary className="cursor-pointer break-all py-1 text-ink-1">提交位置 · {serviceLabel(ipc.agentBaseUrl)} / {project?.name ?? (projectId ? "项目加载中或未知" : "未指定项目")} / {sessionId ? session?.title ?? "会话加载中或未知" : "新会话"} / 目录配置：{session?.workspace_path || project?.root_path || "未指定或未知"}</summary>
          <div className="flex flex-wrap gap-x-3 gap-y-1 pb-1" aria-label="任务提交位置">
          <span>服务：{serviceLabel(ipc.agentBaseUrl)}</span>
          <span>会话：{sessionId ? session?.title ?? "加载中或未知" : "发送时新建"}</span>
          <span>{sessionId ? "会话所属项目" : "新会话项目"}：{project?.name ?? (projectId ? "加载中或未知" : "未指定")}</span>
          {session?.workspace_path ? <span>会话工作区：{session.workspace_path}</span> :
            <span>项目目录：{project?.root_path || "未指定或未知"}（实际执行目录以运行详情为准）</span>}
          </div>
        </details>
        {/* Drop zone overlay */}
        {attachments.dragging && (
          <div
            data-testid="message-input-dropzone"
            className="absolute inset-0 z-10 flex items-center justify-center rounded-md bg-accent-subtle"
          >
            <span className="text-xs font-medium text-accent">{strings.chat.input.dropFilesHere}</span>
          </div>
        )}
        <details className="border-b border-line px-2.5 py-1 text-xs text-ink-2">
          <summary className="cursor-pointer py-1 text-ink-1">任务模板与资料{restrictNetworkTools ? " · 已限制联网工具" : ""}</summary>
        <TaskTemplates
          disabled={disabled}
          codex={codex}
          onSelect={(text) => {
            draft.setValue((previous) => previous ? `${previous}\n\n${text}` : text);
            mention.closePicker();
            draft.ref.current?.focus();
          }}
        />
        {!codex && <ResearchTaskTemplate project={projectId} session={sessionId} host={budgetHost} disabled={disabled}
          onPrepare={(text, restricted) => {
            changeRestriction(restricted);
            draft.setValue((previous) => previous ? `${previous}\n\n${text}` : text);
            mention.closePicker();
            draft.ref.current?.focus();
          }} />}
        {!codex && <DocumentTaskTemplate project={projectId} session={sessionId} host={budgetHost} disabled={disabled}
          onPrepare={(text) => {
            draft.setValue((previous) => previous ? `${previous}\n\n${text}` : text);
            mention.closePicker();
            draft.ref.current?.focus();
          }} />}
        {!codex && <SpreadsheetTaskTemplate project={projectId} session={sessionId} host={budgetHost} disabled={disabled}
          onPrepare={(text) => {
            draft.setValue((previous) => previous ? `${previous}\n\n${text}` : text);
            mention.closePicker();
            draft.ref.current?.focus();
          }} />}
        {(!codex || restrictNetworkTools) && <label className="block px-2.5 py-1 text-xs text-ink-2"><input type="checkbox" checked={restrictNetworkTools} disabled={disabled} onChange={(event) => changeRestriction(event.target.checked)} /> 限制此任务的联网工具（允许读取已保存来源、提问及按写入权限新建 Markdown/TXT 文件；禁用 shell、MCP 和其他工具）。模型服务可能联网；不提供系统网络隔离。{codex && "此限制不支持 Codex，请先关闭或选择 Native。"}</label>}
        <ProjectAttachmentImport sessionId={sessionId} projectId={projectId} host={budgetHost} />
        </details>
        <AttachmentRows
          attachedFiles={attachments.attachedFiles}
          attachedImages={attachments.attachedImages}
          onRemoveFile={attachments.removeFile}
          onRemoveImage={attachments.removeImage}
        />
        <div className="flex items-end gap-1 px-2 py-2 sm:gap-2 sm:px-2.5">
          <IconButton
            aria-label={strings.chat.input.attachFile}
            data-testid="message-attach-btn"
            onClick={() => attachments.fileInputRef.current?.click()}
            title={strings.chat.input.attachTextFile}
            className="h-8 w-8"
          >
            <Paperclip size={14} />
          </IconButton>
          <IconButton
            aria-label={strings.chat.input.attachImage}
            data-testid="message-image-btn"
            onClick={() => attachments.imageInputRef.current?.click()}
            title={strings.chat.input.attachAnImage}
            className="h-8 w-8"
          >
            <Camera size={14} />
          </IconButton>
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
          <textarea
            ref={draft.ref}
            aria-label={strings.chat.input.messageLabel}
            name="message"
            autoComplete="off"
            value={draft.value}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder={streaming ? "当前任务进行中；可在下方排队新任务" : "描述目标、相关资料和期望结果…"}
            rows={2}
            title="Enter 发送 · Shift + Enter 换行"
            data-testid="message-input-textarea"
            disabled={disabled}
            className="min-w-0 flex-1 resize-none bg-transparent px-1 py-1.5 text-sm text-ink-0 placeholder:text-ink-2 focus:outline-none"
          />
          {streaming ? (
            <Button
              variant="danger"
              size="sm"
              data-testid="message-input-cancel"
              onClick={() => void cancel()}
              loading={cancelling}
              icon={<Square size={12} />}
              className="px-2"
              title={cancelling ? strings.chat.input.stopping : strings.chat.input.stop}
              aria-label={cancelling ? strings.chat.input.stopping : strings.chat.input.stop}
            >{cancelling ? "停止中" : "停止"}</Button>
          ) : (
            <>
              <IconButton
                aria-label={voice.listening ? strings.chat.input.stopVoice : strings.chat.input.startVoice}
                data-testid="message-voice-btn"
                onClick={voice.toggleVoice}
                className={
                  "h-8 w-8 " +
                  (voice.listening
                    ? "animate-pulse bg-[var(--status-error-subtle)] text-status-error hover:bg-[var(--status-error-subtle)] hover:text-status-error"
                    : "")
                }
                title={voice.listening ? strings.chat.input.listening : strings.chat.input.voiceInput}
              >
                {voice.listening ? <MicOff size={14} /> : <Mic size={14} />}
              </IconButton>
              <Button
                variant="primary"
                size="sm"
                type="submit"
                data-testid="message-input-send"
                disabled={
                  (!draft.value.trim() && attachments.attachedImages.length === 0) ||
                  draft.overLimit ||
                  !budgetValid ||
                  context.loading
                }
                loading={context.loading}
                icon={<Send size={14} />}
                className="px-2"
                title={context.loading ? strings.chat.input.loadingContext : strings.chat.input.send}
                aria-label={context.loading ? strings.chat.input.loadingContext : strings.chat.input.send}
              >发送</Button>
            </>
          )}
        </div>
        {codex && <p className="px-2.5 text-[11px] text-ink-2">文本文件内容会插入草稿；移除文件标签不会删除文字。图片最多 5 张、总计 4 MiB，随请求持久保存在本地数据库及 Codex 线程中；模型是否支持图片由 Codex 决定。@ 和 # 按普通文本发送。</p>}
        {!codex && <MentionPickerDropdown
          picker={mention.picker}
          filtered={mention.filtered}
          agentsError={mention.agentsError}
          onSelect={(option) => void mention.select(option)}
        />}
        {streaming && <p role="status" className="mx-2 rounded bg-accent-subtle px-2 py-1.5 text-xs text-ink-1">
          {cancelling ? "正在请求停止，请稍候。" : "当前任务进行中。可停止任务，或在下方排队另一个新任务。"}
        </p>}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-2 py-1 text-xs text-ink-2">
        <label className="flex items-center gap-2">
          任务模式
          <select
            aria-label="任务模式"
            data-testid="execution-mode"
            value={executionMode}
            disabled={disabled}
            onChange={(event) => setExecutionMode(event.target.value as "execute" | "plan")}
            className="rounded border bg-transparent px-2 py-1"
          >
            <option value="execute">执行</option>
            <option value="plan">只读规划</option>
          </select>
        </label>
        <details className="min-w-0 flex-1" open={!budgetValid ? true : undefined}>
          <summary className="cursor-pointer py-1 text-ink-1">高级选项{captureFileChanges ? " · 采集文件变化" : ""}{budgetText ? ` · 上限 ${budgetText} tokens` : ""}</summary>
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
          <span className="text-ink-2">空白沿用服务默认（当前值未知）；正整数用于接下来新任务及其子任务。</span>
        </label>
        {!budgetValid && <p role="alert" className="px-2 text-xs text-status-error">请输入安全范围内的正整数，或留空使用服务默认。</p>}
        <p className="px-2 pb-1 text-[11px] text-ink-2">这是 token 数量，不是金额或 Coding Plan 费用上限。日限额另行生效；Native 在请求前估算，Codex 按已观察用量事后控制，均不能保证严格费用上限。切换会话或连接变化会清空此输入。</p>
        </details>
        </div>
        <ComposerToolbar
          valueLength={draft.value.length}
          overLimit={draft.overLimit}
          usagePct={draft.usagePct}
          nearLimit={draft.nearLimit}
          critical={draft.critical}
          cancelling={cancelling}
        />
        <QueuedTaskInput sessionId={sessionId} budgetTokens={budgetNumber} budgetValid={budgetValid} />

      </div>
    </form>
  );
}
