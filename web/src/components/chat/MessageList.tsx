/**
 * Scrollable list of messages. Auto-scrolls to bottom on new messages
 * and during streaming, but only if the user is already near the bottom
 * (so they can scroll up to read history without being yanked back).
 *
 * v0.3.0 §2: completed sub-agent runs whose ``parent_session_id``
 * matches the current session are interleaved as
 * ``<SubAgentResultCard />`` instances after the chat messages that
 * triggered them.
 *
 * v0.3.1: optional ``searchQuery`` prop filters messages by case-insensitive
 * text match. When a query is active only matching messages are shown;
 * an empty query shows all.
 */
import { lazy, Suspense, useLayoutEffect, useMemo } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown } from "lucide-react";
import { useChat, useSessionStore, useSubAgentStore } from "../../stores";
import { SubAgentResultCard } from "../right-panel/SubAgentResultCard";
import { useMessageWindow } from "../../lib/useMessageWindow";
import { useSmartScroll } from "../../lib/useSmartScroll";
import type { Message } from "../../types/ipc";

const MessageItem = lazy(() =>
  import("./MessageItem").then((module) => ({ default: module.MessageItem })),
);

export interface MessageListProps {
  testId?: string;
  /** When non-empty, only messages whose text contains this string (case-insensitive) are shown. */
  searchQuery?: string;
}

type MessageListRow =
  | { type: "search-summary"; key: string }
  | { type: "load-more"; key: string }
  | { type: "message"; key: string; message: Message }
  | { type: "sub-agent-results"; key: string; runIds: string[] };

export function MessageList({ testId = "message-list", searchQuery }: MessageListProps): JSX.Element {
  const messages = useChat((s) => s.messages);
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const runs = useSubAgentStore((s) => s.runs);
  const scrollContentKey = useMemo(
    () =>
      [
        ...messages.map((m) => `${m.id}:${m.text.length}:${m.streaming ? 1 : 0}`),
        ...Object.values(runs).map((r) => `${r.run_id}:${r.status}:${r.updated_at}`),
      ].join("|"),
    [messages, runs],
  );
  const {
    containerRef: scrollRef,
    isFollowing,
    showNewContentButton,
    newContentCount,
    scrollToBottom,
  } = useSmartScroll<HTMLDivElement>({ contentKey: scrollContentKey, thresholdPx: 50 });

  // Completed sub-agent runs scoped to the current session, sorted
  // by finished time so the cards appear in the right order.
  const finishedRuns = useMemo(
    () =>
      Object.values(runs)
        .filter(
          (r) =>
            (r.status === "completed" || r.status === "failed") &&
            (sessionId == null || r.parent_session_id === sessionId),
        )
        .sort((a, b) => (a.finished_at ?? a.updated_at) - (b.finished_at ?? b.updated_at)),
    [runs, sessionId],
  );

  // Apply search filter when query is active — search text, tool_name,
  // and tool_args (JSON-stringified) for comprehensive coverage.
  const filtered = useMemo(() => {
    if (!searchQuery) return messages;
    const q = searchQuery.toLowerCase();
    return messages.filter((m) => {
      if (m.text.toLowerCase().includes(q)) return true;
      if (m.tool_name && m.tool_name.toLowerCase().includes(q)) return true;
      if (m.tool_args) {
        try {
          if (JSON.stringify(m.tool_args).toLowerCase().includes(q)) return true;
        } catch { /* non-serializable args — skip */ }
      }
      return false;
    });
  }, [messages, searchQuery]);

  const hasQuery = !!searchQuery;
  const isFiltered = hasQuery && filtered.length < messages.length;

  // Windowed rendering: only show the most recent messages by default.
  // "Load earlier" button expands the window.
  const { visible, hasMore, hiddenCount, loadMore } = useMessageWindow(filtered, 50);
  const rows = useMemo<MessageListRow[]>(() => {
    const next: MessageListRow[] = [];
    if (isFiltered) next.push({ type: "search-summary", key: "search-summary" });
    if (hasMore) next.push({ type: "load-more", key: `load-more-${hiddenCount}` });
    for (const message of visible) {
      next.push({ type: "message", key: `message-${message.id}`, message });
    }
    if (!hasQuery && finishedRuns.length > 0) {
      next.push({
        type: "sub-agent-results",
        key: `sub-agent-results-${finishedRuns.map((run) => run.run_id).join("-")}`,
        runIds: finishedRuns.map((run) => run.run_id),
      });
    }
    return next;
  }, [finishedRuns, hasMore, hasQuery, hiddenCount, isFiltered, visible]);
  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => estimateRowSize(rows[index]),
    overscan: 8,
    initialRect: { width: 720, height: 640 },
    ...(typeof ResizeObserver === "undefined"
      ? {
          observeElementRect: (_instance, cb) => {
            cb({ width: 720, height: 640 });
            return () => {};
          },
        }
      : {}),
    getItemKey: (index) => rows[index]?.key ?? index,
  });

  useLayoutEffect(() => {
    if (!isFollowing || rows.length === 0) return;
    rowVirtualizer.scrollToIndex(rows.length - 1, { align: "end" });
  }, [isFollowing, rowVirtualizer, rows.length, scrollContentKey]);

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden">
      <div
        ref={scrollRef}
        data-testid={testId}
        className="h-full min-h-0 overflow-y-auto px-4 pb-6 pt-4"
      >
      {messages.length === 0 && finishedRuns.length === 0 ? (
        <div
          data-testid="empty-state"
          className="mx-auto mt-16 max-w-md text-center"
        >
          <div className="mx-auto mb-3 h-10 w-10 rounded-xl bg-minimax-accent/20 text-minimax-accent flex items-center justify-center text-lg font-semibold">
            ✦
          </div>
          <h2 className="text-base font-medium text-minimax-fg">
            How can I help you today?
          </h2>
          <p className="mt-1 text-xs text-minimax-muted">
            Ask me to refactor code, explain a file, run a command, or set up
            a scheduled task.
          </p>
          <div className="mt-6 grid grid-cols-1 gap-2 text-left text-xs text-minimax-muted">
            <Suggestion text="Refactor src/foo.py to use dataclasses" />
            <Suggestion text="Explain what the IPC bridge does" />
            <Suggestion text="Set up a daily 9am test reminder" />
          </div>
        </div>
      ) : (
        <div
          data-testid="message-virtualizer"
          className="relative mx-auto w-full max-w-3xl"
          style={{ height: `${rowVirtualizer.getTotalSize()}px` }}
        >
          {rowVirtualizer.getVirtualItems().map((virtualRow) => {
            const row = rows[virtualRow.index];
            if (!row) return null;
            return (
            <div
              key={virtualRow.key}
              ref={rowVirtualizer.measureElement}
              data-index={virtualRow.index}
              data-testid={`message-window-row-${row.key}`}
              className="absolute left-0 top-0 w-full pb-3"
              style={{
                contentVisibility: "auto",
                containIntrinsicSize: "80px",
                transform: `translateY(${virtualRow.start}px)`,
              }}
            >
              <MessageListRowView
                row={row}
                filteredCount={filtered.length}
                totalCount={messages.length}
                hiddenCount={hiddenCount}
                onLoadMore={loadMore}
              />
            </div>
          );
          })}
        </div>
      )}
      </div>
      {showNewContentButton && (
        <button
          type="button"
          data-testid="scroll-to-bottom-btn"
          onClick={() => scrollToBottom("smooth")}
          className="absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-minimax-border bg-minimax-panel/95 px-2.5 py-1.5 text-[11px] text-minimax-fg shadow-lg backdrop-blur-sm transition-all duration-200 hover:-translate-y-0.5 hover:bg-minimax-border"
          title="Jump to latest message"
        >
          <ChevronDown size={14} className="text-minimax-muted" />
          {newContentCount > 0 ? `${newContentCount} new` : "Latest"}
        </button>
      )}
    </div>
  );
}

function MessageListRowView({
  row,
  filteredCount,
  totalCount,
  hiddenCount,
  onLoadMore,
}: {
  row: MessageListRow;
  filteredCount: number;
  totalCount: number;
  hiddenCount: number;
  onLoadMore: () => void;
}): JSX.Element {
  if (row.type === "search-summary") {
    return (
      <div
        data-testid="chat-search-summary"
        className="rounded-md border border-minimax-border bg-minimax-panel px-3 py-1.5 text-center text-xs text-minimax-muted"
      >
        Showing {filteredCount} of {totalCount} messages
      </div>
    );
  }

  if (row.type === "load-more") {
    return (
      <button
        type="button"
        data-testid="load-earlier-messages"
        onClick={onLoadMore}
        className="w-full rounded-md border border-minimax-border bg-minimax-panel px-3 py-2 text-center text-xs text-minimax-muted hover:border-minimax-accent/40 hover:text-minimax-fg"
      >
        ↑ Load {Math.min(hiddenCount, 50)} earlier messages ({hiddenCount} hidden)
      </button>
    );
  }

  if (row.type === "sub-agent-results") {
    return (
      <div
        data-testid="sub-agent-results"
        className="flex flex-col gap-1 border-t border-minimax-border/40 pt-2"
      >
        {row.runIds.map((runId) => (
          <SubAgentResultCard key={runId} runId={runId} />
        ))}
      </div>
    );
  }

  return (
    <Suspense fallback={<MessageRowFallback />}>
      <MessageItem message={row.message} />
    </Suspense>
  );
}

function MessageRowFallback(): JSX.Element {
  return (
    <div className="space-y-2 py-2" aria-busy="true">
      <div className="h-3 w-2/3 animate-pulse rounded bg-minimax-border/70" />
      <div className="h-3 w-1/2 animate-pulse rounded bg-minimax-border/50" />
    </div>
  );
}

function estimateRowSize(row: MessageListRow | undefined): number {
  if (!row) return 96;
  if (row.type === "search-summary") return 40;
  if (row.type === "load-more") return 44;
  if (row.type === "sub-agent-results") return 96;
  const textLength = row.message.text.length;
  if (row.message.role === "user") return Math.min(180, 48 + textLength / 4);
  if (row.message.role === "tool") return 54;
  return Math.min(260, 92 + textLength / 5);
}

function Suggestion({ text }: { text: string }): JSX.Element {
  return (
    <button
      type="button"
      className="rounded-md border border-minimax-border bg-minimax-panel px-3 py-2 text-left text-minimax-fg hover:border-minimax-accent/40"
      onClick={() => {
        // The composer reads its value from local state, so we just
        // fire a custom event the MessageInput listens for. This keeps
        // the components decoupled.
        window.dispatchEvent(new CustomEvent("minimax:suggestion", { detail: text }));
      }}
    >
      {text}
    </button>
  );
}
