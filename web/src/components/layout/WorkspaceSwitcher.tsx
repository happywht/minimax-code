/**
 * Project switcher — compact dropdown button in the top header that
 * shows the current project name and lets the user pick a different
 * one, plus an inline "new project" row.
 *
 * v1.2.2: rewired from the old localStorage-only workspace list
 * (`lib/workspace.ts`, removed) to the real project system — the same
 * `sessionStore` slice the sidebar selector drives. Both UIs share
 * one state source (`currentProjectId` + `projects`), so switching
 * here updates the sidebar grouping and vice-versa. Projects are
 * grouping labels for sessions (not filesystem workspaces): switching
 * only changes where *new* sessions land, so the existing session
 * list is deliberately left untouched.
 *
 * Keeps the MiniMax Code breadcrumb-style top bar: 40px tall, panel
 * background, project name with a chevron-down, and a dropdown on
 * click.
 */
import { useMemo, useRef, useState, useCallback } from "react";
import { Check, ChevronDown, Folder, Inbox, Plus } from "lucide-react";
import { Button } from "../../ui/Button";
import { strings } from "../../ui/strings";
import { useClickOutside } from "../../lib/useClickOutside";
import { useSessionStore } from "../../stores";
import type { Project } from "../../types/ipc";
import { toast } from "./ErrorBoundary";

export interface WorkspaceSwitcherProps {
  testId?: string;
}

export function WorkspaceSwitcher({
  testId = "workspace-switcher",
}: WorkspaceSwitcherProps): JSX.Element {
  const projects = useSessionStore((s) => s.projects);
  const currentProjectId = useSessionStore((s) => s.currentProjectId);
  const setCurrentProject = useSessionStore((s) => s.setCurrentProject);
  const createProject = useSessionStore((s) => s.createProject);

  const [open, setOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [creating, setCreating] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close on outside click — shared hook replaces inline mousedown listener.
  const closeMenu = useCallback(() => setOpen(false), []);
  useClickOutside(containerRef, closeMenu, { enabled: open });

  // Mirror the sidebar's grouping: inbox first, then active, then
  // archived. Same order in both switchers keeps one mental model.
  const { orderedProjects, inboxProject } = useMemo(() => {
    const inbox: Project =
      projects.find((p) => p.id === "inbox") ?? {
        id: "inbox",
        name: "收件箱",
        description: "",
        root_path: "",
        archived: false,
        created_at: 0,
        updated_at: 0,
      };
    const byUpdated = (a: Project, b: Project) => b.updated_at - a.updated_at;
    const active = projects
      .filter((p) => p.id !== "inbox" && !p.archived)
      .sort(byUpdated);
    const archived = projects
      .filter((p) => p.id !== "inbox" && p.archived)
      .sort(byUpdated);
    return {
      orderedProjects: [inbox, ...active, ...archived],
      inboxProject: inbox,
    };
  }, [projects]);

  const currentProject = useMemo(
    () =>
      orderedProjects.find((p) => p.id === (currentProjectId ?? "inbox")) ??
      inboxProject,
    [orderedProjects, currentProjectId, inboxProject],
  );

  const selectProject = (project: Project) => {
    setOpen(false);
    if (project.id === currentProject.id) return;
    setCurrentProject(project.id);
    toast.info(strings.layout.workspace.switchedToast, project.name);
  };

  const handleCreate = async () => {
    const name = createName.trim();
    if (!name || creating) return;
    setCreating(true);
    try {
      const project = await createProject(name);
      if (project) {
        setCurrentProject(project.id);
        setCreateName("");
        setOpen(false);
        toast.info(strings.layout.workspace.createdToast, project.name);
      } else {
        toast.error(strings.layout.workspace.createFailedToast, name);
      }
    } catch {
      toast.error(strings.layout.workspace.createFailedToast, name);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div ref={containerRef} className="relative min-w-0" data-testid={testId}>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        icon={<Folder size={12} className="text-ink-2" />}
        onClick={() => setOpen((v) => !v)}
        data-testid="workspace-switcher-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={strings.layout.workspace.triggerLabel(currentProject.name)}
        title={strings.layout.workspace.triggerLabel(currentProject.name)}
        className="min-w-0 max-w-[110px] justify-between sm:max-w-[180px]"
      >
        <span data-testid="workspace-switcher-label" className="min-w-0 truncate">
          {currentProject.name}
        </span>
        <ChevronDown
          size={12}
          className="shrink-0 text-ink-2"
          data-testid="workspace-switcher-chevron"
        />
      </Button>
      {open && (
        <div
          data-testid="workspace-switcher-menu"
          className="absolute left-0 top-full z-50 mt-1 w-60 overflow-hidden rounded-lg border border-line bg-surface-1 shadow-pop"
        >
          <ul role="listbox" aria-label={strings.layout.workspace.triggerLabel(currentProject.name)}>
            {orderedProjects.length === 0 && (
              <li className="px-3 py-2 text-xs text-ink-1">
                {strings.layout.workspace.empty}
              </li>
            )}
            {orderedProjects.map((p) => {
              const isCurrent = p.id === currentProject.id;
              return (
                <li key={p.id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={isCurrent}
                    onClick={() => selectProject(p)}
                    data-testid={`workspace-option-${p.id}`}
                    title={p.root_path || undefined}
                    className={
                      "flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-ink-0 transition-colors hover:bg-surface-3 " +
                      (isCurrent ? "bg-accent-subtle" : "")
                    }
                  >
                    {p.id === "inbox" ? (
                      <Inbox size={12} className="shrink-0 text-ink-2" />
                    ) : (
                      <Folder size={12} className="shrink-0 text-ink-2" />
                    )}
                    <span className="min-w-0 flex-1 truncate text-ink-0">
                      {p.name}
                      {p.archived && (
                        <span className="ml-1 text-[11px] text-ink-2">
                          ({strings.layout.workspace.archivedSuffix})
                        </span>
                      )}
                    </span>
                    {isCurrent && (
                      <Check
                        size={12}
                        className="shrink-0 text-accent"
                        data-testid="workspace-switcher-check"
                      />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="border-t border-line p-2">
            <div className="flex items-center gap-1.5">
              <Plus size={12} className="shrink-0 text-ink-2" />
              <input
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleCreate();
                  if (e.key === "Escape") setOpen(false);
                }}
                placeholder={strings.layout.workspace.createPlaceholder}
                aria-label={strings.layout.workspace.createLabel}
                data-testid="workspace-switcher-create-input"
                className="min-w-0 flex-1 rounded border border-line bg-surface-0 px-2 py-1 text-xs text-ink-0 placeholder:text-ink-2 focus:border-accent focus:outline-none"
              />
              <Button
                type="button"
                size="sm"
                loading={creating}
                disabled={creating || createName.trim().length === 0}
                onClick={() => void handleCreate()}
                data-testid="workspace-switcher-create-submit"
                aria-label={strings.layout.workspace.createLabel}
                className="!px-1.5 !py-0.5"
              >
                <Plus size={12} />
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
