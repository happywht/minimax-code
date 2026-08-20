/**
 * SessionRow — a single session row inside the sidebar project group.
 *
 * Memoized so long session lists don't re-render every row on unrelated
 * sidebar state changes (e.g. search input typing).
 */
import { memo, useEffect, useRef, useState } from "react";
import { Archive, Folder, Inbox, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { NavItem } from "./NavItem";
import { formatRelative } from "../../lib/time";
import { Button, Checkbox, DropdownMenu, IconButton, Input, Modal } from "../../ui";
import { strings } from "../../ui/strings";
import { useSessionStore } from "../../stores";
import type { SessionMeta } from "../../stores";
import type { Project } from "../../types/ipc";

const MAX_TITLE_LEN = 24;

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max - 1) + "…";
}

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

function projectIcon(project: Project, size = 14): JSX.Element {
  if (project.id === "inbox") return <Inbox size={size} />;
  if (project.archived) return <Archive size={size} />;
  return <Folder size={size} />;
}

export interface SessionRowProps {
  session: SessionMeta;
  selected: boolean;
  projects: Project[];
  onClick: () => void;
  selectionActive?: boolean;
  isSelected?: boolean;
  onToggleSelect?: (id: string) => void;
}

export const SessionRow = memo(function SessionRow({
  session,
  selected,
  projects,
  onClick,
  selectionActive = false,
  isSelected = false,
  onToggleSelect,
}: SessionRowProps): JSX.Element {
  const [moveOpen, setMoveOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(session.title);
  const renameInputRef = useRef<HTMLInputElement>(null);

  const currentProjectId = session.project_id ?? "inbox";
  const targetProjects = projects
    .filter((p) => p.id !== currentProjectId)
    .sort((a, b) => b.updated_at - a.updated_at);

  useEffect(() => {
    if (renaming) {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }
  }, [renaming]);

  const handleArchive = () => {
    void useSessionStore.getState().archive(session.id);
  };

  const handleUnarchive = () => {
    void useSessionStore.getState().unarchive(session.id);
  };

  const handleDelete = () => {
    void useSessionStore.getState().remove(session.id);
    setDeleteOpen(false);
  };

  const handleMove = (projectId: string) => {
    void useSessionStore.getState().moveSessionProject(session.id, projectId);
    setMoveOpen(false);
  };

  const startRename = () => {
    setRenameValue(session.title);
    setRenaming(true);
  };

  const commitRename = () => {
    const title = renameValue.trim();
    if (title && title !== session.title) {
      void useSessionStore.getState().rename(session.id, title);
    }
    setRenaming(false);
  };

  const cancelRename = () => {
    setRenameValue(session.title);
    setRenaming(false);
  };

  const handleRenameKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commitRename();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelRename();
    }
  };

  const leadingIcon = (
    <span className="relative flex h-4 w-4 items-center justify-center">
      <span
        aria-hidden
        data-testid={`sidebar-session-dot-${session.id}`}
        data-status={session.archived ? "archived" : "active"}
        className={[
          "absolute block h-2 w-2 rounded-sm transition-opacity duration-150",
          statusDotClass(session),
          selectionActive ? "opacity-0" : "opacity-100 group-hover:opacity-0",
        ].join(" ")}
      />
      <span
        className={[
          "absolute inset-0 flex items-center justify-center transition-opacity duration-150",
          selectionActive ? "opacity-100" : "opacity-0 group-hover:opacity-100",
        ].join(" ")}
      >
        <Checkbox
          checked={isSelected}
          onChange={(event) => {
            event.stopPropagation();
            onToggleSelect?.(session.id);
          }}
          onClick={(event) => event.stopPropagation()}
          data-testid={`sidebar-session-checkbox-${session.id}`}
          aria-label="选择任务"
        />
      </span>
    </span>
  );

  if (renaming) {
    return (
      <li
        data-testid={`sidebar-session-row-${session.id}`}
        className="group relative"
      >
        <div className="flex items-center gap-2 rounded-md bg-surface-3 px-2 py-1.5">
          <span
            aria-hidden
            className={`block h-2 w-2 rounded-sm ${statusDotClass(session)}`}
          />
          <Input
            ref={renameInputRef}
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
            onKeyDown={handleRenameKeyDown}
            onBlur={commitRename}
            className="h-6 flex-1 text-[13px]"
            data-testid={`sidebar-session-rename-input-${session.id}`}
          />
        </div>
      </li>
    );
  }

  return (
    <>
      <li
        data-testid={`sidebar-session-row-${session.id}`}
        className="group relative"
      >
        <NavItem
          icon={leadingIcon}
          label={truncate(session.title || strings.layout.sidebar.untitled, MAX_TITLE_LEN)}
          trailing={
            <span className="ml-1 flex shrink-0 items-center gap-1">
              {session.workspace_mode === "worktree" && (
                <span
                  data-testid={`sidebar-session-workspace-${session.id}`}
                  className="rounded border border-accent/30 bg-accent-subtle px-1 py-0.5 text-[11px] text-accent"
                  title={session.workspace_path ?? "Worktree"}
                >
                  WT
                </span>
              )}
              <span
                data-testid={`sidebar-session-time-${session.id}`}
                className="text-[11px] text-ink-2"
              >
                {formatRelative(session.updated_at)}
              </span>
            </span>
          }
          selected={selected}
          onClick={onClick}
          testId={`sidebar-session-${session.id}`}
        />
        <div className="absolute right-1 top-1/2 z-10 -translate-y-1/2 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          <DropdownMenu
            align="right"
            testId={`sidebar-session-menu-${session.id}`}
            trigger={
              <IconButton
                size="sm"
                aria-label={strings.layout.sidebar.sessionOptions}
                title={strings.layout.sidebar.sessionOptions}
                onClick={(e) => e.stopPropagation()}
              >
                <MoreHorizontal size={14} />
              </IconButton>
            }
            items={[
              {
                id: "rename",
                label: "重命名",
                icon: <Pencil size={14} />,
                onClick: startRename,
              },
              {
                id: "move",
                label: "移动到项目",
                icon: <Folder size={14} />,
                onClick: () => setMoveOpen(true),
              },
              {
                id: "archive",
                label: session.archived ? "取消归档" : "归档",
                icon: <Archive size={14} />,
                onClick: session.archived ? handleUnarchive : handleArchive,
              },
              {
                id: "delete",
                label: "删除",
                icon: <Trash2 size={14} />,
                danger: true,
                onClick: () => setDeleteOpen(true),
              },
            ]}
          />
        </div>
      </li>

      {moveOpen && (
        <Modal
          title="移动到项目"
          onClose={() => setMoveOpen(false)}
          footer={
            <Button variant="secondary" onClick={() => setMoveOpen(false)}>
              取消
            </Button>
          }
        >
          <div className="max-h-64 space-y-1 overflow-y-auto py-1">
            {targetProjects.length === 0 && (
              <p className="px-1 py-2 text-[13px] text-ink-2">暂无其他项目</p>
            )}
            {targetProjects.map((project) => (
              <button
                key={project.id}
                type="button"
                onClick={() => handleMove(project.id)}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-ink-0 hover:bg-surface-2"
              >
                <span className="text-ink-1">{projectIcon(project)}</span>
                <span className="flex-1 truncate">{project.name}</span>
              </button>
            ))}
          </div>
        </Modal>
      )}

      {deleteOpen && (
        <Modal
          title="删除任务"
          onClose={() => setDeleteOpen(false)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setDeleteOpen(false)}>
                取消
              </Button>
              <Button variant="danger" onClick={handleDelete}>
                删除
              </Button>
            </>
          }
        >
          <p className="text-[13px] text-ink-0">
            确认删除「{session.title || strings.layout.sidebar.untitled}」？删除后无法恢复。
          </p>
        </Modal>
      )}
    </>
  );
});
