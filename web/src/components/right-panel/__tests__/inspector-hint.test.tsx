/**
 * Inspector tab-strip discoverability hint tests (v1.8.0 R3) — the
 * one-time hint under the icon tab bar shows on first sight of the
 * panel, disappears on dismiss, and the dismissal persists via
 * localStorage so it never nags again (v1.7.1 walkthrough finding).
 */
import { describe, it, expect, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RightPanel } from "../../RightPanel";
import { strings } from "../../../ui/strings";

describe("Inspector tab hint (v1.8.0 R3)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("shows the hint on first sight of the expanded panel", () => {
    render(<RightPanel initialAgents={[]} />);
    const hint = screen.getByTestId("inspector-tab-hint");
    expect(hint.textContent).toContain(strings.rightPanel.shell.hint.text);
  });

  it("dismiss removes the hint and persists via localStorage", () => {
    render(<RightPanel initialAgents={[]} />);
    fireEvent.click(screen.getByTestId("inspector-tab-hint-dismiss"));
    expect(screen.queryByTestId("inspector-tab-hint")).toBeNull();
    expect(localStorage.getItem("minimax_inspector_hint_seen")).toBe("1");

    // A fresh mount (new session) must not show it again.
    const { unmount } = render(<RightPanel initialAgents={[]} />);
    expect(screen.queryByTestId("inspector-tab-hint")).toBeNull();
    unmount();
  });

  it("stays hidden from the start when already dismissed previously", () => {
    localStorage.setItem("minimax_inspector_hint_seen", "1");
    render(<RightPanel initialAgents={[]} />);
    expect(screen.queryByTestId("inspector-tab-hint")).toBeNull();
  });
});
