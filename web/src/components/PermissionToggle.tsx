/**
 * "Always allow" toggle — bottom-bar permission shortcut. Bound to the
 * permission store. The detailed rules editor lives in a future
 * section; this is the everyday control.
 */
import { Shield, ShieldCheck } from "lucide-react";
import { usePermissionStore } from "../stores";

export interface PermissionToggleProps {
  testId?: string;
}

export function PermissionToggle({ testId = "permission-toggle" }: PermissionToggleProps): JSX.Element {
  const alwaysAllow = usePermissionStore((s) => s.alwaysAllow);
  const setAlwaysAllow = usePermissionStore((s) => s.setAlwaysAllow);
  return (
    <button
      type="button"
      role="switch"
      aria-checked={alwaysAllow}
      data-testid={testId}
      onClick={() => setAlwaysAllow(!alwaysAllow)}
      className={
        "flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors " +
        (alwaysAllow
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
          : "border-minimax-border bg-minimax-panel text-minimax-muted hover:text-minimax-fg")
      }
    >
      {alwaysAllow ? <ShieldCheck size={12} /> : <Shield size={12} />}
      {alwaysAllow ? "始终授权：开" : "始终授权"}
    </button>
  );
}
