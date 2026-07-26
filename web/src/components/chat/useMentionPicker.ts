/**
 * useMentionPicker — the `@agent` autocomplete for the composer.
 *
 * Typing ``@`` in the textarea opens a dropdown listing sub-agents the
 * user can dispatch to. Selecting one (click or arrow + Enter) inserts
 * a marker in the textarea and primes a "send" that invokes
 * ``agent.spawn_subagent`` rather than the regular chat ``send``.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { typedIPC } from "../../ipc";
import { useChat, useSessionStore, useSubAgentStore } from "../../stores";
import { toast } from "../layout/ErrorBoundary";
import type { AgentInfo } from "../../types/ipc";
import {
  INITIAL_MENTION_STATE,
  agentMentionOptions,
  detectMentionToken,
  filterMentionOptions,
  type MentionOption,
  type MentionState,
} from "../../lib/mentions";

export interface MentionPickerOptions {
  /** Override the agent list fetcher (for tests). */
  loadAgents?: () => Promise<AgentInfo[]>;
  value: string;
  setValue: React.Dispatch<React.SetStateAction<string>>;
  textareaRef: React.RefObject<HTMLTextAreaElement>;
}

export interface MentionPicker {
  picker: MentionState;
  filtered: MentionOption[];
  agentsError: string | null;
  closePicker: () => void;
  select: (option: MentionOption) => Promise<void>;
  /** Re-detect the @-token after the draft text changes. */
  updateForInput: (next: string, caret: number) => void;
  /**
   * Picker keyboard navigation. Returns true when the event was
   * consumed (arrows / Enter / Tab / Escape while the picker is open).
   */
  handleKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => boolean;
}

export function useMentionPicker({
  loadAgents,
  value,
  setValue,
  textareaRef,
}: MentionPickerOptions): MentionPicker {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentsError, setAgentsError] = useState<string | null>(null);
  const [picker, setPicker] = useState<MentionState>(INITIAL_MENTION_STATE);
  const addLocalMessage = useChat((s) => s.addLocalMessage);
  const subInit = useSubAgentStore((s) => s.init);
  const subRegister = useSubAgentStore((s) => s.register);

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

  const mentionOptions = useMemo(() => agentMentionOptions(agents), [agents]);
  const filtered = useMemo(
    () => filterMentionOptions(mentionOptions, picker),
    [mentionOptions, picker],
  );

  const closePicker = useCallback(() => setPicker(INITIAL_MENTION_STATE), []);

  const select = useCallback(
    async (option: MentionOption) => {
      const agent = option.payload;
      const el = textareaRef.current;
      // Build the prompt by stripping the @token (everything up to and
      // including the active @ match). The token sits between
      // ``picker.anchor`` and the caret; the part of the textarea
      // after the caret is preserved so multi-line prompts survive.
      const caret = el?.selectionStart ?? value.length;
      const head = value.slice(0, picker.anchor);
      const tail = value.slice(caret);
      const promptText = tail.trim() || "(no prompt)";
      const marker = `@${agent.name} `;
      const newValue = `${head}${marker}${tail}`;
      setValue(newValue);
      closePicker();
      textareaRef.current?.focus();
      // Best-effort: append the user's chosen agent as a sentinel
      // user-message so the chat stream shows the trigger, then
      // spawn the sub-agent.
      const sessionId = useSessionStore.getState().currentSessionId;
      const runId = `run_${Math.random().toString(36).slice(2, 10)}`;
      try {
        await subInit();
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
          agent_name: agent.name,
          prompt: promptText,
          parent_session_id: sessionId ?? undefined,
          display_name: agent.name,
          run_id: runId,
        });
        // Mirror the trigger to the chat stream as a local user msg
        // so the user sees the pick in history — no backend round-trip.
        addLocalMessage(`${marker}${promptText}`);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        subRegister({
          run_id: runId,
          agent_id: agent.id,
          agent_name: agent.name,
          display_name: agent.name,
          parent_session_id: sessionId ?? undefined,
          prompt: promptText,
          status: "failed",
          progress: 1,
          summary: "failed",
          error: message,
          started_at: Date.now(),
          updated_at: Date.now(),
          finished_at: Date.now(),
        });
        toast.error("Sub-agent spawn failed", message);
      }
    },
    [value, picker.anchor, closePicker, setValue, textareaRef, subInit, subRegister, addLocalMessage],
  );

  const updateForInput = useCallback(
    (next: string, caret: number) => {
      // Detect the @-token at-or-before the caret.
      const nextMention = detectMentionToken(next, caret);
      if (nextMention.open) {
        setPicker(nextMention);
      } else if (picker.open) {
        setPicker(INITIAL_MENTION_STATE);
      }
    },
    [picker.open],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>): boolean => {
      if (!picker.open || filtered.length === 0) return false;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setPicker((p) => ({ ...p, cursor: (p.cursor + 1) % filtered.length }));
        return true;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setPicker((p) => ({
          ...p,
          cursor: (p.cursor - 1 + filtered.length) % filtered.length,
        }));
        return true;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        const idx = picker.cursor >= 0 ? picker.cursor : 0;
        e.preventDefault();
        void select(filtered[idx]);
        return true;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closePicker();
        return true;
      }
      return false;
    },
    [picker.open, picker.cursor, filtered, select, closePicker],
  );

  return {
    picker,
    filtered,
    agentsError,
    closePicker,
    select,
    updateForInput,
    handleKeyDown,
  };
}
