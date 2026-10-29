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
import { useChat, useThemeStore, useSessionStore, useRunTimelineStore } from "../../stores";
import { ipc } from "../../ipc";
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
import { Brain, FastForward } from "lucide-react";
import { strings } from "../../ui/strings";
import { Badge, Textarea, Button } from "../../ui";
import { VerificationBadge } from "./VerificationBadge";

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
  const [planActionMessage, setPlanActionMessage] = useState("");
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
  const continueRun = useChat((s) => s.continueRun);
  const sessionEngine = useSessionStore((s) => s.sessions.find((session) => session.id === s.currentSessionId)?.engine);
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const projectId = useSessionStore((s) => s.currentProjectId);
  const host = ipc.agentBaseUrl;
  const planRun = useRunTimelineStore((s) => message.metadata?.run_id ? s.runs[message.metadata.run_id] : undefined);
  const codex = message.metadata?.engine === "codex" || sessionEngine === "codex";
  const chatBusy = useChat(
    (s) => s.status === "sending" || s.status === "streaming" || s.status === "cancelling",
  );
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
  const canEdit = !codex && isUser && !message.streaming;
  const canDelete = !codex && !isSystem && !message.streaming;
  const canPreparePlan = isAssistant && !!sessionId && planRun?.session_id === sessionId &&
    planRun.mode === "plan" && planRun.status === "completed" && planRun.assistant_message_id === message.id &&
    !planRun.metadata?.truncated && !planRun.metadata?.codex_plan?.truncated &&
    !message.metadata?.truncated && !message.metadata?.partial && !!message.text.trim();
  const preparePlan = () => {
    const state = useSessionStore.getState();
    const liveRun = planRun && useRunTimelineStore.getState().runs[planRun.id];
    const liveMessage = useChat.getState().messages.find((item) => item.id === message.id);
    const timeline = useRunTimelineStore.getState();
    if (!sessionId || ipc.agentBaseUrl !== host || timeline.loading || timeline.error ||
        state.currentSessionId !== sessionId || state.currentProjectId !== projectId ||
        !liveRun || liveRun.session_id !== sessionId || liveRun.mode !== "plan" || liveRun.status !== "completed" ||
        liveRun.assistant_message_id !== message.id || liveRun.metadata?.truncated || liveRun.metadata?.codex_plan?.truncated ||
        !liveMessage || liveMessage.text !== message.text || liveMessage.metadata?.truncated || liveMessage.metadata?.partial ||
        ["sending", "streaming", "cancelling"].includes(useChat.getState().status)) {
      setPlanActionMessage("计划或会话状态已变化，请刷新后重试。");
      return;
    }
    const plan = liveRun.metadata?.codex_plan;
    const structuredPlan = plan ? `\n\n引擎报告的计划：\n${plan.explanation ?? ""}\n${plan.steps.map((step, index) => `${index + 1}. ${step.step} (${step.status})`).join("\n")}` : "";
    const detail: { host: string; project: string | null; session: string; text: string; result?: string } = {
      host, project: projectId, session: sessionId,
      text: `请执行以下我选择的计划；执行前核对当前工作区和必要授权。遇到需要我决定的事项请先询问。\n\n所选规划运行：${liveRun.id}\n\n${liveMessage.text}${structuredPlan}`,
    };
    window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
    if (detail.result === "已添加到当前会话草稿，尚未发送。") {
      useChat.getState().setExecutionMode("execute");
      setPlanActionMessage("计划已加入草稿并切换到执行模式；请核对、设置预算后发送。");
    } else setPlanActionMessage(detail.result ?? "聊天输入区未就绪，请稍后重试。");
  };

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
        {showSummary && summary && !codex && (
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
        {isAssistant && !message.streaming && message.metadata?.truncated ? (
          <div
            className="mt-1.5 flex flex-wrap items-center gap-2"
            data-testid={`message-truncated-${message.id}`}
          >
            <Badge tone="warning">{strings.chat.continueRun.badge}</Badge>
            {typeof message.metadata.compactions === "number" && message.metadata.compactions > 0 ? (
              <span className="text-xs text-ink-2">
                {strings.chat.continueRun.compactions(message.metadata.compactions)}
              </span>
            ) : null}
            <Button
              size="sm"
              variant="ghost"
              disabled={chatBusy}
              onClick={() => void continueRun(message.metadata?.run_id)}
              data-testid={`message-continue-${message.id}`}
            >
              <FastForward size={12} />
              {chatBusy ? strings.chat.continueRun.running : strings.chat.continueRun.button}
            </Button>
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
        ) : (
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
        )}
        {isAssistant && !message.streaming && <VerificationBadge evidence={message.metadata?.verification} />}
        {canPreparePlan && <div className="mt-2 space-y-1">
          <Button type="button" size="sm" variant="secondary" disabled={chatBusy} onClick={preparePlan}>准备执行此计划</Button>
          {planActionMessage && <p role="status" className="text-xs text-ink-2">{planActionMessage}</p>}
        </div>}
        {isFailed && !codex && <FailedMessageFooter message={message} />}
        {codex && message.metadata?.partial && <p className="text-xs text-status-warning">未完成输出；请核对工作区后使用运行恢复入口。</p>}
        {codex && isAssistant && message.metadata?.usage_certainty !== "observed" && <p className="text-xs text-ink-2">用量{message.metadata?.usage_certainty === "recovery_interval" ? "包含恢复期间的观测区间，不能精确归属本轮" : "未知或不确定"}</p>}
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
