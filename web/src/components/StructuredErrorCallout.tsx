import { useMemo, useState } from "react";
import { Check, ChevronDown, ChevronRight, CircleAlert, Clipboard, RotateCcw, Settings } from "lucide-react";

export interface StructuredErrorCalloutProps {
  testId?: string;
  title?: string;
  message: string;
  context?: Record<string, unknown>;
  onRetry?: () => void;
  onOpenSettings?: () => void;
}

interface ErrorCopy {
  title: string;
  explanation: string;
  actionHint: string;
  settingsLikely: boolean;
}

function normalise(message: string): string {
  return message.trim() || "Unknown error";
}

export function explainError(message: string, fallbackTitle = "Operation failed"): ErrorCopy {
  const text = normalise(message);
  const lower = text.toLowerCase();

  if (lower.includes("unknown agent name") || lower.includes("unknown agent")) {
    return {
      title: "Sub-agent was not found",
      explanation: "The selected @agent no longer matches an available sub-agent definition.",
      actionHint: "Refresh the agent list, choose an available agent, then retry the request.",
      settingsLikely: false,
    };
  }

  if (lower.includes("api key") || lower.includes("no key") || lower.includes("unauthorized") || lower.includes("403")) {
    return {
      title: "Provider authentication failed",
      explanation: "The provider rejected the request or the saved API key is missing or invalid.",
      actionHint: "Open provider settings, save the key again, then retry.",
      settingsLikely: true,
    };
  }

  if (lower.includes("connection refused") || lower.includes("network") || lower.includes("failed to fetch")) {
    return {
      title: "Agent service is unreachable",
      explanation: "The web UI could not reach the local agent service.",
      actionHint: "Restart the backend service and retry once the connection banner clears.",
      settingsLikely: false,
    };
  }

  return {
    title: fallbackTitle,
    explanation: "The operation stopped before it could complete.",
    actionHint: "Review the details below, then retry if the inputs still look correct.",
    settingsLikely: false,
  };
}

export function StructuredErrorCallout({
  testId = "structured-error",
  title,
  message,
  context,
  onRetry,
  onOpenSettings,
}: StructuredErrorCalloutProps): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const copy = useMemo(() => explainError(message, title ?? "Operation failed"), [message, title]);
  const technicalDetails = useMemo(
    () => JSON.stringify({ message: normalise(message), context }, null, 2),
    [context, message],
  );

  async function copyDiagnostics(): Promise<void> {
    try {
      await navigator.clipboard?.writeText(technicalDetails);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setExpanded(true);
    }
  }

  return (
    <div
      data-testid={testId}
      className="rounded-md border border-red-500/30 bg-red-500/5 text-[11px] text-minimax-fg"
    >
      <div className="flex items-start gap-2 px-3 py-2">
        <CircleAlert size={13} className="mt-0.5 shrink-0 text-status-error" />
        <div className="min-w-0 flex-1">
          <div data-testid={`${testId}-title`} className="font-medium text-status-error">
            {title ?? copy.title}
          </div>
          <div data-testid={`${testId}-explanation`} className="mt-0.5 text-minimax-muted">
            {copy.explanation}
          </div>
          <div data-testid={`${testId}-hint`} className="mt-1 text-minimax-muted/90">
            {copy.actionHint}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 border-t border-red-500/20 px-3 py-1.5">
        {onRetry ? (
          <button
            type="button"
            data-testid={`${testId}-retry`}
            onClick={onRetry}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-minimax-muted transition-colors hover:bg-minimax-border/60 hover:text-minimax-fg"
          >
            <RotateCcw size={10} />
            Retry
          </button>
        ) : null}
        {onOpenSettings && copy.settingsLikely ? (
          <button
            type="button"
            data-testid={`${testId}-settings`}
            onClick={onOpenSettings}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-minimax-muted transition-colors hover:bg-minimax-border/60 hover:text-minimax-fg"
          >
            <Settings size={10} />
            Settings
          </button>
        ) : null}
        <button
          type="button"
          data-testid={`${testId}-copy`}
          onClick={() => void copyDiagnostics()}
          className="inline-flex items-center gap-1 rounded px-2 py-1 text-minimax-muted transition-colors hover:bg-minimax-border/60 hover:text-minimax-fg"
        >
          {copied ? <Check size={10} /> : <Clipboard size={10} />}
          {copied ? "Copied" : "Copy diagnostics"}
        </button>
        <button
          type="button"
          data-testid={`${testId}-details-toggle`}
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="ml-auto inline-flex items-center gap-1 rounded px-2 py-1 text-minimax-muted transition-colors hover:bg-minimax-border/60 hover:text-minimax-fg"
        >
          {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
          Details
        </button>
      </div>
      {expanded ? (
        <pre
          data-testid={`${testId}-details`}
          className="max-h-40 overflow-auto whitespace-pre-wrap break-words border-t border-red-500/20 bg-minimax-bg/50 px-3 py-2 font-mono text-[11px] text-minimax-muted"
        >
          {technicalDetails}
        </pre>
      ) : null}
    </div>
  );
}
