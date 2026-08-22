/**
 * Tests for the md–lg inspector drawer (P2-2) — between 768px and
 * 1024px the inline inspector column is hidden, so a TopBar button
 * opens the same <RightPanel /> in an overlay drawer instead.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

vi.mock("../src/ipc/client", () => ({
  typedIPC: new Proxy(
    {},
    {
      get: () => vi.fn(async () => ({ entries: [], total: 0 })),
    },
  ),
  ipc: { on: vi.fn(() => vi.fn()) },
}));

import { TopBar } from "../src/components/layout/TopBar";
import { InspectorDrawer } from "../src/components/layout/InspectorDrawer";
import { useSessionStore } from "../src/stores/sessionStore";
import { strings } from "../src/ui/strings";

beforeEach(() => {
  vi.clearAllMocks();
  useSessionStore.setState({ sessions: [], projects: [], loading: false });
});

describe("TopBar inspector button (P2-2)", () => {
  it("renders the button when the handler is provided and fires it on click", () => {
    const onToggle = vi.fn();
    render(<TopBar onToggleInspector={onToggle} />);

    const btn = screen.getByTestId("app-topbar-inspector");
    expect(btn).toHaveAttribute("aria-label", strings.layout.topbar.toggleInspector);
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("omits the button when no handler is passed", () => {
    render(<TopBar />);
    expect(screen.queryByTestId("app-topbar-inspector")).not.toBeInTheDocument();
  });
});

describe("InspectorDrawer (P2-2)", () => {
  it("renders a labelled dialog with its children when open", () => {
    render(
      <InspectorDrawer open onClose={() => {}}>
        <div data-testid="drawer-child">panel</div>
      </InspectorDrawer>,
    );

    const dialog = screen.getByTestId("inspector-drawer");
    expect(dialog).toHaveAttribute("role", "dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-label", strings.rightPanel.shell.inspector);
    expect(screen.getByTestId("drawer-child")).toBeInTheDocument();
  });

  it("calls onClose when the backdrop is clicked", () => {
    const onClose = vi.fn();
    render(
      <InspectorDrawer open onClose={onClose}>
        <div>panel</div>
      </InspectorDrawer>,
    );

    fireEvent.click(screen.getByTestId("inspector-drawer-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders nothing when closed", () => {
    render(
      <InspectorDrawer open={false} onClose={() => {}}>
        <div data-testid="drawer-child">panel</div>
      </InspectorDrawer>,
    );

    expect(screen.queryByTestId("inspector-drawer")).not.toBeInTheDocument();
    expect(screen.queryByTestId("drawer-child")).not.toBeInTheDocument();
  });
});
