/**
 * useComposerDraft — owns the draft text, the textarea ref, auto-grow
 * behaviour, the external "suggestion" prefill event, and the derived
 * character-limit state (usage %, near/critical/over thresholds).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { MAX_INPUT_CHARS, SUGGESTION_EVENT } from "./constants";

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

export function useComposerDraft(): ComposerDraft {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [value, setValue] = useState("");

  // Auto-grow textarea up to ~8 rows — shrink back when text is deleted.
  // Setting height to "0px" first forces the browser to recalculate
  // scrollHeight from the actual content, preventing stale height values.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [value]);

  // Other components (e.g. MessageList suggestions) prefill the composer
  // by dispatching a custom window event.
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

  const clear = useCallback(() => setValue(""), []);

  const overLimit = value.length > MAX_INPUT_CHARS;
  const usagePct = Math.min(100, (value.length / MAX_INPUT_CHARS) * 100);
  const nearLimit = usagePct >= 80;
  const critical = usagePct >= 95 || overLimit;

  return { ref, value, setValue, clear, overLimit, usagePct, nearLimit, critical };
}
