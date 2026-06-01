/**
 * Scrollable list of messages. Auto-scrolls to bottom on new messages
 * and during streaming, but only if the user is already near the bottom
 * (so they can scroll up to read history without being yanked back).
 */
import { useEffect, useRef } from "react";
import { useChat } from "../stores";
import { MessageItem } from "./MessageItem";

export interface MessageListProps {
  testId?: string;
}

export function MessageList({ testId = "message-list" }: MessageListProps): JSX.Element {
  const messages = useChat((s) => s.messages);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stuckAtBottom = useRef(true);

  // Track whether the user has scrolled away from the bottom.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      stuckAtBottom.current = distance < 80;
    };
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Auto-scroll on new content (only when stuck to bottom).
  useEffect(() => {
    const el = scrollRef.current as
      | (HTMLDivElement & { scrollTo?: (o: { top: number; behavior?: string }) => void })
      | null;
    if (!el) return;
    if (stuckAtBottom.current && typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
  }, [messages]);

  return (
    <div
      ref={scrollRef}
      data-testid={testId}
      className="flex-1 overflow-y-auto px-4 py-4"
    >
      {messages.length === 0 ? (
        <div
          data-testid="empty-state"
          className="mx-auto mt-16 max-w-md text-center"
        >
          <div className="mx-auto mb-3 h-10 w-10 rounded-xl bg-minimax-accent/20 text-minimax-accent flex items-center justify-center text-lg font-semibold">
            ✦
          </div>
          <h2 className="text-base font-medium text-minimax-fg">
            How can I help you today?
          </h2>
          <p className="mt-1 text-xs text-minimax-muted">
            Ask me to refactor code, explain a file, run a command, or set up
            a scheduled task.
          </p>
          <div className="mt-6 grid grid-cols-1 gap-2 text-left text-xs text-minimax-muted">
            <Suggestion text="Refactor src/foo.py to use dataclasses" />
            <Suggestion text="Explain what the IPC bridge does" />
            <Suggestion text="Set up a daily 9am test reminder" />
          </div>
        </div>
      ) : (
        <div className="mx-auto flex max-w-3xl flex-col gap-3">
          {messages.map((m) => (
            <MessageItem key={m.id} message={m} />
          ))}
        </div>
      )}
    </div>
  );
}

function Suggestion({ text }: { text: string }): JSX.Element {
  return (
    <button
      type="button"
      className="rounded-md border border-minimax-border bg-minimax-panel px-3 py-2 text-left text-minimax-fg hover:border-minimax-accent/40"
      onClick={() => {
        // The composer reads its value from local state, so we just
        // fire a custom event the MessageInput listens for. This keeps
        // the components decoupled.
        window.dispatchEvent(new CustomEvent("minimax:suggestion", { detail: text }));
      }}
    >
      {text}
    </button>
  );
}
