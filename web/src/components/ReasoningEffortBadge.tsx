/**
 * ReasoningEffortBadge — a compact indicator + (R63) switcher for a model's
 * reasoning-effort capability (R58 enrich + R59 IPC contract consumer).
 *
 * Two render modes (zero-regression upgrade over R60):
 *  * Read-only badge — when `onEffortChange` is omitted, the component is
 *    byte-identical to R60: a violet mono token with a `title` tooltip listing
 *    the selectable menu. Existing call sites and the 6 R60 tests hit this
 *    branch unchanged.
 *  * Switcher (R63) — when `onEffortChange` is supplied, the badge becomes a
 *    button that opens a dropdown of the model's `reasoning_effort_options`
 *    plus a "Default" entry. Selecting an option fires `onEffortChange(token)`;
 *    "Default" fires `onEffortChange(null)` (clears the override, reverting to
 *    the model's own default effort — the pre-R61 state).
 *
 * Display priority: an explicit user override (`currentEffort`) wins over the
 * model's own default; both fall back to "auto" when neither is declared.
 *
 * This is the read-side terminus AND (R63) the write-side origin of the
 * reasoning_effort pipeline:
 *   catalog meta → R53 readers → R58 enrich → R59 IPC contract → *this badge*
 *     → R63 store → R63 typedIPC `model.set_reasoning_effort` → R61 backend
 *     → R62 runtime → R55/R54 transport → wire.
 *
 * Renders nothing when the model declares no reasoning-effort meta (zero
 * regression — unsupported models are visually identical to pre-R60).
 */
import { useEffect, useRef, useState } from "react";
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
  /**
   * The persisted reasoning-effort override (R63). When supplied, the badge
   * reflects it instead of the model's own default. ``null`` / undefined means
   * "no override — use the model's default effort" (the pre-R61 state).
   */
  currentEffort?: string | null;
  /**
   * When supplied, the badge upgrades to a switcher: clicking opens a dropdown
   * and selecting an option fires this callback (the write-back origin). Omit
   * to keep the R60 read-only behaviour.
   */
  onEffortChange?: (effort: string | null) => void;
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
  currentEffort,
  onEffortChange,
  testId = "reasoning-effort-badge",
}: ReasoningEffortBadgeProps): JSX.Element | null {
  if (!model.supports_reasoning_effort) return null;

  const modelDefault = model.reasoning_effort_default ?? undefined;
  const displayDefault = modelDefault ?? DEFAULT_FALLBACK;

  // Read-only badge (R60): no write callback ⇒ render the plain token. This is
  // the zero-regression path — existing call sites and the 6 R60 tests never
  // supply `onEffortChange`, so they hit this branch byte-identically to R60.
  if (!onEffortChange) {
    return (
      <span
        data-testid={testId}
        title={formatOptionsTooltip(model.reasoning_effort_options, displayDefault)}
        className="shrink-0 rounded bg-violet-500/10 px-1 py-0.5 text-[11px] font-mono text-violet-300"
      >
        effort: {displayDefault}
      </span>
    );
  }

  // Switcher (R63): the override wins for display; else the model default;
  // else the "auto" fallback.
  const displayEffort =
    currentEffort && currentEffort.trim() !== "" ? currentEffort : displayDefault;

  return (
    <EffortSwitcher
      options={model.reasoning_effort_options}
      modelDefault={modelDefault ?? null}
      currentEffort={currentEffort ?? null}
      displayEffort={displayEffort}
      onEffortChange={onEffortChange}
      testId={testId}
    />
  );
}

// ---------------------------------------------------------------------------
// Switcher (R63 write-back UI)
// ---------------------------------------------------------------------------

interface EffortSwitcherProps {
  options: ReasoningEffortOption[] | undefined;
  modelDefault: string | null;
  currentEffort: string | null;
  displayEffort: string;
  onEffortChange: (effort: string | null) => void;
  testId: string;
}

function EffortSwitcher({
  options,
  modelDefault,
  currentEffort,
  displayEffort,
  onEffortChange,
  testId,
}: EffortSwitcherProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);

  // Close on outside pointer-down / Escape — standard dropdown affordance.
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const opts = options ?? [];
  // The effective selection highlighted in the menu: the override if set,
  // else the model's declared default option (if any). "Default" row is
  // highlighted separately when no override is active.
  const effectiveSelection = currentEffort ?? modelDefault;

  const choose = (value: string | null) => {
    onEffortChange(value);
    setOpen(false);
  };

  return (
    <span ref={rootRef} data-testid={testId} className="relative shrink-0">
      <button
        type="button"
        data-testid={`${testId}-trigger`}
        title={formatOptionsTooltip(options, displayEffort)}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className="flex items-center gap-0.5 rounded bg-violet-500/10 px-1 py-0.5 text-[11px] font-mono text-violet-300 hover:bg-violet-500/20 focus:outline-none focus:ring-1 focus:ring-violet-400"
      >
        <span>effort: {displayEffort}</span>
        <span aria-hidden="true" className="text-[8px] leading-none">
          ▾
        </span>
      </button>
      {open && (
        <ul
          role="listbox"
          data-testid={`${testId}-menu`}
          className="absolute right-0 top-full z-50 mt-1 min-w-[8rem] overflow-hidden rounded border border-white/10 bg-[#1a1a2e] py-1 text-[11px] shadow-lg"
        >
          <li>
            <button
              type="button"
              data-testid={`${testId}-option-default`}
              onClick={(e) => {
                e.stopPropagation();
                choose(null);
              }}
              className={`flex w-full items-center justify-between px-2 py-1 text-left hover:bg-white/5 ${
                currentEffort == null ? "text-violet-300" : "text-gray-300"
              }`}
            >
              <span>Default</span>
              {currentEffort == null && <span aria-hidden="true">✓</span>}
            </button>
          </li>
          {opts.map((o) => {
            const selected = effectiveSelection === o.value;
            return (
              <li key={o.value}>
                <button
                  type="button"
                  data-testid={`${testId}-option-${o.value}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    choose(o.value);
                  }}
                  className={`flex w-full items-center justify-between px-2 py-1 text-left hover:bg-white/5 ${
                    selected ? "text-violet-300" : "text-gray-300"
                  }`}
                >
                  <span>
                    {o.label ?? o.value}
                    {o.default ? " (default)" : ""}
                  </span>
                  {selected && <span aria-hidden="true">✓</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </span>
  );
}
