/**
 * Single message bubble — composition layer.
 *
 * Renders user, assistant, tool, and system roles. The heavy lifting
 * lives in focused modules under `./chat/`:
 *
 *   chat/markdown.ts           fence repair for LLM markdown
 *   chat/turnSummary.ts        per-turn tool-call counting
 *   chat/fileReferences.ts     path-mention extraction
 *   chat/MarkdownBody.tsx      react-markdown + GFM + KaTeX config
 *   chat/CodeBlock.tsx         shiki-highlighted code card
 *   chat/MermaidBlock.tsx      mermaid diagram card
 *   chat/FileReferenceStrip.tsx referenced-file cards
 *   chat/ToolCallCard.tsx      collapsible tool bubble
 *   chat/TurnSummaryRow.tsx    per-turn summary row
 *   chat/MessageStatusBadge.tsx lifecycle status pill
 *   chat/MessageActions.tsx    copy overlay + retry footer
 *
 * Assistant messages get a per-turn summary row at the top —
 * "思考 N 次 · 查看 M 个文件 · 修改 K 个文件" — derived from the
 * tool-call/tool-result messages that follow the assistant bubble in
 * the same turn (bounded by the next user/assistant message).
 */
import React, { useMemo, useState, type ChangeEvent } from "react";
import type { Message, MessageStatus } from "../../types/ipc";
import { useChat, useThemeStore } from "../../stores";
import { extractFileReferences } from "./fileReferences";
import { normalizeMarkdownForRender } from "./markdown";
import { summarizeTurn, type TurnSummary } from "./turnSummary";
import { FileReferenceStrip } from "./FileReferenceStrip";
import { MarkdownBody } from "./MarkdownBody";
import { FailedMessageFooter, MessageCopyOverlay } from "./MessageActions";
import { MessageActionMenu } from "./MessageActionMenu";
import { MessageStatusBadge } from "./MessageStatusBadge";
import { ToolCallCard } from "./ToolCallCard";
import { TurnSummaryRow } from "./TurnSummaryRow";
import { SourcesPanel } from "./SourcesPanel";
import { Brain } from "lucide-react";
import { strings } from "../../ui/strings";
import { Badge, Textarea, Button } from "../../ui";

export interface MessageItemProps {
  message: Message;
  testId?: string;
}

export const MessageItem = React.memo(function MessageItem({
  message,
  testId,
}: MessageItemProps): JSX.Element {
  const [isEditing, setIsEditing] = useState(false);
  const [editText, setEditText] = useState(message.text);
  const isUser = message.role === "user";
  const isTool = message.role === "tool";
  const isSystem = message.role === "system";
  const isAssistant = message.role === "assistant";
  const theme = useThemeStore((s) => s.theme);
  const status: MessageStatus =
    message.status ?? (message.streaming ? "streaming" : "completed");
  const isFailed = status === "failed";
  const isQueued = status === "queued" || status === "sending";
  const isCancelling = status === "cancelling";
  const isCancelled = status === "cancelled";
  const showStatus = isAssistant && status !== "completed";
  const showSummary = isAssistant && status === "completed";

  // Per-turn summary is only meaningful for assistant messages.
  // We pull the full message log from the chat store so we can count
  // tool calls that happened in the same turn.
  const messages = useChat((s) => s.messages);
  const updateMessage = useChat((s) => s.updateMessage);
  const deleteMessage = useChat((s) => s.deleteMessage);
  const summary = useMemo<TurnSummary | null>(() => {
    if (!isAssistant) return null;
    const idx = messages.findIndex((m) => m.id === message.id);
    if (idx < 0) return { thinkingCount: 0, filesViewed: 0, filesModified: 0 };
    return summarizeTurn(messages, idx, message);
  }, [isAssistant, messages, message]);
  const renderText = useMemo(
    () => (isUser ? message.text : normalizeMarkdownForRender(message.text || "")),
    [isUser, message.text],
  );
  const fileReferences = useMemo(
    () => extractFileReferences(renderText),
    [renderText],
  );

  // Tool bubbles are collapsible to keep the chat scannable.
  if (isTool) {
    return <ToolCallCard message={message} testId={testId} />;
  }

  const bubbleClass = isUser
    ? "group relative max-w-[80%] rounded-2xl rounded-br-md bg-accent py-2 pl-9 pr-4 text-sm text-accent-contrast shadow-sm transition-colors duration-200"
    : isSystem
      ? "max-w-[80%] rounded-lg border border-status-error/30 bg-[var(--status-error-subtle)] px-3 py-1.5 text-xs italic text-status-error transition-colors duration-200"
      : "group relative max-w-[85%] rounded-2xl rounded-bl-md border py-2 pl-4 pr-9 text-sm shadow-sm transition-colors duration-200 " +
        (isFailed
          ? "border-status-error/40 bg-[var(--status-error-subtle)] text-status-error"
          : isCancelling
            ? "border-status-warning/30 bg-[var(--status-warning-subtle)] text-ink-0"
            : isCancelled
              ? "border-line bg-surface-1 text-ink-1"
              : "border-line bg-surface-2 text-ink-0");
  const canEdit = isUser && !message.streaming;
  const canDelete = !isSystem && !message.streaming;

  return (
    <div
      data-testid={testId ?? `message-${message.role}`}
      data-role={message.role}
      className={
        isUser
          ? "flex justify-end"
          : isSystem
            ? "flex justify-center"
            : "flex justify-start"
      }
    >
      <div className={bubbleClass}>
        {!isSystem && message.text && (
          <MessageCopyOverlay
            messageId={message.id}
            text={message.text}
            onAccent={isUser}
          />
        )}
        {!isSystem && (
          <div
            className={
              "absolute top-1.5 z-10 opacity-0 transition-opacity duration-200 focus-within:opacity-100 group-hover:opacity-100 " +
              (isUser ? "right-1.5" : "left-1.5")
            }
          >
            <MessageActionMenu
              messageId={message.id}
              editable={canEdit}
              deletable={canDelete}
              onEdit={() => {
                setEditText(message.text);
                setIsEditing(true);
              }}
              onDelete={() => void deleteMessage(message.id)}
            />
          </div>
        )}
        {showSummary && summary && (
          <TurnSummaryRow messageId={message.id} summary={summary} />
        )}
        {message.metadata?.sources && message.metadata.sources.length > 0 && (
          <SourcesPanel sources={message.metadata.sources} />
        )}
        {isAssistant && message.metadata?.memory_count ? (
          <div className="mb-1.5 flex items-center gap-1">
            <Badge tone="accent" data-testid={`message-memory-chip-${message.id}`}>
              <Brain size={10} />
              <span>{strings.chat.memory.savedCount(message.metadata.memory_count)}</span>
            </Badge>
          </div>
        ) : null}
        {showStatus && <MessageStatusBadge messageId={message.id} status={status} />}
        {isEditing ? (
          <div className="flex flex-col gap-2">
            <Textarea
              value={editText}
              onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setEditText(e.target.value)}
              className={isUser ? "bg-accent-contrast text-ink-0" : ""}
              rows={3}
              data-testid={`message-edit-input-${message.id}`}
            />
            <div className="flex justify-end gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setIsEditing(false)}
                data-testid={`message-edit-cancel-${message.id}`}
              >
                取消
              </Button>
              <Button
                size="sm"
                variant="primary"
                onClick={() => {
                  void updateMessage(message.id, editText);
                  setIsEditing(false);
                }}
                data-testid={`message-edit-save-${message.id}`}
              >
                保存
              </Button>
            </div>
          </div>
        ) : isQueued && !message.text ? (
          <div data-testid={`message-skeleton-${message.id}`} className="space-y-2 py-1">
            <div className="h-3 w-52 animate-pulse rounded bg-surface-3" />
            <div className="h-3 w-40 animate-pulse rounded bg-surface-3/70" />
          </div>
        ) : !isFailed ? (
          <div
            className={`prose prose-sm max-w-none break-words leading-relaxed [&_.katex-display]:my-3 [&_.katex-display]:overflow-x-auto [&_.katex-display]:overflow-y-hidden [&_.katex-display]:py-1 [&_.katex]:text-[1.02em]${theme === "dark" ? " prose-invert" : ""}`}
          >
            <FileReferenceStrip refs={fileReferences} />
            {isUser ? (
              <p className="m-0 whitespace-pre-wrap">{renderText}</p>
            ) : (
              <MarkdownBody text={renderText} />
            )}
          </div>
        ) : null}
        {isFailed && <FailedMessageFooter message={message} />}
        {status === "streaming" && !isUser && (
          <span
            aria-hidden
            className="ml-0.5 inline-block w-2 animate-pulse text-accent"
          >
            ▍
          </span>
        )}
      </div>
    </div>
  );
});
