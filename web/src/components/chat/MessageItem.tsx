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
import React, { useMemo } from "react";
import type { Message, MessageStatus } from "../../types/ipc";
import { useChat, useThemeStore } from "../../stores";
import { extractFileReferences } from "./fileReferences";
import { normalizeMarkdownForRender } from "./markdown";
import { summarizeTurn, type TurnSummary } from "./turnSummary";
import { FileReferenceStrip } from "./FileReferenceStrip";
import { MarkdownBody } from "./MarkdownBody";
import { FailedMessageFooter, MessageCopyOverlay } from "./MessageActions";
import { MessageStatusBadge } from "./MessageStatusBadge";
import { ToolCallCard } from "./ToolCallCard";
import { TurnSummaryRow } from "./TurnSummaryRow";

export interface MessageItemProps {
  message: Message;
  testId?: string;
}

export const MessageItem = React.memo(function MessageItem({
  message,
  testId,
}: MessageItemProps): JSX.Element {
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
        {showSummary && summary && (
          <TurnSummaryRow messageId={message.id} summary={summary} />
        )}
        {showStatus && <MessageStatusBadge messageId={message.id} status={status} />}
        {isQueued && !message.text ? (
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
