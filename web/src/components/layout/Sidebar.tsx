/**
 * Left sidebar — 240px wide. Contains:
 *   - Brand mark
 *   - "新任务" button with project selector
 *   - Primary nav
 *   - Session history grouped by project
 *   - Footer: UserBadge
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ArchiveRestore,
  Bot,
  CalendarClock,
  ChevronDown,
  ChevronRight,
  Folder,
  GitBranch,
  History,
  Inbox,
  LayoutDashboard,
  MoreHorizontal,
  Pencil,
  Plus,
  Plug,
  Search,
  Settings as SettingsIcon,
  Sparkles,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import { NavItem } from "./NavItem";
import { SessionRow } from "./SessionRow";
import { UserBadge } from "./UserBadge";
import { SkeletonLine } from "./Skeleton";
import { Button, Checkbox, IconButton, Input, Modal, DropdownMenu } from "../../ui";
import { strings } from "../../ui/strings";
import { DEFAULT_SESSION_TITLE, DEFAULT_WORKTREE_TITLE } from "../../lib/defaultTitles";
import { typedIPC } from "../../ipc";
import { useChat, useSessionStore, type SessionMeta } from "../../stores";
import type { Project } from "../../types/ipc";
import { APP_VERSION } from "../../version";

export interface SidebarProps {
  testId?: string;
  onMobileClick?: () => void;
  /** Current top-level view — drives which NavItem is highlighted. */
  view?: "chat" | "skills" | "settings" | "scheduled" | "workflows" | "webhooks" | "agents" | "preview" | "artifacts" | "attachments" | "office" | "research" | "run-artifacts";
  /** Toggle between top-level views. */
  onViewChange?: (v: "chat" | "skills" | "settings" | "scheduled" | "workflows" | "webhooks" | "agents" | "preview" | "artifacts" | "attachments" | "office" | "research" | "run-artifacts") => void;
}

type NavigationGroup = "projects" | "work" | "deliveries" | "automation";

function projectIcon(project: Project, size = 14): JSX.Element {
  if (project.id === "inbox") return <Inbox size={size} />;
  if (project.archived) return <Archive size={size} />;
  return <Folder size={size} />;
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
  const projects = useSessionStore((s) => s.projects);
  const loading = useSessionStore((s) => s.loading);
  const currentId = useSessionStore((s) => s.currentSessionId);
  const currentProjectId = useSessionStore((s) => s.currentProjectId);
  const setCurrent = useSessionStore((s) => s.setCurrent);
  const setCurrentProject = useSessionStore((s) => s.setCurrentProject);
  const createSession = useSessionStore((s) => s.create);
  const creatingSession = useSessionStore((s) => s.creating);
  const createWorktree = useSessionStore((s) => s.createWorktree);
  const refresh = useSessionStore((s) => s.refresh);
  const mergeSessions = useSessionStore((s) => s.mergeSessions);
  const createProject = useSessionStore((s) => s.createProject);
  const updateProject = useSessionStore((s) => s.updateProject);
  const deleteProject = useSessionStore((s) => s.deleteProject);
  const archiveProject = useSessionStore((s) => s.archiveProject);
  const unarchiveProject = useSessionStore((s) => s.unarchiveProject);
  const expandedProjectIds = useSessionStore((s) => s.expandedProjectIds);
  const toggleProjectExpanded = useSessionStore((s) => s.toggleProjectExpanded);
  const selectedSessionIds = useSessionStore((s) => s.selectedSessionIds);
  const toggleSessionSelection = useSessionStore((s) => s.toggleSessionSelection);
  const clearSessionSelection = useSessionStore((s) => s.clearSessionSelection);
  const selectAllVisible = useSessionStore((s) => s.selectAllVisible);
  const batchArchiveSessions = useSessionStore((s) => s.batchArchiveSessions);
  const batchMoveToProject = useSessionStore((s) => s.batchMoveToProject);
  const [navigationGroup, setNavigationGroup] = useState<NavigationGroup | null>("projects");
  useEffect(() => {
    if (["artifacts", "attachments", "office", "research", "run-artifacts"].includes(view)) setNavigationGroup("deliveries");
    else if (["scheduled", "workflows", "webhooks"].includes(view)) setNavigationGroup("automation");
    else if (["skills", "agents", "preview"].includes(view)) setNavigationGroup("work");
  }, [view]);
  const [historyQuery, setHistoryQuery] = useState("");
  const [batchMoveOpen, setBatchMoveOpen] = useState(false);
  const [remoteSearchSessions, setRemoteSearchSessions] = useState<SessionMeta[] | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [stats, setStats] = useState<{ total_sessions: number; total_messages: number } | null>(null);
  const [statsError, setStatsError] = useState(false);
  // Primitive selectors only — streaming chunks mutate messages in place,
  // so we key off the count and the run status instead of the array ref.
  const chatMessageCount = useChat((s) => s.messages.length);
  const chatStatus = useChat((s) => s.status);

  // Modal state for project operations.
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  // v1.3.0: optional absolute workspace root anchoring the new project.
  const [createRoot, setCreateRoot] = useState("");
  const [createValidate, setCreateValidate] = useState("");
  const [renameTarget, setRenameTarget] = useState<Project | null>(null);
  const [renameName, setRenameName] = useState("");
  const [settingsTarget, setSettingsTarget] = useState<Project | null>(null);
  const [settingsRoot, setSettingsRoot] = useState("");
  const [settingsValidate, setSettingsValidate] = useState("");
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);
  const createInputRef = useRef<HTMLInputElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (sessions.length === 0) {
      void refresh();
    }
  }, [sessions.length, refresh]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await typedIPC.sessionStats();
        if (!cancelled) {
          setStats(result);
          setStatsError(false);
        }
      } catch {
        // Footer stats are non-critical, but a silent missing footer
        // reads as "zero sessions" — show the inline fallback row.
        if (!cancelled) setStatsError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessions.length, chatMessageCount, chatStatus]);

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

  useEffect(() => {
    clearSessionSelection();
  }, [filter, historyQuery, clearSessionSelection]);

  const filteredSessions = useMemo(() => {
    const q = historyQuery.trim().toLowerCase();
    const source = q && remoteSearchSessions ? remoteSearchSessions : sessions;
    return source
      .filter((s) => (filter === "archived" ? s.archived : !s.archived))
      .filter((s) => {
        if (!q) return true;
        return s.title.toLowerCase().includes(q) || s.id.toLowerCase().includes(q);
      })
      .sort((a, b) => b.updated_at - a.updated_at);
  }, [filter, historyQuery, remoteSearchSessions, sessions]);

  const searchMode = Boolean(historyQuery.trim());

  const { inboxProject, activeProjects, archivedProjects, projectById } = useMemo(() => {
    const inbox = projects.find((p) => p.id === "inbox") ?? {
      id: "inbox",
      name: "收件箱",
      description: "",
      root_path: "",
      validate_command: "",
      archived: false,
      created_at: Date.now(),
      updated_at: Date.now(),
    };
    const active = projects.filter((p) => p.id !== "inbox" && !p.archived).sort((a, b) => b.updated_at - a.updated_at);
    const archived = projects.filter((p) => p.id !== "inbox" && p.archived).sort((a, b) => b.updated_at - a.updated_at);
    const byId = new Map<string, Project>();
    for (const p of [inbox, ...active, ...archived]) byId.set(p.id, p);
    return { inboxProject: inbox, activeProjects: active, archivedProjects: archived, projectById: byId };
  }, [projects]);

  const currentProject = projectById.get(currentProjectId ?? "inbox") ?? inboxProject;

  const sessionsByProject = useMemo(() => {
    const map = new Map<string, SessionMeta[]>();
    for (const s of filteredSessions) {
      const pid = s.project_id ?? "inbox";
      const list = map.get(pid) ?? [];
      list.push(s);
      map.set(pid, list);
    }
    return map;
  }, [filteredSessions]);

  // When searching, auto-expand projects that have matching sessions so results are visible.
  const effectiveExpandedIds = useMemo(() => {
    const base = new Set(expandedProjectIds);
    if (!searchMode) return base;
    for (const pid of sessionsByProject.keys()) {
      if ((sessionsByProject.get(pid)?.length ?? 0) > 0) {
        base.add(pid);
      }
    }
    return base;
  }, [expandedProjectIds, searchMode, sessionsByProject]);

  // Project creation
  const handleCreateProject = async () => {
    const name = createName.trim();
    if (!name) return;
    const rootPath = createRoot.trim() || undefined;
    const validateCommand = createValidate.trim() || undefined;
    const project = await createProject(name, undefined, rootPath, validateCommand);
    if (project) {
      setCurrentProject(project.id);
      setCreateOpen(false);
      setCreateName("");
      setCreateRoot("");
      setCreateValidate("");
    }
  };

  // Project rename
  const startRename = (project: Project) => {
    setRenameTarget(project);
    setRenameName(project.name);
  };

  const handleRenameProject = async () => {
    if (!renameTarget) return;
    const name = renameName.trim();
    if (!name || name === renameTarget.name) {
      setRenameTarget(null);
      return;
    }
    if (await updateProject(renameTarget.id, { name })) setRenameTarget(null);
  };

  const startProjectSettings = (project: Project) => {
    setSettingsTarget(project); setSettingsRoot(project.root_path ?? "");
    setSettingsValidate(project.validate_command ?? "");
  };
  const saveProjectSettings = async () => {
    if (!settingsTarget || settingsSaving) return;
    const changes: { root_path?: string; validate_command?: string } = {};
    if (settingsRoot.trim() !== (settingsTarget.root_path ?? "")) changes.root_path = settingsRoot.trim();
    if (settingsValidate.trim() !== (settingsTarget.validate_command ?? "")) changes.validate_command = settingsValidate.trim();
    if (!Object.keys(changes).length) { setSettingsTarget(null); return; }
    setSettingsSaving(true);
    try {
      if (await updateProject(settingsTarget.id, changes)) setSettingsTarget(null);
    } finally { setSettingsSaving(false); }
  };

  // Project deletion
  const startDelete = (project: Project) => setDeleteTarget(project);

  const handleDeleteProject = async () => {
    if (!deleteTarget) return;
    await deleteProject(deleteTarget.id);
    if (currentProjectId === deleteTarget.id) {
      setCurrentProject("inbox");
    }
    setDeleteTarget(null);
  };

  const projectMenuItems = (project: Project) => [
    {
      id: "settings",
      label: "项目验证设置",
      icon: <SettingsIcon size={14} />,
      onClick: () => startProjectSettings(project),
    },
    {
      id: "rename",
      label: "重命名",
      icon: <Pencil size={14} />,
      onClick: () => startRename(project),
    },
    project.archived
      ? {
          id: "unarchive",
          label: "取消归档",
          icon: <ArchiveRestore size={14} />,
          onClick: () => void unarchiveProject(project.id),
        }
      : {
          id: "archive",
          label: "归档项目",
          icon: <Archive size={14} />,
          onClick: () => void archiveProject(project.id),
        },
    {
      id: "delete",
      label: "删除项目",
      icon: <Trash2 size={14} />,
      danger: true,
      onClick: () => startDelete(project),
    },
  ];

  const projectSelectorItems = useMemo(() => {
    const all = [inboxProject, ...activeProjects, ...archivedProjects];
    return all.map((p) => ({
      id: p.id,
      label: p.name,
      icon: projectIcon(p),
      onClick: () => setCurrentProject(p.id),
    }));
  }, [inboxProject, activeProjects, archivedProjects, setCurrentProject]);

  const renderProjectHeader = (project: Project, sessionsInProject: SessionMeta[]) => {
    const expanded = effectiveExpandedIds.has(project.id);
    const isInbox = project.id === "inbox";
    return (
      <div
        key={`project-header-${project.id}`}
        data-testid={`sidebar-project-${project.id}`}
        className="group flex items-center justify-between rounded-md px-2 py-1.5 hover:bg-surface-2"
      >
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          onClick={() => toggleProjectExpanded(project.id)}
          aria-expanded={expanded}
        >
          <span className="text-ink-2">{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
          <span className="text-ink-1">{projectIcon(project)}</span>
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink-0">{project.name}</span>
          <span className="rounded bg-surface-3 px-1.5 py-0 text-[11px] text-ink-2">{sessionsInProject.length}</span>
        </button>
        {!isInbox && (
          <DropdownMenu
            align="right"
            testId={`sidebar-project-menu-${project.id}`}
            trigger={
              <IconButton
                size="sm"
                aria-label={strings.layout.sidebar.projectOptions}
                title={strings.layout.sidebar.projectOptions}
                onClick={(e) => e.stopPropagation()}
              >
                <MoreHorizontal size={14} />
              </IconButton>
            }
            items={projectMenuItems(project)}
          />
        )}
      </div>
    );
  };

  const handleSessionClick = (s: SessionMeta) => {
    setCurrentProject(s.project_id ?? "inbox");
    setCurrent(s.id);
  };

  // ── Roving tabindex for the session list (R37, M6 keyboard a11y) ──
  // Exactly one row is a tab stop (tabIndex=0); ArrowUp/Down/Home/End
  // move it. Rows are queried from the DOM so navigation follows the
  // rendered order (sessions are grouped by project, so the flat
  // filteredSessions order doesn't match the DOM order).
  const sessionListRef = useRef<HTMLDivElement>(null);
  const [rovingId, setRovingId] = useState<string | null>(null);
  const visibleSessionIds = useMemo(
    () => new Set(filteredSessions.map((s) => s.id)),
    [filteredSessions],
  );
  // Fallback chain: last roving target → current session → first row,
  // each only while still visible (filter/search can invalidate them).
  const activeRovingId =
    (rovingId !== null && visibleSessionIds.has(rovingId) ? rovingId : null) ??
    (currentId !== null && visibleSessionIds.has(currentId) ? currentId : null) ??
    filteredSessions[0]?.id ??
    null;

  const focusSessionRow = useCallback((target: HTMLButtonElement) => {
    target.focus();
    const tid = target.getAttribute("data-testid") ?? "";
    if (tid.startsWith("sidebar-session-")) {
      setRovingId(tid.slice("sidebar-session-".length));
    }
  }, []);

  const handleSessionRowKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>) => {
      const key = event.key;
      if (key !== "ArrowDown" && key !== "ArrowUp" && key !== "Home" && key !== "End") {
        return;
      }
      const rows = Array.from(
        sessionListRef.current?.querySelectorAll<HTMLButtonElement>(
          "[data-session-row] > button",
        ) ?? [],
      );
      const idx = rows.indexOf(event.currentTarget);
      if (idx === -1) return;
      event.preventDefault();
      const last = rows.length - 1;
      const next =
        key === "ArrowDown"
          ? rows[Math.min(idx + 1, last)]
          : key === "ArrowUp"
            ? rows[Math.max(idx - 1, 0)]
            : key === "Home"
              ? rows[0]
              : rows[last];
      focusSessionRow(next);
    },
    [focusSessionRow],
  );

  const renderProjectGroup = (project: Project) => {
    const sessionsInProject = sessionsByProject.get(project.id) ?? [];
    const expanded = effectiveExpandedIds.has(project.id);
    return (
      <div key={project.id} className="space-y-0.5">
        {renderProjectHeader(project, sessionsInProject)}
        {expanded && (
          <ul className="space-y-0.5 pl-2">
            {sessionsInProject.map((s) => (
              <SessionRow
                key={s.id}
                session={s}
                selected={s.id === currentId}
                projects={projects}
                onClick={() => handleSessionClick(s)}
                selectionActive={selectionActive}
                isSelected={selectedSessionIds.has(s.id)}
                onToggleSelect={toggleSessionSelection}
                tabStop={s.id === activeRovingId}
                onRowKeyDown={handleSessionRowKeyDown}
              />
            ))}
            {!searchMode && sessionsInProject.length === 0 && (
              <li className="px-2 py-1 text-[11px] italic text-ink-2">暂无任务</li>
            )}
          </ul>
        )}
      </div>
    );
  };

  const visibleCount = filteredSessions.length;
  const selectionActive = selectedSessionIds.size > 0;
  const selectedCount = selectedSessionIds.size;
  const allVisibleSelected = visibleCount > 0 && selectedCount === visibleCount;

  const handleSelectAllVisible = () => {
    if (allVisibleSelected) {
      clearSessionSelection();
    } else {
      selectAllVisible(filteredSessions.map((s) => s.id));
    }
  };

  const handleBatchArchive = () => {
    if (selectedCount === 0) return;
    const ids = Array.from(selectedSessionIds);
    const archived = filter !== "archived";
    void batchArchiveSessions(ids, archived);
  };

  const handleBatchMove = (projectId: string) => {
    if (selectedCount === 0) return;
    void batchMoveToProject(Array.from(selectedSessionIds), projectId);
    setBatchMoveOpen(false);
  };

  const batchMoveTargets = useMemo(() => {
    return [...projects].sort((a, b) => b.updated_at - a.updated_at);
  }, [projects]);

  return (
    <aside
      data-testid={testId}
      className="flex h-full w-60 max-w-full shrink-0 flex-col border-r border-line bg-surface-1"
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
          <p className="truncate text-[11px] text-ink-2">
            {strings.layout.sidebar.brandTagline} · v{APP_VERSION}
          </p>
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
          onClick={() => void createSession(DEFAULT_SESSION_TITLE, currentProject.id)}
          title={`在「${currentProject.name}」创建新任务`}
          data-testid="sidebar-new-task"
        >
          新任务
        </Button>
        <DropdownMenu
          align="left"
          testId="sidebar-project-selector"
          trigger={
            <IconButton
              aria-label={`当前项目：${currentProject.name}`}
              title={`当前项目：${currentProject.name}`}
              data-testid="sidebar-project-selector-trigger"
            >
              <span className="flex items-center gap-0.5">
                <span className="[&>svg]:h-3 [&>svg]:w-3">{projectIcon(currentProject, 12)}</span>
                <ChevronDown size={10} />
              </span>
            </IconButton>
          }
          items={projectSelectorItems}
        />
        <IconButton
          aria-label={strings.layout.sidebar.createWorktreeTask}
          title={strings.layout.sidebar.createWorktreeTask}
          onClick={() => void createWorktree(DEFAULT_WORKTREE_TITLE, "HEAD")}
          data-testid="sidebar-new-worktree-task"
        >
          <GitBranch size={14} />
        </IconButton>
      </div>

      {/* Four daily areas; each child retains the existing view/filter behavior. */}
      <nav aria-label="日常导航" className="max-h-[42%] shrink-0 space-y-1 overflow-y-auto px-2" data-testid="sidebar-nav">
        {([
          { id: "projects" as const, label: "项目与任务", icon: <Folder size={14} />, items: <>
            <NavItem icon={<Folder size={14} />} label="项目与任务列表" selected={navigationGroup === "projects" && view === "chat" && filter === "all"} onClick={() => { onViewChange?.("chat"); setFilter("all"); }} testId="sidebar-nav-projects" />
            <NavItem icon={<History size={14} />} label="任务历史" selected={view === "chat" && filter === "history"} onClick={() => { onViewChange?.("chat"); setFilter("history"); }} testId="sidebar-nav-history" />
            <NavItem icon={<Archive size={14} />} label="已归档" selected={view === "chat" && filter === "archived"} onClick={() => { onViewChange?.("chat"); setFilter("archived"); }} testId="sidebar-nav-archived" />
          </> },
          { id: "work" as const, label: "当前工作", icon: <LayoutDashboard size={14} />, items: <>
            <NavItem icon={<LayoutDashboard size={14} />} label="当前会话" selected={navigationGroup === "work" && view === "chat" && filter === "all"} onClick={() => { onViewChange?.("chat"); setFilter("all"); }} testId="sidebar-nav-chat" />
            <NavItem icon={<Search size={14} />} label="工作区预览" selected={view === "preview"} onClick={() => onViewChange?.("preview")} testId="sidebar-nav-preview" />
            <NavItem icon={<Wrench size={14} />} label="技能" selected={view === "skills"} onClick={() => { onViewChange?.("skills"); setFilter("skills"); }} testId="sidebar-nav-skills" />
            <NavItem icon={<Bot size={14} />} label="Agent 管理" selected={view === "agents"} onClick={() => { onViewChange?.("agents"); setFilter("all"); }} testId="sidebar-nav-agents" />
          </> },
          { id: "deliveries" as const, label: "交付物", icon: <Archive size={14} />, items: <>
            <NavItem icon={<Folder size={14} />} label="文本成果" selected={view === "artifacts"} onClick={() => onViewChange?.("artifacts")} testId="sidebar-nav-artifacts" />
            <NavItem icon={<Folder size={14} />} label="项目附件与报告" selected={view === "attachments"} onClick={() => onViewChange?.("attachments")} testId="sidebar-nav-attachments" />
            <NavItem icon={<Folder size={14} />} label="办公成果与表格" selected={view === "office"} onClick={() => onViewChange?.("office")} testId="sidebar-nav-office" />
            <NavItem icon={<Search size={14} />} label="研究来源" selected={view === "research"} onClick={() => onViewChange?.("research")} testId="sidebar-nav-research" />
            <NavItem icon={<History size={14} />} label="当前会话成果" selected={view === "run-artifacts"} onClick={() => onViewChange?.("run-artifacts")} testId="sidebar-nav-run-artifacts" />
          </> },
          { id: "automation" as const, label: "自动化", icon: <CalendarClock size={14} />, items: <>
            <NavItem icon={<CalendarClock size={14} />} label="定时任务" selected={view === "scheduled"} onClick={() => { onViewChange?.("scheduled"); setFilter("all"); }} testId="sidebar-nav-scheduled" />
            <NavItem icon={<Wrench size={14} />} label="工作流" selected={view === "workflows"} onClick={() => onViewChange?.("workflows")} testId="sidebar-nav-workflows" />
            <NavItem icon={<Plug size={14} />} label="Webhook" selected={view === "webhooks"} onClick={() => onViewChange?.("webhooks")} testId="sidebar-nav-webhooks" />
          </> },
        ]).map((group) => <div key={group.id}>
          <button type="button" aria-expanded={navigationGroup === group.id} aria-controls={`sidebar-group-${group.id}`}
            onClick={() => setNavigationGroup((current) => current === group.id ? null : group.id)}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs font-semibold text-ink-0 hover:bg-surface-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            data-testid={`sidebar-area-${group.id}`}>
            <span className="text-ink-2">{group.icon}</span><span className="flex-1">{group.label}</span>{navigationGroup === group.id ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </button>
          {navigationGroup === group.id && <div id={`sidebar-group-${group.id}`} className="ml-2 border-l border-line pl-1">{group.items}</div>}
        </div>)}
        {onViewChange && <NavItem icon={<SettingsIcon size={14} />} label="设置" selected={view === "settings"} onClick={() => onViewChange("settings")} testId="sidebar-nav-settings" />}
      </nav>

      {/* Project / Session list */}
      <div className="mt-3 flex min-h-0 flex-1 flex-col">
        {selectionActive ? (
          <div
            data-testid="sidebar-batch-toolbar"
            className="flex items-center justify-between border-b border-line bg-surface-2 px-3 py-1.5"
          >
            <div className="flex items-center gap-2">
              <Checkbox
                checked={allVisibleSelected}
                onChange={handleSelectAllVisible}
                disabled={visibleCount === 0}
                data-testid="sidebar-select-all-visible"
                aria-label="全选可见任务"
              />
              <span className="text-xs text-ink-0">已选择 {selectedCount} 个</span>
            </div>
            <div className="flex items-center gap-1">
              <Button
                size="sm"
                variant="ghost"
                icon={<Folder size={14} />}
                onClick={() => setBatchMoveOpen(true)}
                data-testid="sidebar-batch-move"
              >
                移动
              </Button>
              <Button
                size="sm"
                variant="ghost"
                icon={filter === "archived" ? <ArchiveRestore size={14} /> : <Archive size={14} />}
                onClick={handleBatchArchive}
                data-testid="sidebar-batch-archive"
              >
                {filter === "archived" ? "取消归档" : "归档"}
              </Button>
              <IconButton
                size="sm"
                aria-label="取消选择"
                title="取消选择"
                onClick={clearSessionSelection}
                data-testid="sidebar-clear-selection"
              >
                <X size={14} />
              </IconButton>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between px-4 pt-1 text-[11px] font-medium uppercase tracking-wide text-ink-2">
            <span>项目</span>
            <div className="flex items-center gap-1.5">
              <span data-testid="sidebar-session-count">{visibleCount}</span>
              <IconButton
                size="sm"
                aria-label={strings.layout.sidebar.newProject}
                title={strings.layout.sidebar.newProject}
                onClick={() => {
                  setCreateName("");
                  setCreateRoot("");
                  setCreateOpen(true);
                }}
                data-testid="sidebar-new-project"
              >
                <Plus size={11} />
              </IconButton>
            </div>
          </div>
        )}
        <div className="px-2 pt-2">
          <label className="relative block">
            <Search
              size={12}
              className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-ink-2"
            />
            <Input
              data-testid="sidebar-session-search"
              aria-label={strings.layout.sidebar.searchTaskHistory}
              name="session-history-search"
              autoComplete="off"
              value={historyQuery}
              onChange={(event) => setHistoryQuery(event.target.value)}
              placeholder={strings.layout.sidebar.searchHistoryPlaceholder}
              fieldSize="sm"
              className="pr-7"
            />
            {historyQuery && (
              <IconButton
                size="sm"
                aria-label={strings.layout.sidebar.clearHistorySearch}
                onClick={() => setHistoryQuery("")}
                data-testid="sidebar-session-search-clear"
                className="absolute right-1 top-1/2 -translate-y-1/2"
              >
                <X size={11} />
              </IconButton>
            )}
          </label>
        </div>
        <div
          ref={sessionListRef}
          data-testid="sidebar-session-list"
          className="mt-1 flex-1 space-y-2 overflow-y-auto px-2 pb-2"
        >
          {(loading || searchLoading) && visibleCount === 0 && (
            <>
              {Array.from({ length: 5 }, (_, i) => (
                <div key={`skel-${i}`} className="flex items-center gap-2 px-2 py-1.5">
                  <div className="h-2 w-2 rounded-sm animate-shimmer" />
                  <SkeletonLine className="h-3.5 flex-1" />
                </div>
              ))}
            </>
          )}
          {!loading && !searchLoading && visibleCount === 0 && (
            <div
              data-testid="sidebar-session-empty"
              className="px-2 py-2 text-[11px] italic text-ink-2"
            >
              {historyQuery.trim()
                ? "无匹配任务"
                : "暂无任务 — 点击上方「新任务」创建"}
            </div>
          )}

          {inboxProject && renderProjectGroup(inboxProject)}
          {activeProjects.map(renderProjectGroup)}

          {archivedProjects.length > 0 && (
            <div className="pt-2">
              <div className="px-2 text-[11px] font-medium uppercase tracking-wide text-ink-2">已归档项目</div>
              {archivedProjects.map(renderProjectGroup)}
            </div>
          )}
        </div>
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
        {stats != null && (
          <div className="flex items-center justify-between border-b border-line px-3 py-1.5 text-[11px] text-ink-2">
            <span data-testid="sidebar-stats-sessions">{stats.total_sessions} 会话</span>
            <span data-testid="sidebar-stats-messages">{stats.total_messages} 消息</span>
          </div>
        )}
        {stats == null && statsError && (
          <div
            role="status"
            data-testid="sidebar-stats-error"
            className="border-b border-line px-3 py-1.5 text-[11px] text-status-error"
          >
            {strings.layout.sidebar.statsUnavailable}
          </div>
        )}
        <div className="border-t border-line p-2">
          <UserBadge name="当前用户" email="数据属于当前服务" plan="个人版" />
        </div>
      </div>

      {/* Create project modal */}
      {createOpen && (
        <Modal
          title="新建项目"
          onClose={() => setCreateOpen(false)}
          testId="sidebar-create-project-modal"
          footer={
            <>
              <Button variant="ghost" size="sm" onClick={() => setCreateOpen(false)}>
                取消
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={() => void handleCreateProject()}
                disabled={!createName.trim()}
              >
                创建
              </Button>
            </>
          }
        >
          <Input
            ref={createInputRef}
            value={createName}
            onChange={(e) => setCreateName(e.target.value)}
            placeholder="项目名称"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCreateProject();
            }}
          />
          <p className="mt-3 mb-1 text-[11px] text-ink-2">
            根目录（可选）：项目会话默认使用此目录；命令仍具有宿主进程权限。
          </p>
          <Input
            data-testid="sidebar-create-project-root"
            value={createRoot}
            onChange={(e) => setCreateRoot(e.target.value)}
            placeholder="例如 D:\projects\my-app（留空则不绑定）"
          />
          <p className="mt-3 mb-1 text-[11px] text-ink-2">
            验证命令（可选）：Native 可写任务正常完成后自动执行；Codex 完成后须单独授权主机执行。结果计入任务验收证据。
          </p>
          <Input
            data-testid="sidebar-create-project-validate"
            value={createValidate}
            onChange={(e) => setCreateValidate(e.target.value)}
            placeholder="例如 pytest -x -q 或 npm test（留空则不验证）"
          />
        </Modal>
      )}

      {settingsTarget && <Modal title={`项目验证设置 · ${settingsTarget.name}`}
        onClose={() => { if (!settingsSaving) setSettingsTarget(null); }} testId="sidebar-project-validation-settings"
        footer={<><Button variant="ghost" size="sm" disabled={settingsSaving} onClick={() => setSettingsTarget(null)}>取消</Button>
          <Button variant="primary" size="sm" loading={settingsSaving} disabled={settingsSaving} onClick={() => void saveProjectSettings()}>保存设置</Button></>}>
        <label className="block text-xs text-ink-1">项目根目录
          <Input aria-label="项目根目录" value={settingsRoot} disabled={settingsSaving} onChange={(event) => setSettingsRoot(event.target.value)} placeholder="已有的绝对目录，留空为默认目录" /></label>
        <label className="mt-3 block text-xs text-ink-1">任务验证命令
          <Input aria-label="任务验证命令" value={settingsValidate} maxLength={500} disabled={settingsSaving} onChange={(event) => setSettingsValidate(event.target.value)} placeholder="例如 pytest -q；留空关闭自动验证" /></label>
        <p className="mt-3 text-xs text-ink-2">Native 可写任务正常完成后自动执行；Codex 正常完成后会先请求一次主机执行授权。结果显示在时间线，规划模式不执行。设置只作用于之后接收的任务；已有会话若绑定独立工作区，仍使用其工作区。命令可读写宿主文件并访问网络，请只填写信任的命令。</p>
      </Modal>}

      {/* Rename project modal */}
      {renameTarget && (
        <Modal
          title="重命名项目"
          onClose={() => setRenameTarget(null)}
          testId="sidebar-rename-project-modal"
          footer={
            <>
              <Button variant="ghost" size="sm" onClick={() => setRenameTarget(null)}>
                取消
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={() => void handleRenameProject()}
                disabled={!renameName.trim() || renameName.trim() === renameTarget.name}
              >
                保存
              </Button>
            </>
          }
        >
          <Input
            ref={renameInputRef}
            value={renameName}
            onChange={(e) => setRenameName(e.target.value)}
            placeholder="项目名称"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleRenameProject();
            }}
          />
        </Modal>
      )}

      {/* Delete project confirm modal */}
      {deleteTarget && (
        <Modal
          title="删除项目"
          onClose={() => setDeleteTarget(null)}
          testId="sidebar-delete-project-modal"
          footer={
            <>
              <Button variant="ghost" size="sm" onClick={() => setDeleteTarget(null)}>
                取消
              </Button>
              <Button variant="danger" size="sm" onClick={() => void handleDeleteProject()}>
                删除
              </Button>
            </>
          }
        >
          <p className="text-sm text-ink-0">
            删除项目「<span className="font-medium">{deleteTarget.name}</span>」？
          </p>
          <p className="mt-1 text-xs text-ink-2">
            其下任务将移回「收件箱」，任务数据不会丢失。
          </p>
        </Modal>
      )}

      {/* Batch move sessions modal */}
      {batchMoveOpen && (
        <Modal
          title="移动选中任务"
          onClose={() => setBatchMoveOpen(false)}
          testId="sidebar-batch-move-modal"
          footer={
            <Button variant="secondary" size="sm" onClick={() => setBatchMoveOpen(false)}>
              取消
            </Button>
          }
        >
          <div className="max-h-64 space-y-1 overflow-y-auto py-1">
            {batchMoveTargets.length === 0 && (
              <p className="px-1 py-2 text-[13px] text-ink-2">暂无其他项目</p>
            )}
            {batchMoveTargets.map((project) => (
              <button
                key={project.id}
                type="button"
                onClick={() => handleBatchMove(project.id)}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-ink-0 hover:bg-surface-2"
              >
                <span className="text-ink-1">{projectIcon(project)}</span>
                <span className="flex-1 truncate">{project.name}</span>
              </button>
            ))}
          </div>
        </Modal>
      )}
    </aside>
  );
}
