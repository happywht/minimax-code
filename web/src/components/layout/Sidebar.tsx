/**
 * Left sidebar — 240px wide. Contains:
 *   - Brand mark
 *   - "New task" button with project selector
 *   - Primary nav
 *   - Session history grouped by project
 *   - Footer: UserBadge
 */
import { useEffect, useMemo, useRef, useState } from "react";
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
import { typedIPC } from "../../ipc";
import { useChat, useSessionStore, type SessionFilter, type SessionMeta } from "../../stores";
import type { Project } from "../../types/ipc";
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
  const [historyQuery, setHistoryQuery] = useState("");
  const [batchMoveOpen, setBatchMoveOpen] = useState(false);
  const [remoteSearchSessions, setRemoteSearchSessions] = useState<SessionMeta[] | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [stats, setStats] = useState<{ total_sessions: number; total_messages: number } | null>(null);
  // Primitive selectors only — streaming chunks mutate messages in place,
  // so we key off the count and the run status instead of the array ref.
  const chatMessageCount = useChat((s) => s.messages.length);
  const chatStatus = useChat((s) => s.status);

  // Modal state for project operations.
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [renameTarget, setRenameTarget] = useState<Project | null>(null);
  const [renameName, setRenameName] = useState("");
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
        if (!cancelled) setStats(result);
      } catch {
        // Footer stats are non-critical.
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
    const project = await createProject(name);
    if (project) {
      setCurrentProject(project.id);
      setCreateOpen(false);
      setCreateName("");
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
    await updateProject(renameTarget.id, { name });
    setRenameTarget(null);
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
          onClick={() => void createSession("New task", currentProject.id)}
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
        <div className="border-t border-line p-2">
          <UserBadge name="本地用户" email="数据仅保存在本机" plan="个人版" />
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
        </Modal>
      )}

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
