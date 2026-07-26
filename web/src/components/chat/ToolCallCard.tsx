/**
 * Collapsible tool-call card.
 *
 * Tool messages render as a compact mono row (tool name + argument
 * keys) that expands on click to reveal the raw tool output, keeping
 * the chat scannable during long agent runs.
 */
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import { useState } from "react";
import type { Message } from "../../types/ipc";

export interface ToolCallCardProps {
  message: Message;
  testId?: string;
}

export function ToolCallCard({ message, testId }: ToolCallCardProps): JSX.Element {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      data-testid={testId ?? "message-tool"}
      data-role="tool"
      className="flex justify-start"
    >
      <div className="max-w-full rounded-lg border border-line bg-surface-1 px-2.5 py-1.5 text-xs transition-colors duration-150 hover:border-line-strong hover:bg-surface-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex w-full items-center gap-1.5 text-left font-mono text-[11px] text-ink-1 transition-colors duration-150 hover:text-ink-0"
          aria-expanded={expanded}
        >
          <Wrench size={11} className="shrink-0 text-ink-2" />
          {expanded ? (
            <ChevronDown size={12} className="shrink-0" />
          ) : (
            <ChevronRight size={12} className="shrink-0" />
          )}
          <span className="truncate font-medium">{message.tool_name ?? "tool"}</span>
          {message.tool_args && (
            <span className="truncate text-[11px] text-ink-2">
              {Object.keys(message.tool_args).join(", ")}
            </span>
          )}
        </button>
        {expanded && (
          <pre className="mt-1.5 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md border border-line bg-surface-0 px-2 py-1.5 text-[11px] text-ink-0">
            {message.text}
          </pre>
        )}
      </div>
    </div>
  );
}
