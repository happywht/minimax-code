/**
 * Mermaid diagram block — renders ```mermaid fences as SVG diagrams
 * with a source-copy button in the header. The mermaid bundle is
 * lazy-loaded on first render; failures surface an inline error
 * instead of breaking the surrounding markdown.
 */
import { AlertCircle } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { useThemeStore } from "../../stores";
import { Spinner } from "../../ui";
import { CopyButton } from "./CopyButton";

export function MermaidBlock({ code }: { code: string }): JSX.Element {
  const rawId = useId();
  const theme = useThemeStore((s) => s.theme);
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const diagramId = useMemo(
    () => `mermaid-${rawId.replace(/[^a-zA-Z0-9_-]/g, "")}`,
    [rawId],
  );

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    setError(null);

    import("mermaid")
      .then(async (mod) => {
        const mermaid = mod.default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: theme === "dark" ? "dark" : "default",
        });
        const result = await mermaid.render(diagramId, code);
        if (!cancelled) setSvg(result.svg);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Unable to render Mermaid diagram");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [code, diagramId, theme]);

  return (
    <div
      data-testid="mermaid-block"
      className="my-2 overflow-hidden rounded-lg border border-line bg-surface-0"
    >
      <div className="flex items-center justify-between gap-2 border-b border-line bg-surface-2 py-0.5 pl-3 pr-1.5">
        <span className="text-[11px] font-medium uppercase tracking-wider text-ink-2">
          Mermaid
        </span>
        <CopyButton text={code} testId="mermaid-copy-button" title="Copy diagram source" />
      </div>
      <div className="min-h-32 overflow-auto p-3">
        {error ? (
          <div
            data-testid="mermaid-error"
            className="flex items-start gap-2 rounded-md border border-status-error/30 bg-[var(--status-error-subtle)] p-3 text-xs text-status-error"
          >
            <AlertCircle size={14} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        ) : svg ? (
          <div
            data-testid="mermaid-svg"
            className="flex min-w-max justify-center [&_svg]:max-w-none"
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        ) : (
          <div
            data-testid="mermaid-loading"
            className="flex items-center gap-2 rounded-md border border-line bg-surface-1 p-3 text-xs text-ink-1"
          >
            <Spinner size={14} />
            Rendering diagram...
          </div>
        )}
      </div>
    </div>
  );
}
