/**
 * ReasoningEffortBadge — a compact indicator for a model's reasoning-effort
 * capability (R58 enrich + R59 IPC contract consumer).
 *
 * Renders nothing when the model declares no reasoning-effort meta (zero
 * regression — unsupported models are visually identical to pre-R60). When
 * the model supports reasoning effort, shows the default effort token as a
 * violet mono badge, with a `title` tooltip listing the full selectable
 * menu so a user can see every option without opening a switcher.
 *
 * This is the read-side terminus of the reasoning_effort pipeline:
 *   catalog meta → R53 readers → R58 enrich → R59 IPC contract → *this badge*.
 *
 * Switching effort (write-back to the agent) requires a new IPC method
 * (`model.set_reasoning_effort`) plus backend persistence wiring — deferred
 * to a later round; this component is intentionally read-only.
 */
import type { ModelInfo, ReasoningEffortOption } from "../types/ipc";

export interface ReasoningEffortBadgeProps {
  /**
   * The model slice this badge reads. Accepts a full `ModelInfo` (the common
   * case inside `ModelSelector`) or any `Pick` of the three reasoning-effort
   * fields, so callers never have to reshape their data.
   */
  model: Pick<
    ModelInfo,
    "supports_reasoning_effort" | "reasoning_effort_default" | "reasoning_effort_options"
  >;
  /** Optional test id for the badge root (defaults to "reasoning-effort-badge"). */
  testId?: string;
}

/** Token shown when a model supports reasoning effort but declares no default. */
const DEFAULT_FALLBACK = "auto";

/**
 * Build a concise tooltip describing the selectable reasoning-effort menu.
 * Falls back to the bare default token when the model declares no options.
 */
function formatOptionsTooltip(
  options: ReasoningEffortOption[] | undefined,
  defaultEffort: string,
): string {
  if (!options || options.length === 0) {
    return `Reasoning effort: ${defaultEffort}`;
  }
  const list = options
    .map((o) => `${o.value}${o.default ? " (default)" : ""}`)
    .join(" · ");
  return `Reasoning effort: ${list}`;
}

export function ReasoningEffortBadge({
  model,
  testId = "reasoning-effort-badge",
}: ReasoningEffortBadgeProps): JSX.Element | null {
  if (!model.supports_reasoning_effort) return null;
  const defaultEffort = model.reasoning_effort_default ?? DEFAULT_FALLBACK;
  return (
    <span
      data-testid={testId}
      title={formatOptionsTooltip(model.reasoning_effort_options, defaultEffort)}
      className="shrink-0 rounded bg-violet-500/10 px-1 py-0.5 text-[9px] font-mono text-violet-300"
    >
      effort: {defaultEffort}
    </span>
  );
}
