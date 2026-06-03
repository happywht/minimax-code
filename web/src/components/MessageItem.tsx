/**
 * Single message bubble. Renders user, assistant, tool, and system
 * roles. Markdown (with GFM) is rendered for assistant / system
 * messages; code blocks go through shiki for syntax highlighting.
 *
 * Assistant messages also get a per-turn summary row at the top —
 * "思考 N 次 · 查看 M 个文件 · 修改 K 个文件" — derived from the
 * tool-call/tool-result messages that follow the assistant bubble in
 * the same turn (bounded by the next user/assistant message).
 */
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Brain, ChevronDown, ChevronRight, Copy, Check, Eye, FileEdit } from "lucide-react";
import type { Message } from "../types/ipc";
import { codeToHtml, bundledLanguages } from "shiki";
import { useChat } from "../stores";

export interface MessageItemProps {
  message: Message;
  testId?: string;
}

interface TurnSummary {
  thinkingCount: number;
  filesViewed: number;
  filesModified: number;
}

/**
 * Walk the global message log starting at `startIdx + 1` and bucket
 * tool calls into "viewed" (read_file / list_files / glob_files /
 * search_files) vs "modified" (write_file / edit_file / create_file
 * / delete_file). Stops at the next user or assistant message.
 *
 * `thinkingCount` reads `message.metadata?.thinking_count` if the
 * store ever exposes it; otherwise falls back to 0. The fallback keeps
 * the summary row non-empty even on plain mock-mode runs.
 */
function summarizeTurn(
  messages: Message[],
  startIdx: number,
  self: Message,
): TurnSummary {
  const VIEW_NAMES = new Set([
    "read_file",
    "list_files",
    "glob_files",
    "search_files",
    "list_directory",
  ]);
  const MOD_NAMES = new Set([
    "write_file",
    "edit_file",
    "create_file",
    "delete_file",
    "patch_file",
  ]);
  let filesViewed = 0;
  let filesModified = 0;
  for (let i = startIdx + 1; i < messages.length; i++) {
    const m = messages[i];
    if (m.role === "user" || m.role === "assistant") break;
    if (m.role === "tool" && m.tool_name) {
      if (VIEW_NAMES.has(m.tool_name)) filesViewed += 1;
      else if (MOD_NAMES.has(m.tool_name)) filesModified += 1;
    }
  }
  // ``Message.metadata`` (v0.3.0) carries ``{thinking_count,
  // tokens_in, tokens_out}`` populated by the chat store from the
  // latest ``agent.message_chunk`` event. Falls back to 0 for
  // older runs that haven't migrated yet.
  const thinkingCount = self.metadata?.thinking_count ?? 0;
  return { thinkingCount, filesViewed, filesModified };
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
  const isAssistant = message.role === "assistant";

  // Per-turn summary is only meaningful for assistant messages.
  // We pull the full message log from the chat store so we can count
  // tool calls that happened in the same turn.
  const messages = useChat((s) => s.messages);
  const summary = useMemo<TurnSummary | null>(() => {
    if (!isAssistant) return null;
    const idx = messages.findIndex((m) => m.id === message.id);
    if (idx < 0) return { thinkingCount: 0, filesViewed: 0, filesModified: 0 };
    return summarizeTurn(messages, idx, message);
  }, [isAssistant, messages, message]);

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
        {isAssistant && summary && (
          <div
            data-testid={`message-summary-${message.id}`}
            className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 border-b border-minimax-border/40 pb-1.5 text-[10px] text-minimax-muted"
            aria-label="turn summary"
          >
            <span className="inline-flex items-center gap-1">
              <Brain size={10} className="text-minimax-muted" />
              思考 {summary.thinkingCount} 次
            </span>
            <span className="inline-flex items-center gap-1">
              <Eye size={10} className="text-minimax-muted" />
              查看 {summary.filesViewed} 个文件
            </span>
            <span className="inline-flex items-center gap-1">
              <FileEdit size={10} className="text-minimax-muted" />
              修改 {summary.filesModified} 个文件
            </span>
          </div>
        )}
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
