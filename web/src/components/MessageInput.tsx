/**
 * Floating composer with an @-agent picker.
 *
 * v0.3.0 §2: typing ``@`` in the textarea opens a dropdown listing
 * sub-agents the user can dispatch to. Selecting one (click or
 * arrow + Enter) inserts a marker in the textarea and primes a
 * "send" that invokes ``agent.spawn_subagent`` rather than the
 * regular chat ``send``. The plain ``Enter``-without-picker path
 * keeps the existing chat send behaviour.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AtSign, Bot, Paperclip, Send, Shield, ShieldCheck, Square } from "lucide-react";
import { useChat, usePermissionStore, useSubAgentStore } from "../stores";
import { typedIPC } from "../ipc";
import { ModelSelector } from "./ModelSelector";
import { toast } from "./ErrorBoundary";
import type { AgentInfo } from "../types/ipc";
import { useSessionStore } from "../stores";

export interface MessageInputProps {
  testId?: string;
  /** Override the agent list fetcher (for tests). */
  loadAgents?: () => Promise<AgentInfo[]>;
}

const SUGGESTION_EVENT = "minimax:suggestion";

interface PickerState {
  open: boolean;
  query: string;
  /** Index in the filtered list, -1 = none. */
  cursor: number;
  /** Anchor position (textarea character offset) where the @ token starts. */
  anchor: number;
}

const INITIAL_PICKER: PickerState = { open: false, query: "", cursor: -1, anchor: -1 };

export function MessageInput({
  testId = "message-input",
  loadAgents,
}: MessageInputProps): JSX.Element {
  const [value, setValue] = useState("");
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentsError, setAgentsError] = useState<string | null>(null);
  const [picker, setPicker] = useState<PickerState>(INITIAL_PICKER);
  const status = useChat((s) => s.status);
  const send = useChat((s) => s.send);
  const cancel = useChat((s) => s.cancel);
  const reset = useChat((s) => s.reset);
  const alwaysAllow = usePermissionStore((s) => s.alwaysAllow);
  const setAlwaysAllow = usePermissionStore((s) => s.setAlwaysAllow);
  const subInit = useSubAgentStore((s) => s.init);
  const subRegister = useSubAgentStore((s) => s.register);
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

  // Fetch the agent list lazily — used by the @-picker.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = loadAgents
          ? await loadAgents()
          : (await typedIPC.listAgents()).agents;
        if (!cancelled) setAgents(list.filter((a) => a.enabled));
      } catch (err) {
        if (!cancelled) setAgentsError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadAgents]);

  const filtered = useMemo(() => {
    if (!picker.open) return [];
    const q = picker.query.toLowerCase();
    return agents
      .filter(
        (a) =>
          !q ||
          a.id.toLowerCase().includes(q) ||
          a.name.toLowerCase().includes(q),
      )
      .slice(0, 6);
  }, [agents, picker.open, picker.query]);

  const closePicker = useCallback(() => setPicker(INITIAL_PICKER), []);

  const handlePickerSelect = useCallback(
    async (agent: AgentInfo) => {
      const el = ref.current;
      // Build the prompt by stripping the @token (everything up to and
      // including the active @ match). The token sits between
      // ``picker.anchor`` and the caret; the part of the textarea
      // after the caret is preserved so multi-line prompts survive.
      const caret = el?.selectionStart ?? value.length;
      const head = value.slice(0, picker.anchor);
      const tail = value.slice(caret);
      const promptText = tail.trim() || "(no prompt)";
      const marker = `@${agent.id} `;
      const newValue = `${head}${marker}${tail}`;
      setValue(newValue);
      closePicker();
      ref.current?.focus();
      // Best-effort: append the user's chosen agent as a sentinel
      // user-message so the chat stream shows the trigger, then
      // spawn the sub-agent.
      const sessionId = useSessionStore.getState().currentSessionId;
      try {
        await subInit();
        const runId = `run_${Math.random().toString(36).slice(2, 10)}`;
        subRegister({
          run_id: runId,
          agent_id: agent.id,
          agent_name: agent.name,
          display_name: agent.name,
          parent_session_id: sessionId ?? undefined,
          prompt: promptText,
          status: "started",
          progress: 0,
          summary: `queued for ${agent.name}`,
          started_at: Date.now(),
          updated_at: Date.now(),
        });
        await typedIPC.spawnSubagent({
          agent_id: agent.id,
          prompt: promptText,
          parent_session_id: sessionId ?? undefined,
          display_name: agent.name,
        });
        // Mirror the trigger to the chat stream as a normal user msg
        // so the user sees the pick in history.
        await send(`${marker}${promptText}`);
      } catch (err) {
        toast.error("Sub-agent spawn failed", err instanceof Error ? err.message : String(err));
      }
    },
    [value, picker.anchor, closePicker, subInit, subRegister, send],
  );

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    setValue(next);
    // Detect the @-token at-or-before the caret.
    const caret = e.target.selectionStart ?? next.length;
    const head = next.slice(0, caret);
    const match = /(^|\s)@([\w-]*)$/.exec(head);
    if (match) {
      const anchor = caret - match[2].length - 1; // -1 for the "@" itself
      setPicker({ open: true, query: match[2], cursor: 0, anchor });
    } else if (picker.open) {
      setPicker(INITIAL_PICKER);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (picker.open && filtered.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setPicker((p) => ({ ...p, cursor: (p.cursor + 1) % filtered.length }));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setPicker((p) => ({
          ...p,
          cursor: (p.cursor - 1 + filtered.length) % filtered.length,
        }));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        const idx = picker.cursor >= 0 ? picker.cursor : 0;
        e.preventDefault();
        void handlePickerSelect(filtered[idx]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closePicker();
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSubmit(e);
    }
  };

  const handleSubmit = async (e?: React.FormEvent | React.KeyboardEvent) => {
    e?.preventDefault();
    if (!value.trim() || disabled) return;
    const text = value;
    setValue("");
    closePicker();
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
      <div className="pointer-events-auto relative w-full max-w-[720px] rounded-xl border border-minimax-border bg-minimax-panel/95 shadow-2xl backdrop-blur supports-[backdrop-filter]:bg-minimax-panel/80">
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
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder="Ask MiniMax anything…  (Enter to send · @agent to spawn a sub-agent)"
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
        {picker.open && (
          <div
            data-testid="message-input-agent-picker"
            className="absolute bottom-full left-2.5 right-2.5 mb-1.5 overflow-hidden rounded-md border border-minimax-border bg-minimax-panel shadow-lg"
          >
            <div className="flex items-center gap-1 border-b border-minimax-border/60 px-2 py-1 text-[10px] uppercase tracking-wider text-minimax-muted">
              <AtSign size={10} />
              <span>Spawn sub-agent</span>
            </div>
            {agentsError ? (
              <div className="px-2 py-1.5 text-[10px] text-red-300" title={agentsError}>
                Failed to load agents
              </div>
            ) : filtered.length === 0 ? (
              <div className="px-2 py-1.5 text-[10px] italic text-minimax-muted">
                No agents match “{picker.query}”
              </div>
            ) : (
              <ul data-testid="message-input-agent-picker-list">
                {filtered.map((a, i) => (
                  <li key={a.id}>
                    <button
                      type="button"
                      data-testid={`message-input-agent-picker-item-${a.id}`}
                      onClick={() => void handlePickerSelect(a)}
                      className={
                        "flex w-full items-center gap-2 px-2 py-1 text-left text-[11px] " +
                        (i === picker.cursor
                          ? "bg-minimax-accent/10 text-minimax-fg"
                          : "text-minimax-fg/90 hover:bg-minimax-border/40")
                      }
                    >
                      <Bot size={10} className="text-minimax-accent" />
                      <span className="font-medium">{a.name}</span>
                      <span className="font-mono text-[10px] text-minimax-muted">
                        @{a.id}
                      </span>
                      <span className="flex-1 truncate text-[10px] text-minimax-muted">
                        {a.description}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
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
