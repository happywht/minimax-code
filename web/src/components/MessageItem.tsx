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
import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import {
  AlertCircle,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Eye,
  FileEdit,
  FileText,
  Hash,
  Loader2,
  RefreshCw,
} from "lucide-react";
import type { Message } from "../types/ipc";
import { highlight } from "../lib/shikiLoader";
import { useChat, useThemeStore } from "../stores";
import { toast } from "./ErrorBoundary";

export interface MessageItemProps {
  message: Message;
  testId?: string;
}

interface TurnSummary {
  thinkingCount: number;
  filesViewed: number;
  filesModified: number;
}

interface FileReference {
  path: string;
  line?: number;
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

const FILE_REF_EXTENSIONS = [
  "ts",
  "tsx",
  "js",
  "jsx",
  "py",
  "md",
  "json",
  "yaml",
  "yml",
  "toml",
  "css",
  "scss",
  "html",
  "sql",
  "rs",
  "go",
  "java",
  "cpp",
  "c",
  "cs",
  "sh",
  "ps1",
].sort((a, b) => b.length - a.length).join("|");

const FILE_REF_PATTERN = new RegExp(
  String.raw`(?:^|[\s(["'` + "`" + String.raw`])((?:[A-Za-z]:[\\/])?(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+\.(` +
    FILE_REF_EXTENSIONS +
    String.raw`))(?:[:#L](\d+))?`,
  "g",
);

function stripFencedCode(text: string): string {
  return text.replace(/```[\s\S]*?```/g, "");
}

function extractFileReferences(text: string): FileReference[] {
  const withoutCode = stripFencedCode(text);
  const refs: FileReference[] = [];
  const seen = new Set<string>();
  for (const match of withoutCode.matchAll(FILE_REF_PATTERN)) {
    const path = match[1].replace(/\\/g, "/");
    const line = match[3] ? Number(match[3]) : undefined;
    const key = `${path}:${line ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    refs.push({ path, line });
    if (refs.length >= 6) break;
  }
  return refs;
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
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  if (isMermaid) {
    return <MermaidBlock code={code} />;
  }

  return (
    <div className="my-2 overflow-hidden rounded-md border border-minimax-border bg-minimax-bg">
      <div className="flex items-center justify-between border-b border-minimax-border/60 bg-minimax-bg/40 px-2 py-1 text-[11px] uppercase tracking-wider text-minimax-muted">
        <span>{lang}</span>
        <button
          type="button"
          data-testid="code-copy-button"
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
      <div className="grid grid-cols-[auto_minmax(0,1fr)] text-[12px] leading-relaxed">
        <pre
          aria-hidden
          data-testid="code-line-numbers"
          className="select-none border-r border-minimax-border/50 bg-minimax-panel/40 px-2 py-3 text-right font-mono text-minimax-muted/70"
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
            <pre className="m-0 text-minimax-fg">
              <code>{code}</code>
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

function MermaidBlock({ code }: { code: string }): JSX.Element {
  const rawId = useId();
  const theme = useThemeStore((s) => s.theme);
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  return (
    <div
      data-testid="mermaid-block"
      className="my-2 overflow-hidden rounded-md border border-minimax-border bg-minimax-bg"
    >
      <div className="flex items-center justify-between border-b border-minimax-border/60 bg-minimax-bg/40 px-2 py-1 text-[11px] uppercase tracking-wider text-minimax-muted">
        <span>Mermaid</span>
        <button
          type="button"
          data-testid="mermaid-copy-button"
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
      <div className="min-h-32 overflow-auto p-3">
        {error ? (
          <div
            data-testid="mermaid-error"
            className="flex items-start gap-2 rounded-md border border-red-500/30 bg-red-500/5 p-3 text-xs text-status-error"
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
            className="flex items-center gap-2 rounded-md border border-minimax-border bg-minimax-panel/40 p-3 text-xs text-minimax-muted"
          >
            <Loader2 size={14} className="animate-spin" />
            Rendering diagram...
          </div>
        )}
      </div>
    </div>
  );
}

function FileReferenceStrip({ refs }: { refs: FileReference[] }): JSX.Element | null {
  if (refs.length === 0) return null;
  return (
    <div
      data-testid="file-reference-strip"
      className="mb-2 flex flex-wrap gap-1.5"
      aria-label="Referenced files"
    >
      {refs.map((ref) => (
        <FileReferenceCard key={`${ref.path}:${ref.line ?? ""}`} refInfo={ref} />
      ))}
    </div>
  );
}

function FileReferenceCard({ refInfo }: { refInfo: FileReference }): JSX.Element {
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const parts = refInfo.path.split("/");
  const filename = parts.at(-1) ?? refInfo.path;
  const directory = parts.slice(0, -1).join("/");
  const label = refInfo.line ? `${refInfo.path}:${refInfo.line}` : refInfo.path;

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  return (
    <div
      data-testid="file-reference-card"
      className="group inline-flex max-w-full items-center gap-2 rounded-md border border-minimax-border bg-minimax-bg/60 px-2 py-1 text-[11px] text-minimax-fg transition-colors duration-200 hover:border-minimax-accent/40"
      title={label}
    >
      <FileText size={13} className="shrink-0 text-minimax-muted" />
      <span className="min-w-0">
        <span className="block truncate font-medium leading-tight">{filename}</span>
        {directory && (
          <span className="block max-w-56 truncate leading-tight text-minimax-muted">
            {directory}
          </span>
        )}
      </span>
      {refInfo.line && (
        <span
          data-testid="file-reference-line"
          className="inline-flex shrink-0 items-center gap-0.5 rounded border border-minimax-border bg-minimax-panel px-1 py-0.5 text-[10px] text-minimax-muted"
        >
          <Hash size={9} />
          {refInfo.line}
        </span>
      )}
      <button
        type="button"
        data-testid="file-reference-copy"
        className="inline-flex shrink-0 items-center gap-1 rounded px-1 py-0.5 text-minimax-muted transition-colors duration-200 hover:bg-minimax-border hover:text-minimax-fg"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(label);
            setCopied(true);
            if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
            copyTimerRef.current = setTimeout(() => setCopied(false), 1500);
          } catch {
            // ignore
          }
        }}
        title="Copy file reference"
      >
        {copied ? <Check size={11} /> : <Copy size={11} />}
        <span>{copied ? "Copied" : "Copy"}</span>
      </button>
    </div>
  );
}

const FENCE_LANGS = [
  "mermaid",
  "typescript",
  "javascript",
  "tsx",
  "jsx",
  "python",
  "json",
  "yaml",
  "bash",
  "shell",
  "html",
  "css",
  "sql",
  "toml",
  "rust",
  "go",
  "java",
  "cpp",
  "csharp",
  "markdown",
  "md",
  "py",
  "ts",
  "js",
  "sh",
].sort((a, b) => b.length - a.length);

function normalizeMarkdownForRender(text: string): string {
  if (!text.includes("```")) return text;
  return text
    .split("\n")
    .flatMap((line) => {
      const match = /^(\s*```)(\S+)(.*)$/.exec(line);
      if (!match) return [line];
      const [, fence, rawInfo, rest] = match;
      const lower = rawInfo.toLowerCase();
      const lang = FENCE_LANGS.find((candidate) => lower.startsWith(candidate));
      if (!lang || (rawInfo.length === lang.length && rest.length === 0)) return [line];
      if (rawInfo.length === lang.length && /^\s/.test(rest)) return [line];
      return [`${fence}${rawInfo.slice(0, lang.length)}`, `${rawInfo.slice(lang.length)}${rest}`];
    })
    .join("\n");
}

function MarkdownBody({ text }: { text: string }): JSX.Element {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeKatex]}
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
      {text}
    </ReactMarkdown>
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
  const [messageCopied, setMessageCopied] = useState(false);
  const messageCopyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const summary = useMemo<TurnSummary | null>(() => {
    if (!isAssistant) return null;
    const idx = messages.findIndex((m) => m.id === message.id);
    if (idx < 0) return { thinkingCount: 0, filesViewed: 0, filesModified: 0 };
    return summarizeTurn(messages, idx, message);
  }, [isAssistant, msgCount, messages, message]);
  const renderText = useMemo(
    () => (isUser ? message.text : normalizeMarkdownForRender(message.text || "")),
    [isUser, message.text],
  );
  const fileReferences = useMemo(
    () => extractFileReferences(renderText),
    [renderText],
  );

  // Tool bubbles are collapsible to keep the chat scannable.
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    return () => {
      if (messageCopyTimerRef.current) clearTimeout(messageCopyTimerRef.current);
    };
  }, []);

  const handleCopyMessage = async () => {
    try {
      await navigator.clipboard.writeText(message.text);
      setMessageCopied(true);
      if (messageCopyTimerRef.current) clearTimeout(messageCopyTimerRef.current);
      messageCopyTimerRef.current = setTimeout(() => setMessageCopied(false), 1500);
      toast.success("Message copied");
    } catch {
      toast.error("Copy failed", "Clipboard access was denied.");
    }
  };

  if (isTool) {
    return (
      <div
        data-testid={testId ?? "message-tool"}
        data-role="tool"
        className="flex justify-start"
      >
        <div className="max-w-full rounded-md border border-minimax-border/70 bg-minimax-panel/35 px-2 py-1.5 text-xs transition-colors duration-200 hover:border-minimax-border hover:bg-minimax-panel/60">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex w-full items-center gap-1.5 text-left font-mono text-[11px] text-minimax-muted hover:text-minimax-fg"
            aria-expanded={expanded}
          >
            {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            <span className="truncate">
              {message.tool_name ?? "tool"}
            </span>
            {message.tool_args && (
              <span className="truncate text-[10px] text-minimax-muted/70">
                {Object.keys(message.tool_args).join(", ")}
              </span>
            )}
          </button>
          {expanded && (
            <pre className="mt-1.5 max-h-64 overflow-auto rounded border border-minimax-border/50 bg-minimax-bg/70 px-2 py-1.5 whitespace-pre-wrap break-all text-[11px] text-minimax-fg">
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
            ? "group relative max-w-[80%] rounded-2xl rounded-br-md bg-minimax-accent py-2 pl-9 pr-4 text-sm text-white shadow-sm transition-colors duration-200"
            : isSystem
              ? "max-w-[80%] rounded-md border border-red-500/30 bg-red-500/5 px-3 py-1.5 text-xs italic text-status-error transition-colors duration-200"
              : "group relative max-w-[85%] rounded-2xl rounded-bl-md border py-2 pl-4 pr-9 text-sm shadow-sm transition-colors duration-200 " +
                (isFailed
                  ? "border-red-500/40 bg-red-500/5 text-status-error"
                  : isCancelling
                    ? "border-amber-500/30 bg-amber-500/5 text-minimax-fg"
                    : isCancelled
                      ? "border-minimax-border bg-minimax-panel/70 text-minimax-muted"
                      : "border-minimax-border bg-minimax-panel text-minimax-fg")
        }
      >
        {!isSystem && message.text && (
          <button
            type="button"
            data-testid={`message-copy-${message.id}`}
            onClick={() => void handleCopyMessage()}
            className={
              "absolute top-1.5 flex h-6 w-6 items-center justify-center rounded-md opacity-0 transition-all duration-200 focus:opacity-100 group-hover:opacity-100 " +
              (isUser
                ? "left-1.5 text-white/80 hover:bg-white/15 hover:text-white"
                : "right-1.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg")
            }
            aria-label="Copy message"
            title="Copy message"
          >
            {messageCopied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        )}
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
        <div className={`prose prose-sm max-w-none break-words leading-relaxed [&_.katex-display]:my-3 [&_.katex-display]:overflow-x-auto [&_.katex-display]:overflow-y-hidden [&_.katex-display]:py-1 [&_.katex]:text-[1.02em]${theme === "dark" ? " prose-invert" : ""}`}>
          <FileReferenceStrip refs={fileReferences} />
          {isUser ? (
            <p className="m-0 whitespace-pre-wrap">{renderText}</p>
          ) : (
            <MarkdownBody text={renderText} />
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
