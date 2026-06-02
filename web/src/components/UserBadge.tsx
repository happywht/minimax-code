/**
 * User badge — bottom-left avatar + plan info.
 *
 * Layout:
 *
 *   <div user-badge>
 *     <button.user-badge-trigger>     // opens dropdown menu
 *       <span.avatar>{initials}</span>
 *       <span.text-stack>
 *         <span.name>{name}</span>
 *         <span.plan-row>             // ← inline editable
 *           <span.plan-label>{plan}</span>
 *           <span.plan-edit role=button>  // enters edit mode
 *         </span.plan-row>
 *       </span.text-stack>
 *     </button.user-badge-trigger>
 *     {editing && <input.plan-input .../>}  // replaces plan-row
 *     {open && <div.dropdown-menu> ... </div>}
 *   </div>
 *
 * Note: the plan-edit affordance is intentionally a *span with
 * role="button"* rather than a real <button> — nesting a <button>
 * inside the trigger <button> would be invalid HTML and trip the
 * React ``validateDOMNesting`` warning. Spans are still keyboard
 * accessible (Enter / Space activate) via the ``onKeyDown`` handler.
 */
import { useEffect, useState } from "react";
import { Crown, LogOut, Pencil, Settings } from "lucide-react";

const PLAN_STORAGE_KEY = "minimax-code:plan";
const DEFAULT_PLAN = "Max Plan";

export interface UserBadgeProps {
  name?: string;
  email?: string;
  plan?: string;
  onSignOut?: () => void;
  onSettings?: () => void;
}

function readStoredPlan(): string {
  if (typeof window === "undefined" || !window.localStorage) return DEFAULT_PLAN;
  try {
    const v = window.localStorage.getItem(PLAN_STORAGE_KEY);
    if (v && v.trim().length > 0) return v;
  } catch {
    // localStorage may throw in private mode / SSR — fall through.
  }
  return DEFAULT_PLAN;
}

function writeStoredPlan(plan: string): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  try {
    window.localStorage.setItem(PLAN_STORAGE_KEY, plan);
  } catch {
    // Ignore persistence failures — the in-memory value still wins
    // for the rest of this session.
  }
}

export function UserBadge({
  name = "User",
  email = "user@example.com",
  plan,
  onSignOut,
  onSettings,
}: UserBadgeProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [currentPlan, setCurrentPlan] = useState<string>(() => plan ?? readStoredPlan());
  const [draft, setDraft] = useState(currentPlan);

  // Sync from prop changes (e.g. parent flips a real auth value in)
  // without clobbering an in-progress edit.
  useEffect(() => {
    if (!editing && plan !== undefined && plan !== currentPlan) {
      setCurrentPlan(plan);
      setDraft(plan);
    }
  }, [plan, editing, currentPlan]);

  const initials = name
    .split(/\s+/)
    .map((s) => s[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  function commitDraft(): void {
    const trimmed = draft.trim();
    const next = trimmed.length > 0 ? trimmed : DEFAULT_PLAN;
    setCurrentPlan(next);
    setDraft(next);
    writeStoredPlan(next);
    setEditing(false);
  }

  function cancelEdit(): void {
    setDraft(currentPlan);
    setEditing(false);
  }

  function startEditing(): void {
    setOpen(false);
    setEditing(true);
  }

  return (
    <div className="relative" data-testid="user-badge">
      <button
        type="button"
        data-testid="user-badge-trigger"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-minimax-border/60"
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-minimax-accent/30 text-[11px] font-semibold text-minimax-fg">
          {initials || "U"}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium text-minimax-fg">
            {name}
          </span>
          {editing ? (
            <input
              autoFocus
              data-testid="user-badge-plan-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitDraft}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commitDraft();
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  cancelEdit();
                }
              }}
              onClick={(e) => e.stopPropagation()}
              className="mt-0.5 w-full rounded-sm border border-minimax-accent/40 bg-minimax-bg/40 px-1 py-0 text-[10px] text-minimax-fg outline-none focus:border-minimax-accent"
            />
          ) : (
            <span
              data-testid="user-badge-plan"
              className="mt-0.5 flex items-center gap-1 truncate text-[10px] text-minimax-muted"
            >
              <Crown size={10} className="shrink-0 text-amber-400" />
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation();
                  startEditing();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    e.stopPropagation();
                    startEditing();
                  }
                }}
                title="Click to edit plan"
                className="group flex min-w-0 flex-1 items-center gap-1 truncate rounded text-left hover:text-minimax-fg focus:outline-none focus-visible:ring-1 focus-visible:ring-minimax-accent"
              >
                <span className="truncate">{currentPlan}</span>
                <Pencil
                  size={9}
                  className="shrink-0 opacity-0 transition-opacity group-hover:opacity-60"
                />
              </span>
            </span>
          )}
        </span>
      </button>
      {open && !editing && (
        <div
          className="absolute bottom-full left-0 mb-1 w-56 rounded-md border border-minimax-border bg-minimax-panel p-1 shadow-xl"
          role="menu"
        >
          <div className="px-2 py-1.5">
            <div className="truncate text-xs text-minimax-fg">{name}</div>
            <div className="truncate text-[10px] text-minimax-muted">
              {email}
            </div>
          </div>
          <hr className="border-minimax-border" />
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onSettings?.();
            }}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-minimax-fg hover:bg-minimax-border"
          >
            <Settings size={12} /> Settings
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onSignOut?.();
            }}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-red-300 hover:bg-minimax-border"
          >
            <LogOut size={12} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
