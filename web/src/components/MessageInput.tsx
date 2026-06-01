/**
 * Composer + send button. Uses a local controlled <textarea> so the
 * input doesn't re-render the whole chat tree on every keystroke.
 *
 * Listens for `minimax:suggestion` events from the empty-state
 * suggestions in MessageList.
 */
import { useEffect, useRef, useState } from "react";
import { Paperclip, Send, Square } from "lucide-react";
import { useChat } from "../stores";

export interface MessageInputProps {
  testId?: string;
}

const SUGGESTION_EVENT = "minimax:suggestion";

export function MessageInput({ testId = "message-input" }: MessageInputProps): JSX.Element {
  const [value, setValue] = useState("");
  const status = useChat((s) => s.status);
  const send = useChat((s) => s.send);
  const cancel = useChat((s) => s.cancel);
  const reset = useChat((s) => s.reset);
  const ref = useRef<HTMLTextAreaElement>(null);
  const disabled = status === "sending" || status === "streaming";
  const streaming = status === "streaming" || status === "sending";

  // Auto-grow textarea up to 8 rows.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [value]);

  useEffect(() => {
    const onSuggestion = (e: Event) => {
      const ce = e as CustomEvent<string>;
      if (typeof ce.detail === "string") {
        setValue(ce.detail);
        ref.current?.focus();
      }
    };
    window.addEventListener(SUGGESTION_EVENT, onSuggestion as EventListener);
    return () =>
      window.removeEventListener(SUGGESTION_EVENT, onSuggestion as EventListener);
  }, []);

  const handleSubmit = async (e?: React.FormEvent | React.KeyboardEvent) => {
    e?.preventDefault();
    if (!value.trim() || disabled) return;
    const text = value;
    setValue("");
    await send(text);
  };

  return (
    <form
      onSubmit={handleSubmit}
      data-testid={testId}
      className="border-t border-minimax-border bg-minimax-bg/40 px-4 py-3"
    >
      <div className="mx-auto max-w-3xl">
        <div className="flex items-end gap-2 rounded-xl border border-minimax-border bg-minimax-panel px-2 py-2 shadow-sm focus-within:border-minimax-accent/50">
          <button
            type="button"
            aria-label="Attach file"
            onClick={reset}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            title="Attach (placeholder — uses reset() in mock mode)"
          >
            <Paperclip size={14} />
          </button>
          <textarea
            ref={ref}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void handleSubmit(e);
              }
            }}
            placeholder="Ask MiniMax anything…  (Enter to send · Shift+Enter for newline)"
            rows={1}
            data-testid="message-input-textarea"
            disabled={disabled}
            className="flex-1 resize-none bg-transparent px-1 py-1.5 text-sm text-minimax-fg placeholder:text-minimax-muted focus:outline-none"
          />
          {streaming ? (
            <button
              type="button"
              data-testid="message-input-cancel"
              onClick={() => void cancel()}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-minimax-border text-minimax-fg hover:bg-red-500/20"
              title="Stop"
              aria-label="Stop"
            >
              <Square size={12} />
            </button>
          ) : (
            <button
              type="submit"
              data-testid="message-input-send"
              disabled={!value.trim()}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-minimax-accent text-white disabled:opacity-40"
              title="Send"
              aria-label="Send"
            >
              <Send size={14} />
            </button>
          )}
        </div>
        <div className="mt-1.5 flex items-center justify-between text-[10px] text-minimax-muted">
          <span>Enter to send · Shift+Enter for newline</span>
          <span>Mock IPC: {value.length}/8000 chars</span>
        </div>
      </div>
    </form>
  );
}
