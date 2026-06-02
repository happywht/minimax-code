/**
 * Workspace switcher — compact dropdown button in the top header
 * that shows the current workspace name and lets the user pick a
 * different one. The list is persisted to `localStorage`; a fresh
 * install is seeded with a single "default" entry.
 *
 * Switching a workspace:
 *   1. Persists the new selection to `localStorage`.
 *   2. Triggers `sessionStore.refresh()` so the sidebar re-fetches
 *      the session list under the new workspace key.
 *   3. Shows a toast confirming the switch.
 *
 * Designed to mirror MiniMax Code's breadcrumb-style top bar:
 * 40px tall, panel background, bottom border, workspace name with a
 * chevron-down, and a 240px dropdown on click.
 */
import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Folder } from "lucide-react";
import {
  getCurrentWorkspace,
  listWorkspaces,
  setCurrentWorkspace,
  type WorkspaceEntry,
} from "../lib/workspace";
import { useSessionStore } from "../stores";
import { toast } from "./ErrorBoundary";

export interface WorkspaceSwitcherProps {
  testId?: string;
}

export function WorkspaceSwitcher({
  testId = "workspace-switcher",
}: WorkspaceSwitcherProps): JSX.Element {
  const [current, setCurrent] = useState<string>(() => getCurrentWorkspace());
  const [workspaces, setWorkspaces] = useState<WorkspaceEntry[]>(() =>
    listWorkspaces(),
  );
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const refreshSessions = useSessionStore((s) => s.refresh);

  // Close on outside click — same pattern as ModelSelector.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  // Keep state in sync if some other code path (e.g. tests) mutates
  // localStorage directly between renders.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (
        e.key === "minimax-code:current-workspace" ||
        e.key === "minimax-code:workspaces"
      ) {
        setCurrent(getCurrentWorkspace());
        setWorkspaces(listWorkspaces());
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const selectWorkspace = async (name: string) => {
    if (name === current) {
      setOpen(false);
      return;
    }
    setCurrentWorkspace(name);
    setCurrent(name);
    setOpen(false);
    // Clearing the cached session list makes the sidebar feel
    // snappy: the new workspace's sessions replace the old ones
    // immediately even before the network round-trip finishes.
    useSessionStore.setState({ sessions: [], currentSessionId: null });
    toast.info("Switched workspace", name);
    try {
      await refreshSessions();
    } catch {
      // refresh() already toasts on failure; we just need to keep
      // the UI responsive when the round-trip rejects.
    }
  };

  return (
    <div ref={containerRef} className="relative" data-testid={testId}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid="workspace-switcher-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded-md border border-minimax-border bg-minimax-panel/60 px-2.5 py-1 text-xs text-minimax-fg hover:border-minimax-accent/50"
      >
        <Folder size={12} className="text-minimax-muted" />
        <span data-testid="workspace-switcher-label" className="truncate max-w-[160px]">
          {current}
        </span>
        <ChevronDown
          size={12}
          className="text-minimax-muted"
          data-testid="workspace-switcher-chevron"
        />
      </button>
      {open && (
        <ul
          role="listbox"
          data-testid="workspace-switcher-menu"
          className="absolute left-0 top-full z-20 mt-1 w-60 overflow-hidden rounded-md border border-minimax-border bg-minimax-panel shadow-xl"
        >
          {workspaces.length === 0 && (
            <li className="px-3 py-2 text-xs text-minimax-muted">
              No workspaces
            </li>
          )}
          {workspaces.map((w) => {
            const isCurrent = w.name === current;
            return (
              <li key={w.name}>
                <button
                  type="button"
                  role="option"
                  aria-selected={isCurrent}
                  onClick={() => void selectWorkspace(w.name)}
                  data-testid={`workspace-option-${w.name}`}
                  className={
                    "flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-minimax-border " +
                    (isCurrent ? "bg-minimax-accent/10" : "")
                  }
                >
                  <Folder size={12} className="shrink-0 text-minimax-muted" />
                  <span className="flex-1 truncate">
                    <span className="block text-minimax-fg">{w.name}</span>
                    {w.path && (
                      <span className="block text-[10px] text-minimax-muted">
                        {w.path}
                      </span>
                    )}
                  </span>
                  {isCurrent && (
                    <Check
                      size={12}
                      className="text-minimax-accent"
                      data-testid="workspace-switcher-check"
                    />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
