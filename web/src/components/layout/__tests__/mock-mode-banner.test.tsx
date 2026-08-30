/**
 * MockModeBanner tests (v1.8.0 R3) — the mock-mode banner must be
 * absent while talking to a real agent and self-describing when the
 * UI degraded to the in-process mock backend.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MockModeBanner } from "../MockModeBanner";
import { strings } from "../../../ui/strings";

describe("MockModeBanner (v1.8.0 R3)", () => {
  it("renders nothing when inactive (real agent session)", () => {
    const { container } = render(<MockModeBanner active={false} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("announces mock backend state with title and detail when active", () => {
    render(<MockModeBanner active={true} />);
    const banner = screen.getByTestId("mock-banner");
    expect(banner.getAttribute("role")).toBe("status");
    expect(banner.textContent).toContain(strings.layout.mock.title);
    expect(banner.textContent).toContain(strings.layout.mock.detail);
  });
});
