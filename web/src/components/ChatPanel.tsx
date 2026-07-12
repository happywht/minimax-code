/**
 * Chat container — header (session title + actions) on top, message
 * list in the middle. The composer lives in a sibling component so the
 * text input doesn't re-render the message list on every keystroke.
 *
 * v0.3.1: the session title in the header is now editable — double-click
 * or click the pencil icon to enter edit mode. Enter saves, Escape
 * cancels, blur saves.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, MessageSquare, MoreHorizontal, Pencil, RefreshCw, Search, X } from "lucide-react";
import { MessageList } from "./MessageList";
import { ProviderReadinessBanner } from "./ProviderReadinessBanner";
import { useChat, useSessionStore } from "../stores";

export interface ChatPanelProps {
  testId?: string;
  onMenuClick?: () => void;
  onOpenProviderSettings?: () => void;
  onOpenModelSettings?: () => void;
}

export function ChatPanel({
  testId = "chat-panel",
  onMenuClick,
  onOpenProviderSettings = () => {},
  onOpenModelSettings = () => {},
}: ChatPanelProps): JSX.Element {
  const sessions = useSessionStore((s) => s.sessions);
  const currentId = useSessionStore((s) => s.currentSessionId);
  const createSession = useSessionStore((s) => s.create);
  const creatingSession = useSessionStore((s) => s.creating);
  const refreshSessions = useSessionStore((s) => s.refresh);
  const renameSession = useSessionStore((s) => s.rename);
  const status = useChat((s) => s.status);
  const error = useChat((s) => s.error);

  // Inline title editing state
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // Search state
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

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

  // Focus the input when entering edit mode
  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  // Focus search input when search bar opens
  useEffect(() => {
    if (searchOpen) {
      searchRef.current?.focus();
    }
  }, [searchOpen]);

  const startEditing = useCallback(() => {
    if (!current) return;
    setEditValue(current.title);
    setEditing(true);
  }, [current]);

  const commitEdit = useCallback(() => {
    setEditing(false);
    const trimmed = editValue.trim();
    if (!current || !trimmed || trimmed === current.title) return;
    void renameSession(current.id, trimmed);
  }, [current, editValue, renameSession]);

  const cancelEdit = useCallback(() => {
    setEditing(false);
  }, []);

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
    red: "text-status-error bg-red-500/10 border-red-500/30",
    emerald: "text-emerald-300 bg-emerald-500/10 border-emerald-500/30",
  };

  return (
    <div
      data-testid={testId}
      className="relative flex min-h-0 flex-1 flex-col overflow-hidden"
    >
      {/* Header */}
      <header className="flex items-center justify-between border-b border-minimax-border bg-minimax-bg/60 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <MessageSquare size={14} className="shrink-0 text-minimax-muted" />
          {editing ? (
            <input
              ref={inputRef}
              data-testid="chat-header-title-input"
              aria-label="Session title"
              name="session-title"
              autoComplete="off"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") { e.preventDefault(); commitEdit(); }
                if (e.key === "Escape") { e.preventDefault(); cancelEdit(); }
              }}
              onBlur={commitEdit}
              className="min-w-0 rounded border border-minimax-accent bg-minimax-bg px-1.5 py-0.5 text-sm text-minimax-fg outline-none"
              style={{ width: `${Math.max(editValue.length * 8, 80)}px` }}
            />
          ) : (
            <h2
              data-testid="chat-header-title"
              className="truncate text-sm font-medium text-minimax-fg"
              onDoubleClick={startEditing}
              title="Double-click to rename"
            >
              {current?.title ?? "New task"}
            </h2>
          )}
          {current && !editing && (
            <button
              type="button"
              data-testid="chat-header-rename-btn"
              onClick={startEditing}
              className="shrink-0 rounded p-0.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
              title="Rename session"
              aria-label="Rename session"
            >
              <Pencil size={12} />
            </button>
          )}
          <span
            data-testid="chat-header-status"
            className={
              "ml-2 inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[11px] " +
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
              className="ml-2 truncate text-[11px] text-status-error"
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
            disabled={creatingSession}
            className="rounded-md px-2 py-1 text-xs text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg disabled:cursor-wait disabled:opacity-50"
            title="New task"
          >
            + New
          </button>
          <button
            type="button"
            data-testid="chat-search-toggle"
            onClick={() => {
              setSearchOpen((v) => !v);
              if (searchOpen) setSearchQuery("");
            }}
            className={
              "rounded-md p-1.5 hover:bg-minimax-border hover:text-minimax-fg " +
              (searchOpen ? "text-minimax-accent" : "text-minimax-muted")
            }
            title="Search messages"
            aria-label="Search messages"
          >
            <Search size={12} />
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

      {/* Search bar */}
      {searchOpen && (
        <div className="flex items-center gap-2 border-b border-minimax-border bg-minimax-panel/60 px-4 py-1.5">
          <Search size={12} className="shrink-0 text-minimax-muted" />
          <input
            ref={searchRef}
            data-testid="chat-search-input"
            type="text"
            aria-label="Search messages"
            name="message-search"
            autoComplete="off"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search messages…"
            className="flex-1 bg-transparent text-xs text-minimax-fg placeholder:text-minimax-muted focus:outline-none"
          />
          {searchQuery && (
            <span className="text-[11px] text-minimax-muted">
              filtering
            </span>
          )}
          <button
            type="button"
            data-testid="chat-search-clear"
            onClick={() => setSearchQuery("")}
            className="rounded p-0.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            aria-label="Clear search"
          >
            <X size={12} />
          </button>
        </div>
      )}

      {/* Message list */}
      <MessageList searchQuery={searchOpen ? searchQuery : undefined} />
      <ProviderReadinessBanner
        onOpenProviders={onOpenProviderSettings}
        onOpenModels={onOpenModelSettings}
      />
    </div>
  );
}
