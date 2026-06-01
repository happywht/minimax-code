/**
 * Tests for the UserBadge — verifies initials, badge visibility, and
 * the dropdown menu opens / closes.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UserBadge } from "../src/components/UserBadge";

describe("UserBadge", () => {
  it("renders initials from the user name", () => {
    render(<UserBadge name="Ada Lovelace" email="ada@minimax.code" />);
    expect(screen.getByText("AL")).toBeInTheDocument();
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("Max Plan")).toBeInTheDocument();
  });

  it("shows a single initial for single-word names", () => {
    render(<UserBadge name="Linus" email="linus@minimax.code" />);
    expect(screen.getByText("L")).toBeInTheDocument();
  });

  it("opens the dropdown menu on click and shows menu items", () => {
    render(<UserBadge name="Bob" email="b@x.com" />);
    const trigger = screen.getByRole("button");
    fireEvent.click(trigger);
    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Sign out")).toBeInTheDocument();
  });

  it("invokes onSignOut when Sign out is clicked", () => {
    const onSignOut = vi.fn();
    render(<UserBadge name="Bob" email="b@x.com" onSignOut={onSignOut} />);
    fireEvent.click(screen.getByRole("button"));
    fireEvent.click(screen.getByText("Sign out"));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });
});
