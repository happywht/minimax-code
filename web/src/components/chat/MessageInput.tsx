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
import { strings } from "../../ui/strings";

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

  const codex = useSessionStore((s) => s.sessions.find((session) => session.id === sessionId)?.engine === "codex");
  const draft = useComposerDraft(sessionId);
  const attachments = useAttachments(draft.ref, draft.setValue);
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
    if (draft.overLimit) return;

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
      await send(rawText);
      return;
    }
    // Resolve @repo / #file mentions into codebase context.
    const resolved = await context.buildContext(rawText);
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
      await send(parts);
    } else {
      draft.clear();
      attachments.clearAll();
      mention.closePicker();
      await send(hasContext ? finalText : rawText);
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
          "relative mx-auto w-full max-w-[780px] rounded-md border bg-surface-1/95 shadow-pop backdrop-blur transition-colors duration-150 supports-[backdrop-filter]:bg-surface-1/85 " +
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
            placeholder={strings.chat.input.placeholder}
            rows={1}
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
              className="w-7 px-0"
              title={cancelling ? strings.chat.input.stopping : strings.chat.input.stop}
              aria-label={cancelling ? strings.chat.input.stopping : strings.chat.input.stop}
            />
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
                  context.loading
                }
                loading={context.loading}
                icon={<Send size={14} />}
                className="w-7 px-0"
                title={context.loading ? strings.chat.input.loadingContext : strings.chat.input.send}
                aria-label={context.loading ? strings.chat.input.loadingContext : strings.chat.input.send}
              />
            </>
          )}
        </div>
        <MentionPickerDropdown
          picker={mention.picker}
          filtered={mention.filtered}
          agentsError={mention.agentsError}
          onSelect={(option) => void mention.select(option)}
        />
        <label className="flex items-center gap-2 px-2 text-xs text-minimax-text-secondary">
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
        <label className="flex items-center gap-2 px-2 text-xs text-minimax-text-secondary">
          <input type="checkbox" checked={captureFileChanges} disabled={disabled}
            onChange={(event) => setCaptureFileChanges(event.target.checked)} />
          采集任务前后文件变化（受限于 Git 可见文件）
        </label>
        <ComposerToolbar
          valueLength={draft.value.length}
          overLimit={draft.overLimit}
          usagePct={draft.usagePct}
          nearLimit={draft.nearLimit}
          critical={draft.critical}
          cancelling={cancelling}
        />
      </div>
    </form>
  );
}
