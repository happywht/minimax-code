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
 * The shell is intentionally custom (not the `Modal` primitive): the
 * keyboard contract is Enter = allow / Esc = deny, which conflicts with
 * Modal's Esc-to-close.
 *
 * The "Allow once" / "Allow always" split is intentionally not exposed
 * here — that lives in the Settings page. The "always allow" master
 * toggle (PermissionToggle in the footer) handles the bulk-allow case
 * by short-circuiting the listener in the store.
 */

import { ShieldAlert, ShieldCheck, X } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { usePermissionStore, type PendingPermission } from "../../stores";
import { useFocusTrap } from "../../lib/useFocusTrap";
import { buildPermissionPatchFiles } from "../../lib/permissionPatchPreview";
import { Button, IconButton } from "../../ui";
import { PermissionPatchPreview } from "./PermissionPatchPreview";

export interface PermissionRequestModalProps {
  testId?: string;
}

export function PermissionRequestModal({
  testId = "permission-request-modal",
}: PermissionRequestModalProps): JSX.Element | null {
  const pendingMap = usePermissionStore((s) => s.pending);
  // Default defensively for older persisted state and partial test/store mocks
  // created before the resolving map was introduced.
  const resolvingMap = usePermissionStore((s) => s.resolving ?? {});
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
  const currentResolving = current ? resolvingMap[current.request_id] : undefined;

  // Enter approves, Esc denies — keeps the approval card keyboard-first.
  useEffect(() => {
    if (!current || currentResolving) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        void resolve(current.request_id, "deny");
      } else if (e.key === "Enter") {
        e.preventDefault();
        void resolve(current.request_id, "allow");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, currentResolving, resolve]);

  if (!current) return null;

  const argsPreview = formatArgs(current.args);
  const patchFiles = buildPermissionPatchFiles(current.tool, current.args);
  const resolving = currentResolving;

  return (
    <div
      ref={dialogRef}
      data-testid={testId}
      data-request-id={current.request_id}
      role="dialog"
      aria-modal="true"
      aria-labelledby="perm-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-surface-overlay p-4 backdrop-blur-sm"
    >
      <div
        data-testid="permission-request-modal-card"
        className="animate-modal-in relative w-full max-w-md rounded-xl border border-line bg-surface-1 p-5 shadow-modal"
      >
        <IconButton
          aria-label="关闭"
          data-testid="permission-request-modal-close"
          onClick={() => void resolve(current.request_id, "deny")}
          disabled={Boolean(resolving)}
          className="absolute right-3 top-3 disabled:cursor-wait"
        >
          <X />
        </IconButton>
        <div className="mb-3 flex items-start gap-3">
          <div
            data-testid="permission-request-modal-icon"
            className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-status-warning/30 bg-[var(--status-warning-subtle)] text-status-warning"
          >
            <ShieldAlert size={18} />
          </div>
          <div className="min-w-0 flex-1">
            <h2
              id="perm-modal-title"
              data-testid="permission-request-modal-title"
              className="text-sm font-semibold text-ink-0"
            >
              工具调用需要授权
            </h2>
            <p
              data-testid="permission-request-modal-tool"
              className="mt-1 text-xs text-ink-1"
            >
              Agent 准备调用 <code className="rounded bg-surface-3 px-1.5 py-0.5 text-ink-0">{current.tool}</code>
            </p>
            {current.session_id && (
              <p
                data-testid="permission-request-modal-session"
                className="mt-0.5 text-[11px] text-ink-2"
                title={current.session_id}
              >
                所属会话 <code className="font-mono">{current.session_id.slice(0, 12)}</code>
              </p>
            )}
          </div>
        </div>

        <div
          data-testid="permission-request-modal-args"
          className="mb-4 max-h-40 overflow-auto rounded-md border border-line bg-surface-2/60 p-2 text-[11px] leading-relaxed text-ink-0"
        >
          <pre className="whitespace-pre-wrap break-words font-mono">{argsPreview}</pre>
        </div>

        {patchFiles.length > 0 && (
          <div className="mb-4 rounded-md border border-line bg-surface-2/40 px-2 pb-2">
            <PermissionPatchPreview files={patchFiles} testId="permission-request-modal-patch-preview" />
          </div>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button
            variant="secondary"
            size="sm"
            data-testid="permission-request-modal-deny"
            onClick={() => void resolve(current.request_id, "deny")}
            disabled={Boolean(resolving)}
            className="disabled:cursor-wait"
          >
            {resolving === "deny" ? "处理中..." : "拒绝"}
          </Button>
          <Button
            variant="primary"
            size="sm"
            icon={<ShieldCheck />}
            data-testid="permission-request-modal-allow"
            onClick={() => void resolve(current.request_id, "allow")}
            disabled={Boolean(resolving)}
            className="disabled:cursor-wait"
          >
            {resolving === "allow" ? "处理中..." : "允许"}
          </Button>
        </div>

        <p className="mt-3 text-[11px] text-ink-2">
          Enter 允许 · Esc 拒绝 · 关闭右侧 × 等同于拒绝
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
