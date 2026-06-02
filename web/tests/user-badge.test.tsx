/**
 * Tests for the UserBadge — verifies initials, badge visibility,
 * the dropdown menu opens / closes, and the inline plan editor
 * reads / writes the persisted plan label.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UserBadge } from "../src/components/UserBadge";

const PLAN_KEY = "minimax-code:plan";

describe("UserBadge", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders initials from the user name and shows the default plan", () => {
    render(<UserBadge name="Ada Lovelace" email="ada@minimax.code" />);
    expect(screen.getByText("AL")).toBeInTheDocument();
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByTestId("user-badge-plan").textContent).toContain("Max Plan");
  });

  it("shows a single initial for single-word names", () => {
    render(<UserBadge name="Linus" email="linus@minimax.code" />);
    expect(screen.getByText("L")).toBeInTheDocument();
  });

  it("opens the dropdown menu on click and shows menu items", () => {
    render(<UserBadge name="Bob" email="b@x.com" />);
    const trigger = screen.getByTestId("user-badge-trigger");
    fireEvent.click(trigger);
    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Sign out")).toBeInTheDocument();
  });

  it("invokes onSignOut when Sign out is clicked", () => {
    const onSignOut = vi.fn();
    render(<UserBadge name="Bob" email="b@x.com" onSignOut={onSignOut} />);
    fireEvent.click(screen.getByTestId("user-badge-trigger"));
    fireEvent.click(screen.getByText("Sign out"));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });

  it("reads the plan from localStorage when present", () => {
    localStorage.setItem(PLAN_KEY, "Pro Plan");
    render(<UserBadge name="Bob" email="b@x.com" />);
    expect(screen.getByTestId("user-badge-plan").textContent).toContain("Pro Plan");
  });

  it("edits the plan inline: click → input → enter persists to localStorage", () => {
    render(<UserBadge name="Bob" email="b@x.com" />);
    const planLabel = screen.getByTestId("user-badge-plan");
    // The plan row contains a role=button span that opens edit mode.
    const editBtn = planLabel.querySelector('[role="button"]') as HTMLElement;
    fireEvent.click(editBtn);

    const input = screen.getByTestId("user-badge-plan-input") as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.value).toBe("Max Plan");

    fireEvent.change(input, { target: { value: "Enterprise Plan" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(localStorage.getItem(PLAN_KEY)).toBe("Enterprise Plan");
    // The plan label now shows the new value.
    expect(screen.getByTestId("user-badge-plan").textContent).toContain("Enterprise Plan");
  });

  it("falls back to the default plan when the user clears the input", () => {
    render(<UserBadge name="Bob" email="b@x.com" />);
    const editBtn = screen
      .getByTestId("user-badge-plan")
      .querySelector('[role="button"]') as HTMLElement;
    fireEvent.click(editBtn);
    const input = screen.getByTestId("user-badge-plan-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("user-badge-plan").textContent).toContain("Max Plan");
  });

  it("Escape cancels an in-progress edit without persisting", () => {
    localStorage.setItem(PLAN_KEY, "Pro Plan");
    render(<UserBadge name="Bob" email="b@x.com" />);
    const editBtn = screen
      .getByTestId("user-badge-plan")
      .querySelector('[role="button"]') as HTMLElement;
    fireEvent.click(editBtn);
    const input = screen.getByTestId("user-badge-plan-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Scribble" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(localStorage.getItem(PLAN_KEY)).toBe("Pro Plan");
    expect(screen.getByTestId("user-badge-plan").textContent).toContain("Pro Plan");
  });
});
