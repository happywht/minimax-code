/**
 * Single message bubble. Renders user, assistant, tool, and system
 * roles. Markdown (with GFM) is rendered for assistant / system
 * messages; code blocks go through shiki for syntax highlighting.
 */
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDown, ChevronRight, Copy, Check } from "lucide-react";
import type { Message } from "../types/ipc";
import { codeToHtml, bundledLanguages } from "shiki";

export interface MessageItemProps {
  message: Message;
  testId?: string;
}

/* ─────────────────────── Code block with shiki ─────────────────────── */

interface CodeProps {
  className?: string;
  children?: React.ReactNode;
  /** react-markdown 9 dropped `inline`; we detect via className. */
  inline?: boolean;
}

function MarkdownCode({ className, children, inline }: CodeProps): JSX.Element {
  const code = String(children ?? "").replace(/\n$/, "");
  const langMatch = /language-(\w+)/.exec(className ?? "");
  const hasLang = !!langMatch;
  // react-markdown 9: inline code has no `language-*` className. We
  // also fall back to the `inline` prop for older runtimes.
  const isInline = inline || (!hasLang && !code.includes("\n"));
  const lang = langMatch?.[1] && bundledLanguages[langMatch[1] as keyof typeof bundledLanguages]
    ? langMatch[1]
    : "text";
  const [html, setHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useMemo(() => {
    let cancelled = false;
    if (isInline || !code) return;
    codeToHtml(code, { lang, theme: "github-dark" })
      .then((h) => {
        if (!cancelled) setHtml(h);
      })
      .catch(() => {
        if (!cancelled) setHtml(null);
      });
    return () => {
      cancelled = true;
    };
  }, [code, lang, isInline]);

  if (isInline) {
    return (
      <code className="rounded bg-minimax-border/70 px-1 py-0.5 font-mono text-[12px] text-minimax-fg">
        {children}
      </code>
    );
  }

  return (
    <div className="my-2 overflow-hidden rounded-md border border-minimax-border bg-[#0d1117]">
      <div className="flex items-center justify-between border-b border-minimax-border/60 bg-minimax-bg/40 px-2 py-1 text-[10px] uppercase tracking-wider text-minimax-muted">
        <span>{lang}</span>
        <button
          type="button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(code);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            } catch {
              // ignore
            }
          }}
          className="flex items-center gap-1 rounded px-1 py-0.5 hover:bg-minimax-border"
        >
          {copied ? <Check size={10} /> : <Copy size={10} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      {html ? (
        <div
          className="overflow-x-auto p-3 text-[12px] leading-relaxed"
          // shiki output is pre-sanitized
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className="overflow-x-auto p-3 text-[12px] leading-relaxed text-minimax-fg">
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}

/* ─────────────────────────── MessageItem ─────────────────────────── */

export function MessageItem({ message, testId }: MessageItemProps): JSX.Element {
  const isUser = message.role === "user";
  const isTool = message.role === "tool";
  const isSystem = message.role === "system";

  // Tool bubbles are collapsible to keep the chat scannable.
  const [expanded, setExpanded] = useState(false);

  if (isTool) {
    return (
      <div
        data-testid={testId ?? "message-tool"}
        data-role="tool"
        className="flex justify-start"
      >
        <div className="max-w-[85%] rounded-md border border-minimax-border bg-minimax-panel/60 px-3 py-2 text-xs">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex w-full items-center gap-1.5 text-left font-mono text-minimax-muted hover:text-minimax-fg"
            aria-expanded={expanded}
          >
            {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            <span className="truncate">
              {message.tool_name ?? "tool"}
              {message.tool_args ? `(${Object.keys(message.tool_args).join(", ")})` : ""}
            </span>
          </button>
          {expanded && (
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-[11px] text-minimax-fg">
              {message.text}
            </pre>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      data-testid={testId ?? `message-${message.role}`}
      data-role={message.role}
      className={
        isUser
          ? "flex justify-end"
          : isSystem
            ? "flex justify-center"
            : "flex justify-start"
      }
    >
      <div
        className={
          isUser
            ? "max-w-[80%] rounded-2xl rounded-br-md bg-minimax-accent px-4 py-2 text-sm text-white shadow-sm"
            : isSystem
              ? "max-w-[80%] rounded-md border border-red-500/30 bg-red-500/5 px-3 py-1.5 text-xs italic text-red-300"
              : "max-w-[85%] rounded-2xl rounded-bl-md border border-minimax-border bg-minimax-panel px-4 py-2 text-sm text-minimax-fg shadow-sm"
        }
      >
        <div className="prose prose-invert prose-sm max-w-none break-words leading-relaxed">
          {isUser ? (
            <p className="m-0 whitespace-pre-wrap">{message.text}</p>
          ) : (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                code: MarkdownCode as never,
                a: ({ href, children }) => (
                  <a
                    href={href}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-minimax-accent underline-offset-2 hover:underline"
                  >
                    {children}
                  </a>
                ),
              }}
            >
              {message.text || (message.streaming ? "▍" : "")}
            </ReactMarkdown>
          )}
        </div>
        {message.streaming && !isUser && (
          <span
            aria-hidden
            className="ml-0.5 inline-block w-2 animate-pulse text-minimax-accent"
          >
            ▍
          </span>
        )}
      </div>
    </div>
  );
}
