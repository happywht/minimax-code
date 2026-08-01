/**
 * MentionPicker — the `@agent` / `@repo` / `#file` autocomplete dropdown
 * that floats above the composer while a mention token is active.
 */
import { AtSign, Bot, Database, FileCode } from "lucide-react";
import type { MentionKind, MentionOption, MentionState } from "../../lib/mentions";

export interface MentionPickerDropdownProps {
  picker: MentionState;
  filtered: MentionOption[];
  agentsError: string | null;
  onSelect: (option: MentionOption) => void;
}

const HEADER: Record<MentionKind, { icon: JSX.Element; label: string }> = {
  agent: { icon: <AtSign size={10} />, label: "Spawn sub-agent" },
  repo: { icon: <Database size={10} />, label: "Repository context" },
  file: { icon: <FileCode size={10} />, label: "File context" },
};

const KIND_ICON: Record<MentionKind, JSX.Element> = {
  agent: <Bot size={10} className="text-accent" />,
  repo: <Database size={10} className="text-accent" />,
  file: <FileCode size={10} className="text-accent" />,
};

export function MentionPickerDropdown({
  picker,
  filtered,
  agentsError,
  onSelect,
}: MentionPickerDropdownProps): JSX.Element | null {
  if (!picker.open) return null;

  const header = HEADER[picker.kind];

  return (
    <div
      data-testid="message-input-mention-picker"
      className="absolute bottom-full left-2.5 right-2.5 mb-1.5 overflow-hidden rounded-md border border-line bg-surface-2 shadow-pop"
    >
      <div className="flex items-center gap-1 border-b border-line/60 px-2 py-1 text-[11px] uppercase tracking-wider text-ink-2">
        {header.icon}
        <span>{header.label}</span>
      </div>
      {agentsError && picker.kind === "agent" ? (
        <div className="px-2 py-1.5 text-[11px] text-status-error" title={agentsError}>
          Failed to load agents
        </div>
      ) : filtered.length === 0 ? (
        <div className="px-2 py-1.5 text-[11px] italic text-ink-2">
          没有匹配的{picker.kind === "file" ? "文件" : picker.kind === "repo" ? "仓库" : "Agent"}「{picker.query}」
        </div>
      ) : (
        <ul data-testid="message-input-mention-picker-list">
          {filtered.map((option, i) => (
            <li key={option.id}>
              <button
                type="button"
                data-testid={`message-input-mention-picker-item-${option.kind}-${option.id}`}
                onClick={() => onSelect(option)}
                className={
                  "flex w-full items-center gap-2 px-2 py-1 text-left text-[11px] transition-colors duration-150 " +
                  (i === picker.cursor
                    ? "bg-accent-subtle text-ink-0"
                    : "text-ink-1 hover:bg-surface-3")
                }
              >
                {KIND_ICON[option.kind]}
                <span className="font-medium">{option.label}</span>
                {option.kind === "agent" && (
                  <span className="font-mono text-[11px] text-ink-2">@{option.id}</span>
                )}
                <span className="flex-1 truncate text-[11px] text-ink-2">{option.detail}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
