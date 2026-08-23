/**
 * Context usage indicator — shows how much of the model's context
 * window is consumed by the current session.
 *
 * Uses the LAST assistant message's usage footprint (``tokens_in +
 * tokens_out``) against the current model's ``context_window``.
 * ``tokens_in`` is what that LLM call actually saw — it already
 * contains the whole prior history plus the system prompt — so the
 * latest call's footprint IS the current context size. Summing it
 * across turns (the pre-v1.1.3 behaviour) would count the history
 * once per turn and overstate usage by design. Renders a thin
 * progress bar with colour coding:
 *
 * - Green  (< 70%): plenty of room.
 * - Amber  (70–90%): getting crowded.
 * - Red    (> 90%): context nearly full.
 *
 * If the model has no ``context_window`` or no messages have metadata,
 * the component shows a graceful fallback.
 */

import { useMemo } from "react";
import { useChat } from "../../stores/chat";
import { useModelStore } from "../../stores/modelStore";
import { strings } from "../../ui/strings";

/** Format a token count for display (e.g. 1234 → "1.2k"). */
function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function ContextIndicator() {
  const messages = useChat((s) => s.messages);
  const models = useModelStore((s) => s.models);
  const currentModelId = useModelStore((s) => s.current);

  const { used, total, pct } = useMemo(() => {
    // Take the latest assistant message that reported usage. Its
    // tokens_in is the full prompt the model just saw (history +
    // system prompt included), so it already IS the current context
    // size; tokens_out approximates what the next call will add.
    let tokensUsed = 0;
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg.role === "assistant" && msg.metadata?.tokens_in) {
        tokensUsed = msg.metadata.tokens_in + (msg.metadata.tokens_out || 0);
        break;
      }
    }

    // Find the current model's context window.
    const currentModel = models.find((m) => m.id === currentModelId);
    const contextWindow = currentModel?.context_window ?? 0;

    const percentage = contextWindow > 0 ? (tokensUsed / contextWindow) * 100 : 0;

    return { used: tokensUsed, total: contextWindow, pct: percentage };
  }, [messages, models, currentModelId]);

  // Graceful fallback: no model data or no usage yet.
  if (total <= 0 && used <= 0) return null;

  // Colour classes based on usage percentage.
  const barColor =
    pct < 70
      ? "bg-emerald-400"
      : pct < 90
        ? "bg-amber-400"
        : "bg-red-400";

  const textColor =
    pct < 70
      ? "text-status-success"
      : pct < 90
        ? "text-status-warning"
        : "text-status-error";

  return (
    <div className="flex items-center gap-1.5" title={strings.chat.context.title(fmtTokens(used), total > 0 ? fmtTokens(total) : "?")}>
      {/* Thin progress bar */}
      <div className="h-1 w-12 rounded-full bg-minimax-border/40 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-300 ${barColor}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      {/* Text label */}
      <span className={`text-[11px] tabular-nums ${textColor}`}>
        {fmtTokens(used)}{total > 0 ? `/${fmtTokens(total)}` : ""}
      </span>
    </div>
  );
}
