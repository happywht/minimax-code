/**
 * Collapsible per-turn sources panel for assistant messages.
 *
 * Renders codebase source annotations harvested from tool results
 * (``file_path`` + optional ``line_range``). The user can expand the
 * panel to see every source that contributed to the turn.
 */
import { useState } from "react";
import { ChevronDown, FileCode } from "lucide-react";
import { strings } from "../../ui/strings";
import type { SourceAnnotation } from "../../types/ipc";

export interface SourcesPanelProps {
  sources: SourceAnnotation[];
  testId?: string;
}

export function SourcesPanel({ sources, testId }: SourcesPanelProps): JSX.Element | null {
  const [open, setOpen] = useState(false);

  if (sources.length === 0) return null;

  return (
    <div className="mt-1" data-testid={testId ?? "sources-panel"}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-ink-2 transition-colors hover:bg-surface-1"
        aria-expanded={open}
        aria-label={strings.chat.sources}
      >
        <FileCode size={10} />
        {sources.length} 来源
        <ChevronDown
          size={10}
          className={`shrink-0 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {sources.map((source, index) => {
            const full = `${source.file_path}${source.line_range ? `#${source.line_range}` : ""}`;
            return (
              <span
                key={`${source.file_path}-${source.line_range ?? index}`}
                className="inline-flex max-w-full items-center gap-1 rounded-md border border-line bg-surface-1 px-1.5 py-0.5 text-[11px] text-ink-0"
                title={full}
              >
                <FileCode size={10} className="shrink-0 text-ink-2" />
                <span className="truncate">{source.file_path}</span>
                {source.line_range && (
                  <span className="shrink-0 text-ink-2">#{source.line_range}</span>
                )}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
