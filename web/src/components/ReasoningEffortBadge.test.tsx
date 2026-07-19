import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ReasoningEffortBadge } from "./ReasoningEffortBadge";
import type { ModelInfo, ReasoningEffortOption } from "../types/ipc";

/**
 * Tests for {@link ReasoningEffortBadge} — the read-side UI terminus of the
 * reasoning_effort pipeline (R53 readers → R58 enrich → R59 contract → here).
 *
 * Coverage focus: zero-regression (unsupported models render nothing), the
 * default-token render, the options-menu tooltip, and the `auto` fallback.
 */
function slice(
  fields: Partial<ModelInfo>,
): Pick<
  ModelInfo,
  "supports_reasoning_effort" | "reasoning_effort_default" | "reasoning_effort_options"
> {
  return fields;
}

describe("ReasoningEffortBadge", () => {
  it("renders nothing when the model declares no reasoning-effort meta (zero regression)", () => {
    const { container } = render(<ReasoningEffortBadge model={slice({})} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when supports_reasoning_effort is false", () => {
    const { container } = render(
      <ReasoningEffortBadge model={slice({ supports_reasoning_effort: false })} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders the default effort token when the model supports reasoning effort", () => {
    render(
      <ReasoningEffortBadge
        model={slice({
          supports_reasoning_effort: true,
          reasoning_effort_default: "high",
          reasoning_effort_options: [
            { value: "low", id: "low", label: "Low", description: null, default: false },
            { value: "high", id: "high", label: "High", description: null, default: true },
          ],
        })}
      />,
    );
    expect(screen.getByTestId("reasoning-effort-badge")).toHaveTextContent("effort: high");
  });

  it("lists the full selectable menu in the tooltip", () => {
    render(
      <ReasoningEffortBadge
        model={slice({
          supports_reasoning_effort: true,
          reasoning_effort_default: "high",
          reasoning_effort_options: [
            { value: "low", id: "low", label: "Low", description: null, default: false },
            { value: "high", id: "high", label: "High", description: null, default: true },
          ],
        })}
      />,
    );
    const title = screen.getByTestId("reasoning-effort-badge").getAttribute("title") ?? "";
    expect(title).toContain("low");
    expect(title).toContain("high");
    expect(title).toContain("default");
  });

  it("falls back to 'auto' when the supports flag is set but no default declared", () => {
    render(<ReasoningEffortBadge model={slice({ supports_reasoning_effort: true })} />);
    expect(screen.getByTestId("reasoning-effort-badge")).toHaveTextContent("effort: auto");
  });

  it("renders a standalone default token in the tooltip when options are absent", () => {
    render(
      <ReasoningEffortBadge
        model={slice({
          supports_reasoning_effort: true,
          reasoning_effort_default: "medium",
        })}
      />,
    );
    const title = screen.getByTestId("reasoning-effort-badge").getAttribute("title") ?? "";
    expect(title).toBe("Reasoning effort: medium");
  });
});

// ---------------------------------------------------------------------------
// Switcher (R63) — write-back UI tests.
// ---------------------------------------------------------------------------
//
// When `onEffortChange` is supplied the badge upgrades from a read-only token
// (R60) to a clickable dropdown. These tests pin the switcher contract: the
// trigger reflects the active effort, the menu lists every option plus a
// "Default" (clear-override) row, selecting an option fires the write-back
// callback with the right token, and the active row is checkmarked.
//
// The 6 read-only tests above never supply `onEffortChange`, so they keep
// exercising the R60 branch byte-identically (zero-regression guarantee).

const SWITCHER_OPTS: ReasoningEffortOption[] = [
  { value: "low", id: "low", label: "Low", description: null, default: false },
  { value: "high", id: "high", label: "High", description: null, default: true },
];

describe("ReasoningEffortBadge switcher (R63)", () => {
  it("renders a trigger button when onEffortChange is supplied", () => {
    render(
      <ReasoningEffortBadge
        model={slice({
          supports_reasoning_effort: true,
          reasoning_effort_default: "high",
          reasoning_effort_options: SWITCHER_OPTS,
        })}
        currentEffort={null}
        onEffortChange={() => undefined}
      />,
    );
    const trigger = screen.getByTestId("reasoning-effort-badge-trigger");
    expect(trigger).toBeInTheDocument();
    // No override ⇒ the model's own default effort is shown.
    expect(trigger).toHaveTextContent("effort: high");
  });

  it("reflects the currentEffort override instead of the model default", () => {
    render(
      <ReasoningEffortBadge
        model={slice({
          supports_reasoning_effort: true,
          reasoning_effort_default: "high",
          reasoning_effort_options: SWITCHER_OPTS,
        })}
        currentEffort="low"
        onEffortChange={() => undefined}
      />,
    );
    expect(screen.getByTestId("reasoning-effort-badge-trigger")).toHaveTextContent(
      "effort: low",
    );
  });

  it("opens the menu on trigger click and lists all options plus Default", () => {
    render(
      <ReasoningEffortBadge
        model={slice({
          supports_reasoning_effort: true,
          reasoning_effort_default: "high",
          reasoning_effort_options: SWITCHER_OPTS,
        })}
        currentEffort={null}
        onEffortChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("reasoning-effort-badge-trigger"));
    expect(screen.getByTestId("reasoning-effort-badge-menu")).toBeInTheDocument();
    expect(screen.getByTestId("reasoning-effort-badge-option-low")).toBeInTheDocument();
    expect(screen.getByTestId("reasoning-effort-badge-option-high")).toBeInTheDocument();
    expect(screen.getByTestId("reasoning-effort-badge-option-default")).toBeInTheDocument();
  });

  it("fires onEffortChange with the chosen token", () => {
    const onChange = vi.fn();
    render(
      <ReasoningEffortBadge
        model={slice({
          supports_reasoning_effort: true,
          reasoning_effort_default: "high",
          reasoning_effort_options: SWITCHER_OPTS,
        })}
        currentEffort={null}
        onEffortChange={onChange}
      />,
    );
    fireEvent.click(screen.getByTestId("reasoning-effort-badge-trigger"));
    fireEvent.click(screen.getByTestId("reasoning-effort-badge-option-low"));
    expect(onChange).toHaveBeenCalledWith("low");
  });

  it("fires onEffortChange(null) when Default is chosen (clears override)", () => {
    const onChange = vi.fn();
    render(
      <ReasoningEffortBadge
        model={slice({
          supports_reasoning_effort: true,
          reasoning_effort_default: "high",
          reasoning_effort_options: SWITCHER_OPTS,
        })}
        currentEffort="low"
        onEffortChange={onChange}
      />,
    );
    fireEvent.click(screen.getByTestId("reasoning-effort-badge-trigger"));
    fireEvent.click(screen.getByTestId("reasoning-effort-badge-option-default"));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("highlights the active override option with a checkmark", () => {
    render(
      <ReasoningEffortBadge
        model={slice({
          supports_reasoning_effort: true,
          reasoning_effort_default: "high",
          reasoning_effort_options: SWITCHER_OPTS,
        })}
        currentEffort="low"
        onEffortChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("reasoning-effort-badge-trigger"));
    // The override ("low") is the active selection ⇒ checkmarked.
    expect(screen.getByTestId("reasoning-effort-badge-option-low")).toHaveTextContent("✓");
    // The model default ("high") is not the active override ⇒ no checkmark.
    expect(screen.getByTestId("reasoning-effort-badge-option-high")).not.toHaveTextContent("✓");
  });

  it("highlights Default when no override is active", () => {
    render(
      <ReasoningEffortBadge
        model={slice({
          supports_reasoning_effort: true,
          reasoning_effort_default: "high",
          reasoning_effort_options: SWITCHER_OPTS,
        })}
        currentEffort={null}
        onEffortChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("reasoning-effort-badge-trigger"));
    expect(screen.getByTestId("reasoning-effort-badge-option-default")).toHaveTextContent("✓");
  });

  it("closes the menu after selecting an option", () => {
    render(
      <ReasoningEffortBadge
        model={slice({
          supports_reasoning_effort: true,
          reasoning_effort_default: "high",
          reasoning_effort_options: SWITCHER_OPTS,
        })}
        currentEffort={null}
        onEffortChange={() => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("reasoning-effort-badge-trigger"));
    expect(screen.getByTestId("reasoning-effort-badge-menu")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("reasoning-effort-badge-option-low"));
    expect(screen.queryByTestId("reasoning-effort-badge-menu")).not.toBeInTheDocument();
  });

  it("stays read-only when onEffortChange is omitted (zero-regression R60 path)", () => {
    // No onEffortChange ⇒ no trigger button, just the plain R60 span.
    render(
      <ReasoningEffortBadge
        model={slice({
          supports_reasoning_effort: true,
          reasoning_effort_default: "high",
          reasoning_effort_options: SWITCHER_OPTS,
        })}
      />,
    );
    expect(screen.queryByTestId("reasoning-effort-badge-trigger")).not.toBeInTheDocument();
    expect(screen.getByTestId("reasoning-effort-badge")).toHaveTextContent("effort: high");
  });
});
