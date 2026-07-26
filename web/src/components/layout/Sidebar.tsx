/**
 * Left sidebar — 240px wide. Contains:
 *   - Brand mark
 *   - "New task" button
 *   - Primary nav
 *   - Session history with search
 *   - Footer: UserBadge
 */
import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  CalendarClock,
  GitBranch,
  History,
  LayoutDashboard,
  Plug,
  Plus,
  Search,
  Settings as SettingsIcon,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";
import { NavItem } from "./NavItem";
import { UserBadge } from "./UserBadge";
import { SkeletonLine } from "./Skeleton";
import { Button, IconButton, Input } from "../../ui";
import { typedIPC } from "../../ipc";
import { useSessionStore, type SessionFilter, type SessionMeta } from "../../stores";
import { formatRelative } from "../../lib/time";
import { APP_VERSION } from "../../version";

export interface SidebarProps {
  testId?: string;
  onMobileClick?: () => void;
  /** Current top-level view — drives which NavItem is highlighted. */
  view?: "chat" | "skills" | "settings" | "scheduled" | "agents" | "preview";
  /** Toggle between top-level views. */
  onViewChange?: (v: "chat" | "skills" | "settings" | "scheduled" | "agents" | "preview") => void;
}

type PrimaryNavId = SessionFilter | "skills" | "agents" | "chat";

const NAV_ITEMS: Array<{
  id: PrimaryNavId;
  label: string;
  icon: JSX.Element;
  group: "primary" | "history";
}> = [
  { id: "chat", label: "工作台", icon: <LayoutDashboard size={14} />, group: "primary" },
  { id: "skills", label: "技能", icon: <Wrench size={14} />, group: "primary" },
  { id: "scheduled", label: "定时任务", icon: <CalendarClock size={14} />, group: "primary" },
  { id: "history", label: "任务历史", icon: <History size={14} />, group: "primary" },
  { id: "agents", label: "Agents", icon: <Bot size={14} />, group: "primary" },
  { id: "archived", label: "已归档", icon: <Plug size={14} />, group: "history" },
];

const MAX_TITLE_LEN = 24;

function statusDotClass(
  session: { archived: boolean; updated_at: number },
  now: number = Date.now(),
): string {
  if (session.archived) return "bg-ink-2";
  if (now - session.updated_at > 24 * 60 * 60 * 1000) {
    return "bg-ink-2";
  }
  return "bg-accent";
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max - 1) + "…";
}

export function Sidebar({
  testId = "sidebar",
  onMobileClick,
  view = "chat",
  onViewChange,
}: SidebarProps): JSX.Element {
  const filter = useSessionStore((s) => s.filter);
  const setFilter = useSessionStore((s) => s.setFilter);
  const sessions = useSessionStore((s) => s.sessions);
  const loading = useSessionStore((s) => s.loading);
  const currentId = useSessionStore((s) => s.currentSessionId);
  const setCurrent = useSessionStore((s) => s.setCurrent);
  const createSession = useSessionStore((s) => s.create);
  const creatingSession = useSessionStore((s) => s.creating);
  const createWorktree = useSessionStore((s) => s.createWorktree);
  const refresh = useSessionStore((s) => s.refresh);
  const mergeSessions = useSessionStore((s) => s.mergeSessions);
  const [historyQuery, setHistoryQuery] = useState("");
  const [remoteSearchSessions, setRemoteSearchSessions] = useState<SessionMeta[] | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);

  useEffect(() => {
    if (sessions.length === 0) {
      void refresh();
    }
  }, [sessions.length, refresh]);

  useEffect(() => {
    const q = historyQuery.trim();
    if (!q) {
      setRemoteSearchSessions(null);
      setSearchLoading(false);
      return;
    }
    let cancelled = false;
    setSearchLoading(true);
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const result = await typedIPC.listSessions({
            archived: filter === "archived",
            search: q,
            limit: 50,
          });
          if (cancelled) return;
          setRemoteSearchSessions(result.sessions);
          mergeSessions(result.sessions);
        } catch {
          if (!cancelled) setRemoteSearchSessions([]);
        } finally {
          if (!cancelled) setSearchLoading(false);
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [filter, historyQuery, mergeSessions]);

  const visibleSessions = useMemo(() => {
    const q = historyQuery.trim().toLowerCase();
    const source = q && remoteSearchSessions ? remoteSearchSessions : sessions;
    return source
      .filter((s) => (filter === "archived" ? s.archived : !s.archived))
      .filter((s) => {
        if (!q) return true;
        return s.title.toLowerCase().includes(q) || s.id.toLowerCase().includes(q);
      })
      .sort((a, b) => b.updated_at - a.updated_at)
      .slice(0, 30);
  }, [filter, historyQuery, remoteSearchSessions, sessions]);

  return (
    <aside
      data-testid={testId}
      className="flex h-full w-60 shrink-0 flex-col border-r border-line bg-surface-1"
    >
      {/* Brand */}
      <div className="flex items-center gap-2.5 border-b border-line px-3 py-3">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-subtle text-accent">
          <Sparkles size={14} />
        </div>
        <div className="min-w-0">
          <h1
            data-testid="sidebar-brand"
            className="truncate text-sm font-semibold text-ink-0"
          >
            MiniMax Code
          </h1>
          <p className="truncate text-[11px] text-ink-2">AI coding agent · v{APP_VERSION}</p>
        </div>
      </div>

      {/* New task */}
      <div className="flex gap-1.5 px-3 py-3">
        <Button
          size="sm"
          icon={<Plus size={14} />}
          className="flex-1"
          loading={creatingSession}
          disabled={creatingSession}
          onClick={() => void createSession("New task")}
          data-testid="sidebar-new-task"
        >
          新任务
        </Button>
        <IconButton
          aria-label="Create isolated worktree task"
          title="Create isolated worktree task"
          onClick={() => void createWorktree("Worktree task", "HEAD")}
          data-testid="sidebar-new-worktree-task"
        >
          <GitBranch size={14} />
        </IconButton>
      </div>

      {/* Primary nav */}
      <nav className="space-y-0.5 px-2" data-testid="sidebar-nav">
        {NAV_ITEMS.filter((n) => n.group === "primary").map((n) => (
          <NavItem
            key={n.id}
            icon={n.icon}
            label={n.label}
            selected={
              n.id === "chat"
                ? view === "chat" && filter === "all"
                : n.id === "skills"
                ? view === "skills"
                : n.id === "scheduled" || n.id === "agents"
                ? view === n.id
                : view === "chat" && filter === n.id
            }
            onClick={() => {
              if (n.id === "chat") {
                onViewChange?.("chat");
                setFilter("all");
                return;
              }
              if (n.id === "skills") {
                onViewChange?.("skills");
                setFilter("skills");
                return;
              }
              if (n.id === "scheduled" || n.id === "agents") {
                onViewChange?.(n.id);
                setFilter("all");
                return;
              }
              onViewChange?.("chat");
              setFilter(n.id as SessionFilter);
            }}
            testId={`sidebar-nav-${n.id}`}
          />
        ))}
        <div className="pt-2">
          {NAV_ITEMS.filter((n) => n.group === "history").map((n) => (
            <NavItem
              key={n.id}
              icon={n.icon}
              label={n.label}
              selected={view === "chat" && filter === n.id}
              onClick={() => {
                onViewChange?.("chat");
                setFilter(n.id as SessionFilter);
              }}
              testId={`sidebar-nav-${n.id}`}
            />
          ))}
        </div>
        {onViewChange && (
          <NavItem
            icon={<SettingsIcon size={14} />}
            label="设置"
            selected={view === "settings"}
            onClick={() => onViewChange("settings")}
            testId="sidebar-nav-settings"
          />
        )}
      </nav>

      {/* Session list */}
      <div className="mt-3 flex min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between px-4 pt-1 text-[11px] font-medium uppercase tracking-wide text-ink-2">
          <span>任务历史</span>
          <span data-testid="sidebar-session-count">{visibleSessions.length}</span>
        </div>
        <div className="px-2 pt-2">
          <label className="relative block">
            <Search
              size={12}
              className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-ink-2"
            />
            <Input
              data-testid="sidebar-session-search"
              aria-label="Search task history"
              name="session-history-search"
              autoComplete="off"
              value={historyQuery}
              onChange={(event) => setHistoryQuery(event.target.value)}
              placeholder="Search history…"
              fieldSize="sm"
              className="pr-7"
            />
            {historyQuery && (
              <IconButton
                size="sm"
                aria-label="Clear history search"
                onClick={() => setHistoryQuery("")}
                data-testid="sidebar-session-search-clear"
                className="absolute right-1 top-1/2 -translate-y-1/2"
              >
                <X size={11} />
              </IconButton>
            )}
          </label>
        </div>
        <ul
          data-testid="sidebar-session-list"
          className="mt-1 flex-1 space-y-0.5 overflow-y-auto px-2 pb-2"
        >
          {(loading || searchLoading) && visibleSessions.length === 0 && (
            <>
              {Array.from({ length: 5 }, (_, i) => (
                <li key={`skel-${i}`} className="flex items-center gap-2 px-2 py-1.5">
                  <div className="h-2 w-2 rounded-sm animate-shimmer" />
                  <SkeletonLine className="h-3.5 flex-1" />
                </li>
              ))}
            </>
          )}
          {!loading && !searchLoading && visibleSessions.length === 0 && (
            <li
              data-testid="sidebar-session-empty"
              className="px-2 py-2 text-[11px] italic text-ink-2"
            >
              {historyQuery.trim()
                ? "No matching sessions."
                : "No sessions yet — start a new task ↑"}
            </li>
          )}
          {visibleSessions.map((s) => (
            <li key={s.id} data-testid={`sidebar-session-row-${s.id}`}>
              <NavItem
                icon={
                  <span
                    aria-hidden
                    data-testid={`sidebar-session-dot-${s.id}`}
                    data-status={s.archived ? "archived" : "active"}
                    className={`block h-2 w-2 rounded-sm ${statusDotClass(s)}`}
                  />
                }
                label={truncate(s.title || "(untitled)", MAX_TITLE_LEN)}
                trailing={
                  <span className="ml-1 flex shrink-0 items-center gap-1">
                    {s.workspace_mode === "worktree" && (
                      <span
                        data-testid={`sidebar-session-workspace-${s.id}`}
                        className="rounded border border-accent/30 bg-accent-subtle px-1 py-0.5 text-[11px] text-accent"
                        title={s.workspace_path ?? "Worktree"}
                      >
                        WT
                      </span>
                    )}
                    <span
                      data-testid={`sidebar-session-time-${s.id}`}
                      className="text-[11px] text-ink-2"
                    >
                      {formatRelative(s.updated_at)}
                    </span>
                  </span>
                }
                selected={s.id === currentId}
                onClick={() => setCurrent(s.id)}
                testId={`sidebar-session-${s.id}`}
              />
            </li>
          ))}
        </ul>
      </div>

      {/* Footer */}
      <div className="shrink-0 border-t border-line bg-surface-1">
        <div className="px-2 py-2">
          <NavItem
            icon={<Plug size={14} />}
            label="连接手机"
            onClick={onMobileClick}
            testId="sidebar-mobile"
          />
        </div>
        <div className="border-t border-line p-2">
          <UserBadge name="本地用户" email="数据仅保存在本机" plan="个人版" />
        </div>
      </div>
    </aside>
  );
}
