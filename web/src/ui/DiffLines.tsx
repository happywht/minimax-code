/**
 * DiffLines — colour-coded unified-diff renderer. One <div> per line:
 * +++/--- file headers, @@ hunk markers, + additions and - deletions
 * each get a distinct tone; everything else renders as plain context.
 * Shared by the Git viewer modal and the checkpoint diff expander.
 */
export interface DiffLinesProps {
  text: string;
  testId?: string;
  /** Already-localised content shown when the diff is empty. */
  emptyText?: string;
}

export function DiffLines({ text, testId, emptyText }: DiffLinesProps): JSX.Element {
  if (!text) {
    return (
      <div className="px-1 py-6 text-center text-xs text-ink-2">{emptyText}</div>
    );
  }

  const lines = text.split("\n");

  return (
    <pre
      data-testid={testId}
      className="font-mono text-[11px] leading-relaxed"
    >
      {lines.map((line, i) => {
        let cls = "text-ink-1";
        if (line.startsWith("+++") || line.startsWith("---")) {
          cls = "font-semibold text-accent";
        } else if (line.startsWith("@@")) {
          cls = "text-status-info";
        } else if (line.startsWith("+")) {
          cls = "bg-[var(--status-success-subtle)] text-status-success";
        } else if (line.startsWith("-")) {
          cls = "bg-[var(--status-error-subtle)] text-status-error";
        }
        return (
          <div key={i} className={"px-1 " + cls}>
            {line}
          </div>
        );
      })}
    </pre>
  );
}
