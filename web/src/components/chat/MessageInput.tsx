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
import { QueuedTaskInput } from "./QueuedTaskInput";
import { strings } from "../../ui/strings";

function serviceLabel(value: string): string {
  try { return new URL(value).origin; } catch { return "未知"; }
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

  const codex = useSessionStore((s) => s.sessions.find((session) => session.id === sessionId)?.engine === "codex");
  const draft = useComposerDraft(sessionId);
  const attachments = useAttachments(draft.ref, draft.setValue);
  const latestAttachments = useRef(attachments);
  latestAttachments.current = attachments;
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
    mention.updateForInput(next, caret);
  };

  const handleSubmit = async (e?: React.FormEvent | React.KeyboardEvent) => {
    e?.preventDefault();
    if (disabled) return;
    if (draft.overLimit || !budgetValid) return;

    const rawText = draft.value;
    const hasText = rawText.trim().length > 0;
    const hasImages = attachments.attachedImages.length > 0;
    if (!hasText && !hasImages) return;

    if (codex) {
      if (hasImages || attachments.attachedFiles.length || /(^|\s)(@|#file)/.test(rawText)) {
        useChat.setState({ error: "Codex 当前仅支持纯文本，不支持附件或 Native 的 @agent/@repo/#file 功能。" });
        return;
      }
      draft.clear();
      mention.closePicker();
      await send(rawText, budgetNumber);
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
        currentStatus === "sending" || currentStatus === "streaming" || currentStatus === "cancelling") {
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
      await send(parts, budgetNumber);
    } else {
      draft.clear();
      attachments.clearAll();
      mention.closePicker();
      await send(hasContext ? finalText : rawText, budgetNumber);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "?") {
      e.stopPropagation();
    }
    if (mention.handleKeyDown(e)) return;
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
      className="shrink-0 border-t border-line bg-surface-0/92 px-2 py-2 backdrop-blur supports-[backdrop-filter]:bg-surface-0/78 sm:px-4 sm:py-3"
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
        <div className="flex flex-wrap gap-x-3 gap-y-1 border-b border-line px-2.5 py-2 text-xs text-ink-2" aria-label="任务提交位置">
          <span>服务：{serviceLabel(ipc.agentBaseUrl)}</span>
          <span>会话：{sessionId ? session?.title ?? "加载中或未知" : "发送时新建"}</span>
          <span>{sessionId ? "会话所属项目" : "新会话项目"}：{project?.name ?? (projectId ? "加载中或未知" : "未指定")}</span>
          {session?.workspace_path ? <span>会话工作区：{session.workspace_path}</span> :
            <span>项目目录：{project?.root_path || "未指定或未知"}（实际执行目录以运行详情为准）</span>}
        </div>
        {/* Drop zone overlay */}
        {attachments.dragging && (
          <div
            data-testid="message-input-dropzone"
            className="absolute inset-0 z-10 flex items-center justify-center rounded-md bg-accent-subtle"
          >
            <span className="text-xs font-medium text-accent">{strings.chat.input.dropFilesHere}</span>
          </div>
        )}
        <TaskTemplates
          disabled={disabled}
          codex={codex}
          onSelect={(text) => {
            draft.setValue((previous) => previous ? `${previous}\n\n${text}` : text);
            mention.closePicker();
            draft.ref.current?.focus();
          }}
        />
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
            disabled={codex}
            onClick={() => attachments.fileInputRef.current?.click()}
            title={strings.chat.input.attachTextFile}
            className="h-8 w-8"
          >
            <Paperclip size={14} />
          </IconButton>
          <IconButton
            aria-label={strings.chat.input.attachImage}
            data-testid="message-image-btn"
            disabled={codex}
            onClick={() => attachments.imageInputRef.current?.click()}
            title={strings.chat.input.attachAnImage}
            className="h-8 w-8"
          >
            <Camera size={14} />
          </IconButton>
          <input
            ref={attachments.imageInputRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            data-testid="message-image-input"
            disabled={codex}
            onChange={attachments.handleImageSelect}
          />
          <input
            ref={attachments.fileInputRef}
            type="file"
            multiple
            accept={ACCEPTED_EXTS}
            className="hidden"
            data-testid="message-file-input"
            disabled={codex}
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
        <MentionPickerDropdown
          picker={mention.picker}
          filtered={mention.filtered}
          agentsError={mention.agentsError}
          onSelect={(option) => void mention.select(option)}
        />
        {streaming && <p role="status" className="mx-2 rounded bg-accent-subtle px-2 py-1.5 text-xs text-ink-1">
          {cancelling ? "正在请求停止，请稍候。" : "当前任务进行中。可停止任务，或在下方排队另一个新任务。"}
        </p>}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-2 py-2 text-xs text-ink-2">
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
        <ComposerToolbar
          valueLength={draft.value.length}
          overLimit={draft.overLimit}
          usagePct={draft.usagePct}
          nearLimit={draft.nearLimit}
          critical={draft.critical}
          cancelling={cancelling}
        />
        <QueuedTaskInput sessionId={sessionId} budgetTokens={budgetNumber} budgetValid={budgetValid} />
        {!streaming && <p className="px-2.5 pb-2 text-[11px] text-ink-2">Enter 发送 · Shift + Enter 换行；运行中可排队新任务。</p>}
      </div>
    </form>
  );
}
