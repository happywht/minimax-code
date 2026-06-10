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
import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertCircle,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Eye,
  FileEdit,
  Loader2,
  RefreshCw,
} from "lucide-react";
import type { Message } from "../types/ipc";
import { highlight } from "../lib/shikiLoader";
import { useChat, useThemeStore } from "../stores";

export interface MessageItemProps {
  message: Message;
  testId?: string;
}

interface TurnSummary {
  thinkingCount: number;
  filesViewed: number;
  filesModified: number;
}

const STATUS_LABELS = {
  queued: "Waiting",
  sending: "Sending",
  streaming: "Generating",
  completed: "",
  failed: "Failed",
  cancelling: "Stopping...",
  cancelled: "Stopped",
} as const;

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
  // Language detection deferred to highlight() — unknown langs return null.
  const lang = langMatch?.[1] ?? "text";
  const [html, setHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Lazy-load shiki on first code block render
  useEffect(() => {
    let cancelled = false;
    if (isInline || !code) return;
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
  }, [code, lang, isInline]);

  // Cleanup copy feedback timer on unmount
  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  if (isInline) {
    return (
      <code className="rounded bg-minimax-border/70 px-1 py-0.5 font-mono text-[12px] text-minimax-fg">
        {children}
      </code>
    );
  }

  return (
    <div className="my-2 overflow-hidden rounded-md border border-minimax-border bg-minimax-bg">
      <div className="flex items-center justify-between border-b border-minimax-border/60 bg-minimax-bg/40 px-2 py-1 text-[11px] uppercase tracking-wider text-minimax-muted">
        <span>{lang}</span>
        <button
          type="button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(code);
              setCopied(true);
              if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
              copyTimerRef.current = setTimeout(() => setCopied(false), 1500);
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

export const MessageItem = React.memo(function MessageItem({ message, testId }: MessageItemProps): JSX.Element {
  const isUser = message.role === "user";
  const isTool = message.role === "tool";
  const isSystem = message.role === "system";
  const isAssistant = message.role === "assistant";
  const theme = useThemeStore((s) => s.theme);
  const retryMessage = useChat((s) => s.retryMessage);
  const status = message.status ?? (message.streaming ? "streaming" : "completed");
  const isFailed = status === "failed";
  const isQueued = status === "queued" || status === "sending";
  const isCancelling = status === "cancelling";
  const isCancelled = status === "cancelled";
  const showStatus = isAssistant && status !== "completed";

  // Per-turn summary is only meaningful for assistant messages.
  // We pull the full message log from the chat store so we can count
  // tool calls that happened in the same turn.
  // Optimization: use a stable selector key (message count) to avoid
  // unnecessary re-computation when unrelated parts of the store change.
  const msgCount = useChat((s) => s.messages.length);
  const messages = useChat((s) => s.messages);
  const summary = useMemo<TurnSummary | null>(() => {
    if (!isAssistant) return null;
    const idx = messages.findIndex((m) => m.id === message.id);
    if (idx < 0) return { thinkingCount: 0, filesViewed: 0, filesModified: 0 };
    return summarizeTurn(messages, idx, message);
  }, [isAssistant, msgCount, messages, message]);

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
            ? "max-w-[80%] rounded-2xl rounded-br-md bg-minimax-accent px-4 py-2 text-sm text-white shadow-sm transition-colors duration-200"
            : isSystem
              ? "max-w-[80%] rounded-md border border-red-500/30 bg-red-500/5 px-3 py-1.5 text-xs italic text-status-error transition-colors duration-200"
              : "max-w-[85%] rounded-2xl rounded-bl-md border px-4 py-2 text-sm shadow-sm transition-colors duration-200 " +
                (isFailed
                  ? "border-red-500/40 bg-red-500/5 text-status-error"
                  : isCancelling
                    ? "border-amber-500/30 bg-amber-500/5 text-minimax-fg"
                    : isCancelled
                      ? "border-minimax-border bg-minimax-panel/70 text-minimax-muted"
                      : "border-minimax-border bg-minimax-panel text-minimax-fg")
        }
      >
        {isAssistant && summary && (
          <div
            data-testid={`message-summary-${message.id}`}
            className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 border-b border-minimax-border/40 pb-1.5 text-[11px] text-minimax-muted"
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
        {showStatus && (
          <div
            data-testid={`message-status-${message.id}`}
            className={
              "mb-1.5 inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] transition-all duration-200 " +
              (isFailed
                ? "border-red-500/30 bg-red-500/10 text-status-error"
                : isCancelling
                  ? "border-amber-500/30 bg-amber-500/10 text-amber-300"
                  : isCancelled
                    ? "border-minimax-border bg-minimax-bg/40 text-minimax-muted"
                    : "border-minimax-accent/20 bg-minimax-accent/10 text-minimax-accent")
            }
          >
            {isFailed ? (
              <AlertCircle size={11} />
            ) : isQueued || isCancelling ? (
              <Loader2 size={11} className="animate-spin" />
            ) : null}
            {STATUS_LABELS[status]}
          </div>
        )}
        {isQueued && !message.text ? (
          <div data-testid={`message-skeleton-${message.id}`} className="space-y-2 py-1">
            <div className="h-3 w-52 animate-pulse rounded bg-minimax-border/70" />
            <div className="h-3 w-40 animate-pulse rounded bg-minimax-border/50" />
          </div>
        ) : !isFailed ? (
        <div className={`prose prose-sm max-w-none break-words leading-relaxed${theme === "dark" ? " prose-invert" : ""}`}>
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
              {message.text || ""}
            </ReactMarkdown>
          )}
        </div>
        ) : null}
        {isFailed && (
          <div className="mt-2 flex items-center justify-between gap-3 border-t border-red-500/20 pt-2">
            <span className="text-[11px] text-status-error/80">
              {message.error ?? message.text}
            </span>
            <button
              type="button"
              data-testid={`message-retry-${message.id}`}
              onClick={() => void retryMessage(message.id)}
              className="inline-flex shrink-0 items-center gap-1 rounded-md border border-red-500/30 px-2 py-1 text-[11px] font-medium text-status-error transition-colors duration-200 hover:bg-red-500/10"
            >
              <RefreshCw size={11} />
              Retry
            </button>
          </div>
        )}
        {status === "streaming" && !isUser && (
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
});
