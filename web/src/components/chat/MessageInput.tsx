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
import { useChat } from "../../stores";
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

  const draft = useComposerDraft();
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
            <span className="text-xs font-medium text-accent">Drop files here…</span>
          </div>
        )}
        <AttachmentRows
          attachedFiles={attachments.attachedFiles}
          attachedImages={attachments.attachedImages}
          onRemoveFile={attachments.removeFile}
          onRemoveImage={attachments.removeImage}
        />
        <div className="flex items-end gap-1 px-2 py-2 sm:gap-2 sm:px-2.5">
          <IconButton
            aria-label="Attach file"
            data-testid="message-attach-btn"
            onClick={() => attachments.fileInputRef.current?.click()}
            title="Attach a text file"
            className="h-8 w-8"
          >
            <Paperclip size={14} />
          </IconButton>
          <IconButton
            aria-label="Attach image"
            data-testid="message-image-btn"
            onClick={() => attachments.imageInputRef.current?.click()}
            title="Attach an image"
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
            aria-label="Message"
            name="message"
            autoComplete="off"
            value={draft.value}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder="Ask MiniMax anything…  (Enter · @agent · @repo · #file)"
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
              title={cancelling ? "Stopping..." : "Stop"}
              aria-label={cancelling ? "Stopping..." : "Stop"}
            />
          ) : (
            <>
              <IconButton
                aria-label={voice.listening ? "Stop voice input" : "Start voice input"}
                data-testid="message-voice-btn"
                onClick={voice.toggleVoice}
                className={
                  "h-8 w-8 " +
                  (voice.listening
                    ? "animate-pulse bg-[var(--status-error-subtle)] text-status-error hover:bg-[var(--status-error-subtle)] hover:text-status-error"
                    : "")
                }
                title={voice.listening ? "Listening… click to stop" : "Voice input"}
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
                title={context.loading ? "Loading context…" : "Send"}
                aria-label={context.loading ? "Loading context…" : "Send"}
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
