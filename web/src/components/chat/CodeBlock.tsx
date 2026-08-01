/**
 * Markdown code renderer.
 *
 * Inline code renders as a pill; fenced blocks render as a card with
 * a header (language label + optional source tag + copy button), a
 * line-number gutter, and shiki syntax highlighting (lazy-loaded on
 * first render; falls back to a plain <pre> for unsupported languages).
 * ``mermaid`` fences are delegated to <MermaidBlock />.
 *
 * v0.11.0: code fence info strings may carry a source annotation, e.g.
 *   ```ts src/auth.ts#L10-20
 * which is rendered as a clickable source chip in the block header.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { FileCode } from "lucide-react";
import { highlight } from "../../lib/shikiLoader";
import { CopyButton } from "./CopyButton";
import { MermaidBlock } from "./MermaidBlock";

export interface MarkdownCodeProps {
  className?: string;
  children?: ReactNode;
  /** react-markdown 9 dropped `inline`; we detect via className. */
  inline?: boolean;
}

interface SourceAnnotation {
  file_path: string;
  line_range: string | null;
}

function parseSource(className: string): SourceAnnotation | null {
  // React-markdown puts the fence info string in the className as
  // "language-<lang> [extra tokens]". We look for a file-like token.
  const tokens = className.split(/\s+/);
  for (const token of tokens) {
    if (token.startsWith("language-")) continue;
    const match = /^(.*\.[A-Za-z0-9_]+)(?:#(.*))?$/.exec(token);
    if (match) {
      return { file_path: match[1], line_range: match[2] ?? null };
    }
  }
  return null;
}

export function MarkdownCode({ className, children, inline }: MarkdownCodeProps): JSX.Element {
  const code = String(children ?? "").replace(/\n$/, "");
  const lineNumbers = useMemo(
    () => Array.from({ length: Math.max(1, code.split("\n").length) }, (_, index) => index + 1),
    [code],
  );
  const langMatch = /language-(\w+)/.exec(className ?? "");
  const hasLang = !!langMatch;
  const isInline = inline || (!hasLang && !code.includes("\n"));
  const lang = langMatch?.[1] ?? "text";
  const isMermaid = lang.toLowerCase() === "mermaid";
  const source = useMemo(() => parseSource(className ?? ""), [className]);
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
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-[11px] font-medium uppercase tracking-wider text-ink-2">
            {lang}
          </span>
          {source && (
            <span
              className="inline-flex max-w-[16rem] items-center gap-1 truncate rounded bg-surface-3 px-1.5 py-0.5 text-[11px] text-ink-1"
              title={source.line_range ? `${source.file_path}#${source.line_range}` : source.file_path}
              data-testid="code-source-tag"
            >
              <FileCode size={10} className="text-accent" />
              <span className="truncate">{source.file_path}</span>
              {source.line_range && (
                <span className="text-ink-2">#{source.line_range}</span>
              )}
            </span>
          )}
        </div>
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
