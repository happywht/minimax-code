/**
 * Floating composer.
 *
 * The input now lives in a `fixed bottom` pill that floats above the
 * message list, horizontally centered, capped at 720px wide. Two
 * affordances used to live in the App footer and have moved inline:
 *
 *   - `chat-input-always-allow` — toggles `usePermissionStore.alwaysAllow`,
 *     which makes the IPC layer auto-resolve every `permission.request`
 *     with `allow`. Toggling fires a toast so the user knows they've
 *     armed the bypass.
 *   - `chat-input-model-select` — a slim ModelSelector (variant="inline")
 *     that opens its dropdown upward and stays anchored to the right
 *     edge of the input.
 *
 * The message list in `ChatPanel` reserves bottom padding so the last
 * message doesn't slide under the floating pill.
 */
import { useEffect, useRef, useState } from "react";
import { Paperclip, Send, Shield, ShieldCheck, Square } from "lucide-react";
import { useChat, usePermissionStore } from "../stores";
import { ModelSelector } from "./ModelSelector";
import { toast } from "./ErrorBoundary";

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
  const alwaysAllow = usePermissionStore((s) => s.alwaysAllow);
  const setAlwaysAllow = usePermissionStore((s) => s.setAlwaysAllow);
  const ref = useRef<HTMLTextAreaElement>(null);
  const disabled = status === "sending" || status === "streaming";
  const streaming = status === "streaming" || status === "sending";

  // Auto-grow textarea up to ~8 rows.
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

  const handleToggleAlwaysAllow = () => {
    const next = !alwaysAllow;
    setAlwaysAllow(next);
    toast.info(
      next ? "始终授权已开启" : "始终授权已关闭",
      next
        ? "所有 tool 调用将自动放行（仅当前会话）"
        : "tool 调用将再次弹窗询问",
    );
  };

  return (
    <form
      onSubmit={handleSubmit}
      data-testid={testId}
      data-floating="true"
      className="pointer-events-none fixed inset-x-0 bottom-6 z-20 flex justify-center px-4"
    >
      <div className="pointer-events-auto w-full max-w-[720px] rounded-xl border border-minimax-border bg-minimax-panel/95 shadow-2xl backdrop-blur supports-[backdrop-filter]:bg-minimax-panel/80">
        <div className="flex items-end gap-2 px-2.5 py-2">
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
        <div className="flex items-center justify-between border-t border-minimax-border/60 px-2.5 py-1.5">
          <button
            type="button"
            role="switch"
            aria-checked={alwaysAllow}
            data-testid="chat-input-always-allow"
            onClick={handleToggleAlwaysAllow}
            className={
              "inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] transition-colors " +
              (alwaysAllow
                ? "text-emerald-300 hover:bg-emerald-500/10"
                : "text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg")
            }
            title={
              alwaysAllow
                ? "始终授权已开启 — tool 调用将自动放行"
                : "始终授权：下次 tool 调用前不再询问"
            }
          >
            {alwaysAllow ? <ShieldCheck size={11} /> : <Shield size={11} />}
            {alwaysAllow ? "始终授权：开" : "始终授权"}
          </button>
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-minimax-muted">
              {value.length}/8000
            </span>
            <ModelSelector variant="inline" />
          </div>
        </div>
      </div>
    </form>
  );
}
