/**
 * SessionRow — a single session row inside the sidebar project group.
 *
 * Memoized so long session lists don't re-render every row on unrelated
 * sidebar state changes (e.g. search input typing).
 */
import { memo, useState } from "react";
import { Archive, Folder, Inbox, MoreHorizontal, Trash2 } from "lucide-react";
import { NavItem } from "./NavItem";
import { formatRelative } from "../../lib/time";
import { Button, DropdownMenu, IconButton, Modal } from "../../ui";
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
  onClick: () => void;
}

export const SessionRow = memo(function SessionRow({
  session,
  selected,
  onClick,
}: SessionRowProps): JSX.Element {
  const [moveOpen, setMoveOpen] = useState(false);
  const projects = useSessionStore((s) => s.projects);
  const currentProjectId = session.project_id ?? "inbox";
  const targetProjects = projects
    .filter((p) => p.id !== currentProjectId)
    .sort((a, b) => b.updated_at - a.updated_at);

  const handleArchive = () => {
    void useSessionStore.getState().archive(session.id);
  };

  const handleDelete = () => {
    void useSessionStore.getState().remove(session.id);
  };

  const handleMove = (projectId: string) => {
    void useSessionStore.getState().moveSessionProject(session.id, projectId);
    setMoveOpen(false);
  };

  return (
    <>
      <li
        key={session.id}
        data-testid={`sidebar-session-row-${session.id}`}
        className="group relative"
      >
        <NavItem
          icon={
            <span
              aria-hidden
              data-testid={`sidebar-session-dot-${session.id}`}
              data-status={session.archived ? "archived" : "active"}
              className={`block h-2 w-2 rounded-sm ${statusDotClass(session)}`}
            />
          }
          label={truncate(session.title || "(untitled)", MAX_TITLE_LEN)}
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
                aria-label="Session options"
                title="Session options"
                onClick={(e) => e.stopPropagation()}
              >
                <MoreHorizontal size={14} />
              </IconButton>
            }
            items={[
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
                onClick: () => {
                  if (session.archived) {
                    void useSessionStore.getState().unarchive(session.id);
                  } else {
                    handleArchive();
                  }
                },
              },
              {
                id: "delete",
                label: "删除",
                icon: <Trash2 size={14} />,
                danger: true,
                onClick: handleDelete,
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
    </>
  );
});
