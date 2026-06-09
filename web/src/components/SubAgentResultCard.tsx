/**
 * SubAgentResultCard — collapsible card rendered in the chat stream
 * once a sub-agent run reaches ``completed`` | ``failed``.
 *
 * The card carries the run's final text and a small "open run"
 * affordance. The card is fully driven by the sub-agent store so
 * the parent (MessageList / ChatPanel) only has to decide when to
 * render one — it does not need to track progress itself.
 */
import { useState } from "react";
import { Bot, ChevronDown, ChevronRight, CircleAlert, ExternalLink, Sparkles } from "lucide-react";
import { useSubAgentStore } from "../stores";
import type { SubAgentRun } from "../types/ipc";

export interface SubAgentResultCardProps {
  testId?: string;
  runId: string;
  /** Optional click handler — the parent decides what "open run" means. */
  onOpenRun?: (run: SubAgentRun) => void;
}

export function SubAgentResultCard({
  testId = "sub-agent-result",
  runId,
  onOpenRun,
}: SubAgentResultCardProps): JSX.Element | null {
  const run = useSubAgentStore((s) => s.runs[runId]);
  const [expanded, setExpanded] = useState(false);

  if (!run) {
    return (
      <div
        data-testid={`${testId}-missing`}
        className="mx-auto my-1 max-w-[80%] rounded-md border border-minimax-border bg-minimax-bg/40 px-3 py-2 text-[11px] italic text-minimax-muted"
      >
        Sub-agent result unavailable
      </div>
    );
  }

  const failed = run.status === "failed";
  const finished = run.status === "completed" || run.status === "failed";
  const accent = failed
    ? "border-red-500/40 bg-red-500/5"
    : "border-minimax-accent/40 bg-minimax-accent/5";
  const label = run.display_name || run.agent_name;
  const icon = failed ? (
    <CircleAlert size={12} className="text-status-error" />
  ) : (
    <Bot size={12} className="text-minimax-accent" />
  );

  return (
    <div
      data-testid={testId}
      data-run-id={runId}
      data-status={run.status}
      className={
        "mx-auto my-1 max-w-[80%] rounded-md border " +
        accent
      }
    >
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span aria-hidden="true">{icon}</span>
        <span
          data-testid={`${testId}-name`}
          className="truncate text-[11px] font-medium text-minimax-fg"
        >
          {label}
        </span>
        <span
          data-testid={`${testId}-status`}
          className={
            "rounded-full border px-1.5 py-0.5 text-[11px] " +
            (failed
              ? "border-red-500/30 text-status-error"
              : "border-emerald-500/30 text-emerald-300")
          }
        >
          {failed ? "failed" : "completed"}
        </span>
        <span className="flex-1" />
        {!finished && (
          <Sparkles size={10} className="animate-pulse text-minimax-accent" />
        )}
        {onOpenRun && (
          <button
            type="button"
            data-testid={`${testId}-open`}
            onClick={() => onOpenRun(run)}
            className="inline-flex items-center gap-0.5 text-[11px] text-minimax-muted hover:text-minimax-fg"
            title="Open in sub-agent panel"
          >
            <ExternalLink size={9} />
            open run
          </button>
        )}
        {run.text && (
          <button
            type="button"
            data-testid={`${testId}-toggle`}
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            className="text-minimax-muted hover:text-minimax-fg"
            title={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
          </button>
        )}
      </div>
      {run.summary && (
        <div
          data-testid={`${testId}-summary`}
          className="border-t border-minimax-border/40 px-3 py-1 text-[11px] text-minimax-muted"
        >
          {run.summary}
        </div>
      )}
      {expanded && run.text && (
        <div
          data-testid={`${testId}-text`}
          className="max-h-60 overflow-auto whitespace-pre-wrap break-words border-t border-minimax-border/40 bg-minimax-bg/40 px-3 py-1.5 text-[11px] text-minimax-fg"
        >
          {run.text}
        </div>
      )}
      {failed && run.error && (
        <div
          data-testid={`${testId}-error`}
          className="border-t border-red-500/30 px-3 py-1 text-[11px] text-status-error"
        >
          {run.error}
        </div>
      )}
    </div>
  );
}
