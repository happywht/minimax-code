/**
 * Permission request modal — pops up when the Python sidecar emits a
 * `permission.request` event (a tool with an `ask` rule is about to
 * run, and we want real human approval before the agent loop continues).
 *
 * The component subscribes to `usePermissionStore.pending`. As soon as
 * a new request arrives, the modal opens and shows:
 *   - the tool name
 *   - the (pretty-printed) tool arguments
 *   - 允许 / 拒绝 buttons (each clicking also closes the modal)
 *
 * The "Allow once" / "Allow always" split is intentionally not exposed
 * here — that lives in the Settings page. The "always allow" master
 * toggle (PermissionToggle in the footer) handles the bulk-allow case
 * by short-circuiting the listener in the store.
 */

import { ShieldAlert, ShieldCheck, X } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { usePermissionStore, type PendingPermission } from "../stores";
import { useFocusTrap } from "../lib/useFocusTrap";

export interface PermissionRequestModalProps {
  testId?: string;
}

export function PermissionRequestModal({
  testId = "permission-request-modal",
}: PermissionRequestModalProps): JSX.Element | null {
  const pendingMap = usePermissionStore((s) => s.pending);
  const resolve = usePermissionStore((s) => s.resolve);
  const dialogRef = useRef<HTMLDivElement>(null);
  const isOpen = useMemo(() => Object.keys(pendingMap).length > 0, [pendingMap]);
  useFocusTrap(dialogRef, isOpen);

  // Pick the oldest pending request (FIFO). Multiple simultaneous
  // prompts are stacked; the modal only shows the head of the queue.
  const current: PendingPermission | null = useMemo(() => {
    const entries = Object.values(pendingMap);
    if (entries.length === 0) return null;
    return entries.reduce((oldest, cur) =>
      cur.received_at < oldest.received_at ? cur : oldest,
    );
  }, [pendingMap]);

  // Esc to deny — matches the OS-level "deny on cancel" convention.
  useEffect(() => {
    if (!current) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        void resolve(current.request_id, "deny");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, resolve]);

  if (!current) return null;

  const argsPreview = formatArgs(current.args);

  return (
    <div
      ref={dialogRef}
      data-testid={testId}
      data-request-id={current.request_id}
      role="dialog"
      aria-modal="true"
      aria-labelledby="perm-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
    >
      <div
        data-testid="permission-request-modal-card"
        className="relative mx-4 w-full max-w-md rounded-lg border border-minimax-border bg-minimax-bg p-5 shadow-2xl"
      >
        <button
          type="button"
          aria-label="关闭"
          data-testid="permission-request-modal-close"
          onClick={() => void resolve(current.request_id, "deny")}
          className="absolute right-3 top-3 rounded p-1 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
        >
          <X size={14} />
        </button>
        <div className="mb-3 flex items-start gap-3">
          <div
            data-testid="permission-request-modal-icon"
            className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-amber-500/30 bg-amber-500/10 text-amber-300"
          >
            <ShieldAlert size={18} />
          </div>
          <div className="min-w-0 flex-1">
            <h2
              id="perm-modal-title"
              data-testid="permission-request-modal-title"
              className="text-sm font-semibold text-minimax-fg"
            >
              工具调用需要授权
            </h2>
            <p
              data-testid="permission-request-modal-tool"
              className="mt-1 text-xs text-minimax-muted"
            >
              Agent 准备调用 <code className="rounded bg-minimax-panel px-1.5 py-0.5 text-minimax-fg">{current.tool}</code>
            </p>
          </div>
        </div>

        <div
          data-testid="permission-request-modal-args"
          className="mb-4 max-h-40 overflow-auto rounded border border-minimax-border bg-minimax-panel/60 p-2 text-[11px] leading-relaxed text-minimax-fg"
        >
          <pre className="whitespace-pre-wrap break-words font-mono">{argsPreview}</pre>
        </div>

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            data-testid="permission-request-modal-deny"
            onClick={() => void resolve(current.request_id, "deny")}
            className="rounded-md border border-minimax-border bg-minimax-panel px-3 py-1.5 text-xs text-minimax-fg hover:border-red-500/40 hover:text-red-300"
          >
            拒绝
          </button>
          <button
            type="button"
            data-testid="permission-request-modal-allow"
            onClick={() => void resolve(current.request_id, "allow")}
            className="flex items-center gap-1.5 rounded-md border border-emerald-500/40 bg-emerald-500/15 px-3 py-1.5 text-xs text-emerald-200 hover:bg-emerald-500/25"
          >
            <ShieldCheck size={12} />
            允许
          </button>
        </div>

        <p className="mt-3 text-[10px] text-minimax-muted">
          按 Esc 拒绝 · 关闭右侧 × 等同于拒绝
        </p>
      </div>
    </div>
  );
}

function formatArgs(args: Record<string, unknown>): string {
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
}
