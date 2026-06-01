import { useEffect, useState } from "react";
import { ipc } from "../ipc";

/**
 * Top-right corner status pill — shows whether the agent sidecar is
 * running. The actual progress reporting is added by the agent-core
 * task (see `task.progress` event in docs/ipc-contract.md).
 */
export function ProgressPanel() {
  const [status, setStatus] = useState<"pending" | "ready" | "error">(
    "pending",
  );
  const [detail, setDetail] = useState<string>("");

  useEffect(() => {
    let mounted = true;
    let unlisten: (() => void) | null = null;

    (async () => {
      await ipc.start();
      unlisten = ipc.onSideCar((p) => {
        if (!mounted) return;
        if (p.status === "started") {
          setStatus("ready");
          setDetail("");
        } else if (p.status === "error") {
          setStatus("error");
          setDetail(p.error || "agent crashed");
        }
      });
    })();

    return () => {
      mounted = false;
      unlisten?.();
    };
  }, []);

  const color =
    status === "ready"
      ? "bg-emerald-500"
      : status === "error"
        ? "bg-red-500"
        : "bg-amber-500";

  return (
    <div className="border-b border-minimax-border px-4 py-2 flex items-center gap-2 text-xs text-minimax-muted">
      <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
      <span>
        agent: {status}
        {detail ? ` — ${detail}` : ""}
      </span>
    </div>
  );
}
