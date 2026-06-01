import { useEffect, useRef } from "react";
import { useChat } from "../stores";

/**
 * Renders the message list with auto-scroll. Each message is a bubble
 * aligned left (assistant) or right (user). System messages (errors)
 * are centered and italic.
 */
export function ChatPanel() {
  const messages = useChat((s) => s.messages);
  const status = useChat((s) => s.status);
  const error = useChat((s) => s.error);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current as (HTMLDivElement & { scrollTo?: (o: unknown) => void }) | null;
    if (el && typeof el.scrollTo === "function") {
      el.scrollTo({
        top: el.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [messages]);

  return (
    <div className="flex flex-1 flex-col">
      <div className="border-b border-minimax-border px-4 py-2 text-xs text-minimax-muted">
        Status: <span className="font-mono">{status}</span>
        {error && <span className="ml-2 text-red-400">— {error}</span>}
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 ? (
          <div className="mt-12 text-center text-minimax-muted">
            <p className="text-lg">Welcome to MiniMax Code (skeleton)</p>
            <p className="text-sm mt-2">
              Type <span className="font-mono">hello</span> to test the IPC
              round-trip.
            </p>
          </div>
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              className={
                m.role === "user"
                  ? "flex justify-end"
                  : m.role === "system"
                    ? "flex justify-center"
                    : "flex justify-start"
              }
            >
              <div
                className={
                  m.role === "user"
                    ? "max-w-[80%] rounded-lg bg-minimax-accent px-3 py-2 text-white"
                    : m.role === "system"
                      ? "max-w-[80%] italic text-red-300"
                      : "max-w-[80%] rounded-lg bg-minimax-panel border border-minimax-border px-3 py-2"
                }
              >
                <div className="whitespace-pre-wrap break-words text-sm font-mono">
                  {m.text}
                  {m.streaming && (
                    <span className="ml-0.5 inline-block w-2 animate-pulse">
                      ▍
                    </span>
                  )}
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
