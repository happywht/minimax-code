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
import {
  Archive,
  Download,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { MessageList } from "./MessageList";
import { ProviderReadinessBanner } from "./ProviderReadinessBanner";
import { useChat, useSessionStore } from "../../stores";
import { DropdownMenu, type DropdownMenuItem } from "../../ui/DropdownMenu";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { toast } from "../layout/ErrorBoundary";
import { strings } from "../../ui/strings";
import { DEFAULT_SESSION_TITLE } from "../../lib/defaultTitles";
import { exportSessionMarkdown } from "../../lib/exportSession";

export interface ChatPanelProps {
  testId?: string;
  onOpenProviderSettings?: () => void;
  onOpenModelSettings?: () => void;
}

export function ChatPanel({
  testId = "chat-panel",
  onOpenProviderSettings = () => {},
  onOpenModelSettings = () => {},
}: ChatPanelProps): JSX.Element {
  const sessions = useSessionStore((s) => s.sessions);
  const currentId = useSessionStore((s) => s.currentSessionId);
  const createSession = useSessionStore((s) => s.create);
  const creatingSession = useSessionStore((s) => s.creating);
  const refreshSessions = useSessionStore((s) => s.refresh);
  const renameSession = useSessionStore((s) => s.rename);
  const archiveSession = useSessionStore((s) => s.archive);
  const removeSession = useSessionStore((s) => s.remove);
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

  // Session-level actions behind the header "more" menu. All disabled
  // until a session is open; delete asks for confirmation first.
  const menuItems = useMemo<DropdownMenuItem[]>(() => {
    const disabled = !current;
    return [
      {
        id: "export",
        label: strings.chat.menu.exportMarkdown,
        icon: <Download size={14} />,
        disabled,
        onClick: () => {
          if (current) void exportSessionMarkdown(current.id);
        },
      },
      {
        id: "archive",
        label: strings.chat.menu.archive,
        icon: <Archive size={14} />,
        disabled: disabled || current.archived,
        onClick: () => {
          if (!current) return;
          void archiveSession(current.id).then(() => {
            toast.success(strings.chat.menu.archivedToast, current.title);
          });
        },
      },
      {
        id: "delete",
        label: strings.chat.menu.delete,
        icon: <Trash2 size={14} />,
        danger: true,
        disabled,
        onClick: () => {
          if (!current) return;
          void (async () => {
            const ok = await requestConfirmation({
              title: strings.chat.menu.deleteTitle(current.title),
              description: strings.chat.menu.deleteDesc,
              confirmLabel: strings.chat.menu.deleteLabel,
            });
            if (!ok) return;
            await removeSession(current.id);
            toast.success(strings.chat.menu.delete, current.title);
          })();
        },
      },
    ];
  }, [archiveSession, current, removeSession]);

  const headerStatus = (() => {
    switch (status) {
      case "sending":
        return { label: strings.chat.headerStatus.sending, tone: "amber" };
      case "streaming":
        return { label: strings.chat.headerStatus.streaming, tone: "accent" };
      case "error":
        return { label: strings.chat.headerStatus.error, tone: "red" };
      default:
        return { label: strings.chat.headerStatus.ready, tone: "emerald" };
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
              aria-label={strings.chat.header.titleInput}
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
              title={strings.chat.header.renameHint}
            >
              {current?.title ?? DEFAULT_SESSION_TITLE}
            </h2>
          )}
          {current && !editing && (
            <button
              type="button"
              data-testid="chat-header-rename-btn"
              onClick={startEditing}
              className="shrink-0 rounded p-0.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
              title={strings.chat.header.rename}
              aria-label={strings.chat.header.rename}
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
            onClick={() => void createSession(DEFAULT_SESSION_TITLE)}
            disabled={creatingSession}
            className="rounded-md px-2 py-1 text-xs text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg disabled:cursor-wait disabled:opacity-50"
            title={strings.chat.header.newTask}
          >
            {strings.chat.header.newButton}
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
            title={strings.chat.header.search}
            aria-label={strings.chat.header.search}
          >
            <Search size={12} />
          </button>
          <button
            type="button"
            data-testid="chat-header-refresh"
            onClick={() => void refreshSessions()}
            className="rounded-md p-1.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            title={strings.chat.header.refresh}
            aria-label={strings.chat.header.refreshSessions}
          >
            <RefreshCw size={12} />
          </button>
          <DropdownMenu
            align="right"
            testId="chat-header-menu"
            trigger={
              <button
                type="button"
                className="rounded-md p-1.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
                title={strings.chat.header.more}
                aria-label={strings.chat.header.moreOptions}
              >
                <MoreHorizontal size={14} />
              </button>
            }
            items={menuItems}
          />
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
            aria-label={strings.chat.header.search}
            name="message-search"
            autoComplete="off"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={strings.chat.header.searchPlaceholder}
            className="flex-1 bg-transparent text-xs text-minimax-fg placeholder:text-minimax-muted focus:outline-none"
          />
          {searchQuery && (
            <span className="text-[11px] text-minimax-muted">
              {strings.chat.header.filtering}
            </span>
          )}
          <button
            type="button"
            data-testid="chat-search-clear"
            onClick={() => setSearchQuery("")}
            className="rounded p-0.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            aria-label={strings.chat.header.clearSearch}
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
