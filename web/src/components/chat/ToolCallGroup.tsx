/**
 * Collapsible group of consecutive tool-call cards.
 *
 * Long agent turns can emit dozens of tool calls; rendering each as its
 * own card buried the conversation under a wall of mono rows. Runs of
 * consecutive tool messages (>= TOOL_GROUP_MIN) collapse into a one-line
 * summary ("工具调用 × N · exec_command ×3 · …"); expanding reveals the
 * individual ToolCallCards, each still individually collapsible.
 *
 * A group containing an in-flight (streaming) tool call starts expanded
 * so live progress stays visible; once everything settles the default
 * flips back to collapsed. Explicit clicks always win over the default.
 */
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import { useState } from "react";
import { strings } from "../../ui/strings";
import type { Message } from "../../types/ipc";
import { ToolCallCard } from "./ToolCallCard";

/** Consecutive-tool run length that triggers grouping; shorter runs stay as-is. */
export const TOOL_GROUP_MIN = 3;

/** Distinct tool names shown in the summary line before ellipsizing. */
const TOOL_NAME_PREVIEW = 3;

export interface ToolCallGroupProps {
  messages: Message[];
  testId?: string;
}

function summarizeToolNames(messages: Message[]): string {
  const counts = new Map<string, number>();
  for (const m of messages) {
    const name = m.tool_name ?? strings.chat.toolCall.fallbackName;
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  const parts = [...counts.entries()].map(
    ([name, n]) => (n > 1 ? `${name} ×${n}` : name),
  );
  const preview = parts.slice(0, TOOL_NAME_PREVIEW).join(" · ");
  if (parts.length <= TOOL_NAME_PREVIEW) return preview;
  return `${preview} · ${strings.chat.toolGroup.moreTools(parts.length)}`;
}

export function ToolCallGroup({ messages, testId }: ToolCallGroupProps): JSX.Element {
  const [userExpanded, setUserExpanded] = useState<boolean | null>(null);
  const hasActive = messages.some((m) => m.streaming);
  const expanded = userExpanded ?? hasActive;

  return (
    <div
      data-testid={testId ?? "message-tool-group"}
      data-role="tool-group"
      className="flex flex-col items-start gap-1"
    >
      <button
        type="button"
        onClick={() => setUserExpanded(!expanded)}
        aria-expanded={expanded}
        className="flex max-w-full items-center gap-1.5 rounded-lg border border-line bg-surface-1 px-2.5 py-1.5 font-mono text-[11px] text-ink-1 transition-colors duration-150 hover:border-line-strong hover:bg-surface-2 hover:text-ink-0"
      >
        <Wrench size={11} className="shrink-0 text-ink-2" />
        {expanded ? (
          <ChevronDown size={12} className="shrink-0" />
        ) : (
          <ChevronRight size={12} className="shrink-0" />
        )}
        <span className="shrink-0 font-medium">
          {strings.chat.toolGroup.count(messages.length)}
        </span>
        <span className="truncate text-ink-2">{summarizeToolNames(messages)}</span>
      </button>
      {expanded && (
        <div className="flex w-full flex-col gap-1 pl-3">
          {messages.map((m) => (
            <ToolCallCard key={m.id} message={m} />
          ))}
        </div>
      )}
    </div>
  );
}
