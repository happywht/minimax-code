import { useState } from "react";
import { useChat } from "../stores";

/**
 * Composer + send button at the bottom of the chat panel.
 * Submitting triggers `chat.send(content)`, which round-trips through
 * the Tauri sidecar to the Python agent.
 */
export function MessageInput() {
  const [value, setValue] = useState("");
  const status = useChat((s) => s.status);
  const send = useChat((s) => s.send);
  const disabled = status === "sending" || status === "streaming";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!value.trim() || disabled) return;
    const text = value;
    setValue("");
    await send(text);
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="border-t border-minimax-border p-4 bg-minimax-panel"
    >
      <div className="flex items-end gap-2">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSubmit(e as unknown as React.FormEvent);
            }
          }}
          placeholder="Type 'hello' and press Enter to round-trip through the agent…"
          rows={2}
          className="flex-1 resize-none rounded-md border border-minimax-border bg-minimax-bg p-2 text-sm focus:outline-none focus:ring-1 focus:ring-minimax-accent"
          disabled={disabled}
        />
        <button
          type="submit"
          disabled={disabled || !value.trim()}
          className="rounded-md bg-minimax-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {status === "sending"
            ? "Sending…"
            : status === "streaming"
              ? "Streaming…"
              : "Send"}
        </button>
      </div>
    </form>
  );
}
