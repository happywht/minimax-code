/**
 * Markdown code renderer.
 *
 * Inline code renders as a pill; fenced blocks render as a card with
 * a header (language label + copy button), a line-number gutter, and
 * shiki syntax highlighting (lazy-loaded on first render; falls back
 * to a plain <pre> for unsupported languages). ``mermaid`` fences
 * are delegated to <MermaidBlock />.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { highlight } from "../../lib/shikiLoader";
import { CopyButton } from "./CopyButton";
import { MermaidBlock } from "./MermaidBlock";

export interface MarkdownCodeProps {
  className?: string;
  children?: ReactNode;
  /** react-markdown 9 dropped `inline`; we detect via className. */
  inline?: boolean;
}

export function MarkdownCode({ className, children, inline }: MarkdownCodeProps): JSX.Element {
  const code = String(children ?? "").replace(/\n$/, "");
  const lineNumbers = useMemo(
    () => Array.from({ length: Math.max(1, code.split("\n").length) }, (_, index) => index + 1),
    [code],
  );
  const langMatch = /language-(\w+)/.exec(className ?? "");
  const hasLang = !!langMatch;
  // react-markdown 9: inline code has no `language-*` className. We
  // also fall back to the `inline` prop for older runtimes.
  const isInline = inline || (!hasLang && !code.includes("\n"));
  // Language detection deferred to highlight() — unknown langs return null.
  const lang = langMatch?.[1] ?? "text";
  const isMermaid = lang.toLowerCase() === "mermaid";
  const [html, setHtml] = useState<string | null>(null);

  // Lazy-load shiki on first code block render
  useEffect(() => {
    let cancelled = false;
    if (isInline || isMermaid || !code) return;
    highlight(code, lang)
      .then((h) => {
        if (!cancelled) setHtml(h);
      })
      .catch(() => {
        if (!cancelled) setHtml(null);
      });
    return () => {
      cancelled = true;
    };
  }, [code, lang, isInline, isMermaid]);

  if (isInline) {
    return (
      <code className="rounded border border-line bg-surface-3 px-1 py-0.5 font-mono text-[12px] text-ink-0">
        {children}
      </code>
    );
  }

  if (isMermaid) {
    return <MermaidBlock code={code} />;
  }

  return (
    <div className="my-2 overflow-hidden rounded-lg border border-line bg-surface-0">
      <div className="flex items-center justify-between gap-2 border-b border-line bg-surface-2 py-0.5 pl-3 pr-1.5">
        <span className="text-[11px] font-medium uppercase tracking-wider text-ink-2">
          {lang}
        </span>
        <CopyButton text={code} testId="code-copy-button" />
      </div>
      <div className="grid grid-cols-[auto_minmax(0,1fr)] text-[12px] leading-relaxed">
        <pre
          aria-hidden
          data-testid="code-line-numbers"
          className="select-none border-r border-line bg-surface-1 px-2 py-3 text-right font-mono text-ink-2"
        >
          {lineNumbers.join("\n")}
        </pre>
        <div className="min-w-0 overflow-x-auto p-3 font-mono">
          {html ? (
            <div
              className="[&_pre]:m-0 [&_pre]:!bg-transparent [&_pre]:!p-0 [&_pre]:font-mono [&_pre]:leading-relaxed"
              // shiki output is pre-sanitized
              dangerouslySetInnerHTML={{ __html: html }}
            />
          ) : (
            <pre className="m-0 text-ink-0">
              <code>{code}</code>
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
