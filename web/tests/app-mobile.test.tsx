/**
 * B4 mobile shell tests — the App-level sidebar drawer behaviour that
 * the mobile-viewport e2e spec covers visually, pinned here at the
 * component level (jsdom cannot apply responsive CSS, so these tests
 * assert interaction semantics only):
 *
 *   - the drawer dialog is absent until the hamburger toggles it in
 *   - the backdrop click dismisses it
 *   - picking a destination in the drawer closes it (App wires
 *     onViewChange to also setSidebarOpen(false)) and opens the
 *     corresponding workspace overlay
 */
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import App from "../src/App";

beforeAll(() => {
  if (typeof globalThis.crypto === "undefined") {
    Object.defineProperty(globalThis, "crypto", {
      value: { randomUUID: () => "test-uuid" },
      configurable: true,
    });
  }
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new Error("network disabled in app tests"))),
  );
});

afterAll(() => {
  vi.unstubAllGlobals();
});

describe("App mobile sidebar drawer (B4)", () => {
  it("does not render the drawer dialog until the hamburger is clicked", async () => {
    render(<App />);
    expect(
      screen.queryByRole("dialog", { name: "导航" }),
    ).not.toBeInTheDocument();
    // The hamburger affordance itself is present (its md:hidden class
    // only hides it visually on desktop viewports; jsdom keeps it in
    // the a11y tree, which is what the click path needs).
    expect(screen.getByTestId("app-topbar-hamburger")).toBeInTheDocument();
  });

  it("opens the labelled drawer on hamburger click and closes it via backdrop", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("app-topbar-hamburger"));

    const drawer = await screen.findByRole("dialog", { name: "导航" });
    expect(drawer).toBeInTheDocument();

    // The backdrop is the dialog's first child covering the viewport.
    fireEvent.click(drawer.firstChild as HTMLElement);
    expect(
      screen.queryByRole("dialog", { name: "导航" }),
    ).not.toBeInTheDocument();
  });

  it("closes the drawer when a navigation destination is picked", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("app-topbar-hamburger"));

    const drawer = await screen.findByRole("dialog", { name: "导航" });
    // The drawer renders its own <Sidebar /> instance; clicking a nav
    // item both switches the view and closes the drawer.
    const navInsideDrawer = drawer.querySelector('[data-testid="sidebar-nav-settings"]');
    expect(navInsideDrawer).not.toBeNull();
    fireEvent.click(navInsideDrawer as HTMLElement);

    expect(
      screen.queryByRole("dialog", { name: "导航" }),
    ).not.toBeInTheDocument();
    // Settings opened as the workspace overlay in the main pane.
    expect(await screen.findByTestId("workspace-overlay")).toBeInTheDocument();
  });
});
