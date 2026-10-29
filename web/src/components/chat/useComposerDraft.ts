/**
 * useComposerDraft — owns the draft text, the textarea ref, auto-grow
 * behaviour, the external "suggestion" prefill event, and the derived
 * character-limit state (usage %, near/critical/over thresholds).
 *
 * The draft is scoped per session and mirrored to localStorage:
 * typing in one session leaves other sessions' drafts untouched, and
 * a reload (or an agent crash mid-composition) restores the text.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { MAX_INPUT_CHARS, SUGGESTION_EVENT } from "./constants";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";

const DRAFT_KEY_PREFIX = "minimax-code:draft:";

function readDraft(sessionId: string | null): string {
  if (!sessionId) return "";
  try {
    return window.localStorage.getItem(DRAFT_KEY_PREFIX + sessionId) ?? "";
  } catch {
    return "";
  }
}

function writeDraft(sessionId: string | null, value: string): void {
  if (!sessionId) return;
  try {
    if (value) window.localStorage.setItem(DRAFT_KEY_PREFIX + sessionId, value);
    else window.localStorage.removeItem(DRAFT_KEY_PREFIX + sessionId);
  } catch {
    // Storage unavailable (private mode / quota) — the draft simply
    // won't survive a reload.
  }
}

export interface ComposerDraft {
  ref: React.RefObject<HTMLTextAreaElement>;
  value: string;
  setValue: React.Dispatch<React.SetStateAction<string>>;
  /** Reset the draft to empty. */
  clear: () => void;
  overLimit: boolean;
  usagePct: number;
  nearLimit: boolean;
  critical: boolean;
}

/** Draft text plus the session it belongs to, swapped in lockstep. */
interface DraftState {
  id: string | null;
  text: string;
}

export function useComposerDraft(sessionId: string | null): ComposerDraft {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState<DraftState>(() => ({
    id: sessionId,
    text: readDraft(sessionId),
  }));

  // Session switch: swap in the other session's saved draft. Adjusting
  // state during render (React's recommended pattern for prop-driven
  // resets) keeps text and id from ever being committed out of sync.
  // The outgoing session's text is already persisted keystroke by
  // keystroke below, so there is nothing to save here.
  if (draft.id !== sessionId) {
    setDraft({ id: sessionId, text: readDraft(sessionId) });
  }

  const draftRef = useRef(draft);
  draftRef.current = draft;

  // Mirror every edit under the session-scoped key (empty → remove).
  useEffect(() => {
    writeDraft(draft.id, draft.text);
  }, [draft]);

  const setValue = useCallback<React.Dispatch<React.SetStateAction<string>>>(
    (next) => {
      setDraft((prev) => ({
        ...prev,
        text: typeof next === "function" ? next(prev.text) : next,
      }));
    },
    [],
  );

  // Auto-grow textarea up to ~8 rows — shrink back when text is deleted.
  // Setting height to "0px" first forces the browser to recalculate
  // scrollHeight from the actual content, preventing stale height values.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [draft.text]);

  // Other components (e.g. MessageList suggestions) prefill the composer
  // by dispatching a custom window event.
  useEffect(() => {
    const onSuggestion = (e: Event) => {
      const ce = e as CustomEvent<string>;
      if (typeof ce.detail === "string") {
        setValue((previous) => previous ? `${previous}\n\n${ce.detail}` : ce.detail);
        ref.current?.focus();
      }
    };
    window.addEventListener(SUGGESTION_EVENT, onSuggestion as EventListener);
    return () =>
      window.removeEventListener(SUGGESTION_EVENT, onSuggestion as EventListener);
  }, [setValue]);

  // This event is acknowledged only by the composer for the same live scope.
  useEffect(() => {
    const appendSource = (event: Event) => {
      const detail = (event as CustomEvent<{ host: string; project: string; session: string; text: string; result?: string }>).detail;
      const current = useSessionStore.getState(), previous = draftRef.current;
      if (!detail || typeof detail.text !== "string" || ipc.agentBaseUrl !== detail.host ||
          current.currentProjectId !== detail.project || current.currentSessionId !== detail.session ||
          previous.id !== detail.session || sessionId !== detail.session) return;
      const next = previous.text ? `${previous.text}\n\n${detail.text}` : detail.text;
      if (next.length > MAX_INPUT_CHARS) { detail.result = "草稿加上来源后超过输入上限，请先精简草稿。"; return; }
      // Update the ref immediately so two rapid selections cannot overwrite one another.
      draftRef.current = { id: previous.id, text: next };
      setDraft((state) => state.id === detail.session ? { ...state, text: next } : state);
      detail.result = "已添加到当前会话草稿，尚未发送。";
    };
    window.addEventListener("minimax:append-research-source", appendSource);
    return () => window.removeEventListener("minimax:append-research-source", appendSource);
  }, [sessionId]);

  const clear = useCallback(() => setValue(""), [setValue]);

  const overLimit = draft.text.length > MAX_INPUT_CHARS;
  const usagePct = Math.min(100, (draft.text.length / MAX_INPUT_CHARS) * 100);
  const nearLimit = usagePct >= 80;
  const critical = usagePct >= 95 || overLimit;

  return { ref, value: draft.text, setValue, clear, overLimit, usagePct, nearLimit, critical };
}
