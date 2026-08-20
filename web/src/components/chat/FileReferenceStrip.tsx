/**
 * File-reference strip — compact cards for paths mentioned in a
 * message, shown above the rendered markdown. Each card shows the
 * filename, directory, optional line badge, and a copy button.
 */
import { FileText, Hash } from "lucide-react";
import { Badge } from "../../ui";
import { strings } from "../../ui/strings";
import { CopyButton } from "./CopyButton";
import type { FileReference } from "./fileReferences";

export function FileReferenceStrip({ refs }: { refs: FileReference[] }): JSX.Element | null {
  if (refs.length === 0) return null;
  return (
    <div
      data-testid="file-reference-strip"
      className="mb-2 flex flex-wrap gap-1.5"
      aria-label={strings.chat.fileRefs.groupLabel}
    >
      {refs.map((ref) => (
        <FileReferenceCard key={`${ref.path}:${ref.line ?? ""}`} refInfo={ref} />
      ))}
    </div>
  );
}

function FileReferenceCard({ refInfo }: { refInfo: FileReference }): JSX.Element {
  const parts = refInfo.path.split("/");
  const filename = parts.at(-1) ?? refInfo.path;
  const directory = parts.slice(0, -1).join("/");
  const label = refInfo.line ? `${refInfo.path}:${refInfo.line}` : refInfo.path;

  return (
    <div
      data-testid="file-reference-card"
      className="group inline-flex max-w-full items-center gap-2 rounded-lg border border-line bg-surface-1 px-2 py-1 text-[11px] text-ink-0 transition-colors duration-150 hover:border-accent/40"
      title={label}
    >
      <FileText size={13} className="shrink-0 text-ink-2" />
      <span className="min-w-0">
        <span className="block truncate font-medium leading-tight">{filename}</span>
        {directory && (
          <span className="block max-w-56 truncate leading-tight text-ink-2">
            {directory}
          </span>
        )}
      </span>
      {refInfo.line && (
        <Badge tone="neutral" data-testid="file-reference-line" className="shrink-0">
          <Hash size={9} />
          {refInfo.line}
        </Badge>
      )}
      <CopyButton
        text={label}
        testId="file-reference-copy"
        title={strings.chat.fileRefs.copyReference}
        className="shrink-0"
      />
    </div>
  );
}
