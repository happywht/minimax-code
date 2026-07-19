import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReasoningEffortBadge } from "./ReasoningEffortBadge";
import type { ModelInfo } from "../types/ipc";

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
