import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Play, RefreshCw, Square, TerminalSquare } from "lucide-react";
import { useSessionStore, useTerminalStore } from "../stores";
import type { TerminalChunk, TerminalSession } from "../types/ipc";

export interface TerminalPanelProps {
  testId?: string;
}

const EMPTY_CHUNKS: TerminalChunk[] = [];

export function TerminalPanel({ testId = "terminal-panel" }: TerminalPanelProps): JSX.Element {
  const sessions = useTerminalStore((s) => s.sessions);
  const order = useTerminalStore((s) => s.order);
  const activeId = useTerminalStore((s) => s.activeId);
  const chunksBySession = useTerminalStore((s) => s.chunks);
  const loading = useTerminalStore((s) => s.loading);
  const error = useTerminalStore((s) => s.error);
  const setActive = useTerminalStore((s) => s.setActive);
  const list = useTerminalStore((s) => s.list);
  const start = useTerminalStore((s) => s.start);
  const read = useTerminalStore((s) => s.read);
  const stop = useTerminalStore((s) => s.stop);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const [command, setCommand] = useState("");
  const [cwd, setCwd] = useState("");
  const [autoFollow, setAutoFollow] = useState(true);
  const outputRef = useRef<HTMLPreElement | null>(null);

  const active = activeId ? sessions[activeId] : null;
  const chunks = activeId ? chunksBySession[activeId] ?? EMPTY_CHUNKS : EMPTY_CHUNKS;
  const running = active?.status === "starting" || active?.status === "running";

  useEffect(() => {
    void list();
  }, [list]);

  useEffect(() => {
    if (!activeId || !running) return;
    const timer = window.setInterval(() => {
      void read(activeId);
    }, 700);
    return () => window.clearInterval(timer);
  }, [activeId, read, running]);

  useEffect(() => {
    if (!autoFollow || !outputRef.current) return;
    scrollOutputToBottom(outputRef.current);
  }, [autoFollow, chunks.length, active?.status]);

  const output = useMemo(
    () =>
      chunks.map((chunk) => ({
        key: `${chunk.seq}-${chunk.stream}`,
        text: chunk.text,
        stream: chunk.stream,
      })),
    [chunks],
  );

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void start({
      command,
      cwd: cwd.trim() || undefined,
      session_id: currentSessionId,
    }).then((session) => {
      if (session) {
        setCommand("");
        setAutoFollow(true);
      }
    });
  };

  const handleScroll = () => {
    const node = outputRef.current;
    if (!node) return;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    setAutoFollow(distance <= 50);
  };

  return (
    <div data-testid={testId} className="flex min-h-0 flex-1 flex-col px-3 pb-3">
      <form onSubmit={handleSubmit} className="sticky top-0 z-10 bg-minimax-panel pb-2 pt-3">
        <div className="flex items-center gap-1.5">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-minimax-border bg-minimax-bg/40 text-minimax-muted">
            <TerminalSquare size={13} />
          </div>
          <input
            data-testid={`${testId}-command`}
            value={command}
            onChange={(event) => setCommand(event.target.value)}
            className="h-8 min-w-0 flex-1 rounded border border-minimax-border bg-minimax-bg px-2 font-mono text-[11px] text-minimax-fg outline-none transition-colors duration-200 placeholder:text-minimax-muted focus:border-minimax-accent/70"
            placeholder="pnpm test"
          />
          <button
            type="submit"
            data-testid={`${testId}-run`}
            disabled={loading || !command.trim()}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-minimax-accent/15 text-minimax-accent transition-colors duration-200 hover:bg-minimax-accent/25 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Run command"
            title="Run command"
          >
            {loading ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
          </button>
          <button
            type="button"
            data-testid={`${testId}-refresh`}
            onClick={() => activeId && void read(activeId)}
            disabled={!activeId}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded text-minimax-muted transition-colors duration-200 hover:bg-minimax-border/70 hover:text-minimax-fg disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Refresh terminal"
            title="Refresh terminal"
          >
            <RefreshCw size={13} />
          </button>
          <button
            type="button"
            data-testid={`${testId}-stop`}
            onClick={() => activeId && void stop(activeId)}
            disabled={!activeId || !running}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded text-minimax-muted transition-colors duration-200 hover:bg-red-500/15 hover:text-status-error disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Stop command"
            title="Stop command"
          >
            <Square size={12} />
          </button>
        </div>
        <input
          data-testid={`${testId}-cwd`}
          value={cwd}
          onChange={(event) => setCwd(event.target.value)}
          className="mt-1 h-7 w-full rounded border border-minimax-border bg-minimax-bg px-2 font-mono text-[10px] text-minimax-muted outline-none transition-colors duration-200 placeholder:text-minimax-muted/70 focus:border-minimax-accent/60"
          placeholder="cwd (default workspace root)"
        />
      </form>

      {error && (
        <div
          data-testid={`${testId}-error`}
          className="mb-2 rounded border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-[11px] text-status-error"
          title={error}
        >
          Terminal error
        </div>
      )}

      {order.length > 0 && (
        <div
          data-testid={`${testId}-sessions`}
          className="mb-2 flex gap-1 overflow-x-auto border-b border-minimax-border pb-2"
        >
          {order.map((id) => (
            <SessionPill
              key={id}
              session={sessions[id]}
              active={id === activeId}
              onClick={() => setActive(id)}
              testId={`${testId}-session-${id}`}
            />
          ))}
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        {!active ? (
          <div
            data-testid={`${testId}-empty`}
            className="rounded border border-minimax-border bg-minimax-bg/30 px-2 py-4 text-center text-[11px] italic text-minimax-muted"
          >
            No terminal sessions
          </div>
        ) : (
          <>
            <pre
              ref={outputRef}
              data-testid={`${testId}-output`}
              onScroll={handleScroll}
              className="h-full min-h-48 overflow-auto rounded border border-minimax-border bg-[#07090d] px-2 py-2 font-mono text-[10px] leading-relaxed text-minimax-fg"
            >
              <TerminalHeader session={active} />
              {output.map((chunk) => (
                <span
                  key={chunk.key}
                  className={chunk.stream === "stderr" ? "text-status-error" : "text-minimax-fg"}
                >
                  {chunk.text}
                </span>
              ))}
              {running && <span className="inline-block h-3 w-1 animate-pulse bg-minimax-accent align-middle" />}
            </pre>
            {!autoFollow && (
              <button
                type="button"
                data-testid={`${testId}-new-output`}
                onClick={() => {
                  setAutoFollow(true);
                  if (outputRef.current) scrollOutputToBottom(outputRef.current);
                }}
                className="absolute bottom-2 right-2 rounded border border-minimax-border bg-minimax-panel px-2 py-1 text-[11px] text-minimax-accent shadow-lg"
              >
                New output
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function scrollOutputToBottom(node: HTMLPreElement): void {
  if (typeof node.scrollTo === "function") {
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
    return;
  }
  node.scrollTop = node.scrollHeight;
}

function SessionPill({
  session,
  active,
  onClick,
  testId,
}: {
  session?: TerminalSession;
  active: boolean;
  onClick: () => void;
  testId: string;
}): JSX.Element | null {
  if (!session) return null;
  const tone =
    session.status === "running" || session.status === "starting"
      ? "bg-status-warning"
      : session.status === "completed"
        ? "bg-status-success"
        : session.status === "failed" || session.status === "cancelled"
          ? "bg-status-error"
          : "bg-minimax-muted";
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className={[
        "inline-flex max-w-36 shrink-0 items-center gap-1 rounded px-1.5 py-1 text-[10px] transition-colors duration-200",
        active ? "bg-minimax-accent/15 text-minimax-accent" : "text-minimax-muted hover:bg-minimax-border/70",
      ].join(" ")}
      title={session.command}
    >
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${tone}`} />
      <span className="truncate font-mono">{session.command}</span>
    </button>
  );
}

function TerminalHeader({ session }: { session: TerminalSession }): JSX.Element {
  return (
    <span className="block text-minimax-muted">
      $ {session.command}
      {"\n"}
      [{session.status}
      {session.exit_code !== null ? `:${session.exit_code}` : ""}] {session.cwd}
      {session.run_id ? `\nrun ${session.run_id.slice(0, 12)}` : ""}
      {"\n\n"}
    </span>
  );
}
