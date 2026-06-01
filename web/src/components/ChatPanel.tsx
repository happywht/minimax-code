/**
 * Chat container — header (session title + actions) on top, message
 * list in the middle. The composer lives in a sibling component so the
 * text input doesn't re-render the message list on every keystroke.
 */
import { useEffect, useMemo } from "react";
import { Loader2, MessageSquare, MoreHorizontal, RefreshCw } from "lucide-react";
import { MessageList } from "./MessageList";
import { useChat, useSessionStore } from "../stores";

export interface ChatPanelProps {
  testId?: string;
  onMenuClick?: () => void;
}

export function ChatPanel({ testId = "chat-panel", onMenuClick }: ChatPanelProps): JSX.Element {
  const sessions = useSessionStore((s) => s.sessions);
  const currentId = useSessionStore((s) => s.currentSessionId);
  const createSession = useSessionStore((s) => s.create);
  const refreshSessions = useSessionStore((s) => s.refresh);
  const status = useChat((s) => s.status);
  const error = useChat((s) => s.error);

  // Lazily bootstrap an empty session on first paint.
  useEffect(() => {
    if (sessions.length === 0) {
      void refreshSessions();
    }
  }, [sessions.length, refreshSessions]);

  const current = useMemo(
    () => sessions.find((s) => s.id === currentId) ?? null,
    [sessions, currentId],
  );

  const headerStatus = (() => {
    switch (status) {
      case "sending":
        return { label: "Sending", tone: "amber" };
      case "streaming":
        return { label: "Streaming", tone: "accent" };
      case "error":
        return { label: "Error", tone: "red" };
      default:
        return { label: "Ready", tone: "emerald" };
    }
  })();

  const toneClass: Record<string, string> = {
    amber: "text-amber-300 bg-amber-500/10 border-amber-500/30",
    accent: "text-minimax-accent bg-minimax-accent/10 border-minimax-accent/30",
    red: "text-red-300 bg-red-500/10 border-red-500/30",
    emerald: "text-emerald-300 bg-emerald-500/10 border-emerald-500/30",
  };

  return (
    <div
      data-testid={testId}
      className="relative flex flex-1 flex-col"
    >
      {/* Header */}
      <header className="flex items-center justify-between border-b border-minimax-border bg-minimax-bg/60 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <MessageSquare size={14} className="shrink-0 text-minimax-muted" />
          <h2
            data-testid="chat-header-title"
            className="truncate text-sm font-medium text-minimax-fg"
          >
            {current?.title ?? "New task"}
          </h2>
          <span
            data-testid="chat-header-status"
            className={
              "ml-2 inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] " +
              toneClass[headerStatus.tone]
            }
          >
            {(headerStatus.tone === "amber" || headerStatus.tone === "accent") && (
              <Loader2 size={10} className="animate-spin" />
            )}
            {headerStatus.label}
          </span>
          {error && (
            <span
              data-testid="chat-header-error"
              className="ml-2 truncate text-[10px] text-red-300"
              title={error}
            >
              {error}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            data-testid="chat-header-new"
            onClick={() => void createSession("New task")}
            className="rounded-md px-2 py-1 text-xs text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            title="New task"
          >
            + New
          </button>
          <button
            type="button"
            data-testid="chat-header-refresh"
            onClick={() => void refreshSessions()}
            className="rounded-md p-1.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            title="Refresh"
            aria-label="Refresh sessions"
          >
            <RefreshCw size={12} />
          </button>
          <button
            type="button"
            onClick={onMenuClick}
            className="rounded-md p-1.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            title="More"
            aria-label="More options"
          >
            <MoreHorizontal size={14} />
          </button>
        </div>
      </header>

      {/* Message list */}
      <MessageList />
    </div>
  );
}
