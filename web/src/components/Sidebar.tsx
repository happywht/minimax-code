/**
 * Left sidebar — 240px wide. Contains:
 *   - Brand mark
 *   - "New task" button
 *   - Primary nav (skills, scheduler, mobile, etc.)
 *   - Session history (loaded from sessionStore) — redesigned to
 *     show status dot + truncated title + relative timestamp, in
 *     the style of MiniMax Code's conversation list.
 *   - Footer: UserBadge
 *
 * The "skills" entry is a top-level view: clicking it tells the
 * parent to switch to the SkillsPanel. Other primary nav items
 * (定时任务 / Agents / 已归档) still drive the session filter.
 */
import { useEffect } from "react";
import {
  Bot,
  CalendarClock,
  History,
  Plug,
  Plus,
  Settings as SettingsIcon,
  Sparkles,
  Wrench,
} from "lucide-react";
import { NavItem } from "./NavItem";
import { UserBadge } from "./UserBadge";
import { useSessionStore, type SessionFilter } from "../stores";
import { formatRelative } from "../lib/time";
import { APP_VERSION } from "../version";

export interface SidebarProps {
  testId?: string;
  onMobileClick?: () => void;
  /** Current top-level view — drives which NavItem is highlighted. */
  view?: "chat" | "skills" | "settings" | "preview";
  /** Toggle between top-level views. */
  onViewChange?: (v: "chat" | "skills" | "settings" | "preview") => void;
}

type PrimaryNavId = SessionFilter | "skills" | "agents";

const NAV_ITEMS: Array<{
  id: PrimaryNavId;
  label: string;
  icon: JSX.Element;
  group: "primary" | "history";
}> = [
  { id: "skills", label: "技能", icon: <Wrench size={14} />, group: "primary" },
  { id: "scheduled", label: "定时任务", icon: <CalendarClock size={14} />, group: "primary" },
  { id: "history", label: "任务历史", icon: <History size={14} />, group: "primary" },
  { id: "agents", label: "Agents", icon: <Bot size={14} />, group: "primary" },
  { id: "archived", label: "已归档", icon: <Plug size={14} />, group: "history" },
];

const MAX_TITLE_LEN = 24;

/** Pick a Tailwind background class for the session status dot. */
function statusDotClass(
  session: { archived: boolean; updated_at: number },
  now: number = Date.now(),
): string {
  if (session.archived) return "bg-minimax-muted";
  // Stale (untouched for 24h+) is still "active" but we surface it
  // with the muted hue. (Future: real backend status will replace
  // this heuristic.)
  if (now - session.updated_at > 24 * 60 * 60 * 1000) {
    return "bg-minimax-muted";
  }
  return "bg-minimax-accent";
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
  const currentId = useSessionStore((s) => s.currentSessionId);
  const setCurrent = useSessionStore((s) => s.setCurrent);
  const createSession = useSessionStore((s) => s.create);
  const refresh = useSessionStore((s) => s.refresh);

  useEffect(() => {
    if (sessions.length === 0) {
      void refresh();
    }
  }, [sessions.length, refresh]);

  const visibleSessions = sessions
    .filter((s) => (filter === "archived" ? s.archived : !s.archived))
    .sort((a, b) => b.updated_at - a.updated_at)
    .slice(0, 30);

  return (
    <aside
      data-testid={testId}
      className="flex w-60 shrink-0 flex-col border-r border-minimax-border bg-minimax-panel"
    >
      {/* Brand */}
      <div className="flex items-center gap-2 border-b border-minimax-border px-3 py-3">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-minimax-accent/20 text-minimax-accent">
          <Sparkles size={14} />
        </div>
        <div className="min-w-0">
          <h1
            data-testid="sidebar-brand"
            className="truncate text-sm font-semibold text-minimax-fg"
          >
            MiniMax Code
          </h1>
          <p className="truncate text-[10px] text-minimax-muted">
            AI coding agent · v{APP_VERSION}
          </p>
        </div>
      </div>

      {/* New task */}
      <div className="px-3 py-3">
        <button
          type="button"
          data-testid="sidebar-new-task"
          onClick={() => void createSession("New task")}
          className="flex w-full items-center gap-2 rounded-md border border-minimax-border bg-minimax-bg/40 px-2.5 py-1.5 text-sm text-minimax-fg hover:border-minimax-accent/50"
        >
          <Plus size={14} className="text-minimax-accent" />
          <span>新任务</span>
        </button>
      </div>

      {/* Primary nav */}
      <nav className="space-y-0.5 px-2" data-testid="sidebar-nav">
        {NAV_ITEMS.filter((n) => n.group === "primary").map((n) => (
          <NavItem
            key={n.id}
            icon={n.icon}
            label={n.label}
            selected={
              n.id === "skills"
                ? view === "skills"
                : view === "chat" && filter === n.id
            }
            onClick={() => {
              if (n.id === "skills") {
                onViewChange?.("skills");
                // Keep the legacy "skills" filter in sync so older
                // tests / any third-party consumer that reads the
                // filter value keeps working.
                setFilter("skills");
                return;
              }
              onViewChange?.("chat");
              setFilter(n.id as SessionFilter);
            }}
            testId={`sidebar-nav-${n.id}`}
          />
        ))}
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

      {/* Session list (history) — redesigned row format. */}
      <div className="mt-4 flex min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between px-4 pt-1 text-[10px] uppercase tracking-wider text-minimax-muted">
          <span>任务历史</span>
          <span data-testid="sidebar-session-count">{visibleSessions.length}</span>
        </div>
        <ul
          data-testid="sidebar-session-list"
          className="mt-1 flex-1 space-y-0.5 overflow-y-auto px-2"
          style={{ maxHeight: "60vh" }}
        >
          {visibleSessions.length === 0 && (
            <li className="px-2 py-2 text-[11px] italic text-minimax-muted">
              No sessions yet — start a new task ↑
            </li>
          )}
          {visibleSessions.map((s) => (
            <li
              key={s.id}
              data-testid={`sidebar-session-row-${s.id}`}
            >
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
                  <span
                    data-testid={`sidebar-session-time-${s.id}`}
                    className="ml-1 shrink-0 text-[9px] text-minimax-muted"
                  >
                    {formatRelative(s.updated_at)}
                  </span>
                }
                selected={s.id === currentId}
                onClick={() => setCurrent(s.id)}
                // Preserve the legacy testId so the existing
                // sidebar.test.tsx keeps working.
                testId={`sidebar-session-${s.id}`}
              />
            </li>
          ))}
        </ul>
      </div>

      {/* Mobile pairing link */}
      <div className="px-2 py-2">
        <NavItem
          icon={<Plug size={14} />}
          label="连接手机"
          onClick={onMobileClick}
          testId="sidebar-mobile"
        />
      </div>

      {/* Footer user badge */}
      <div className="border-t border-minimax-border p-2">
        <UserBadge name="Demo User" email="demo@minimax.code" plan="Max Plan" />
      </div>
    </aside>
  );
}
