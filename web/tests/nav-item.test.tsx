/**
 * Tests for the NavItem component — the building block of the
 * sidebar.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Star } from "lucide-react";
import { NavItem } from "../src/components/layout/NavItem";

describe("NavItem", () => {
  it("renders label and icon", () => {
    render(
      <NavItem
        icon={<Star data-testid="icon" />}
        label="Favorites"
        testId="nav-item"
      />,
    );
    expect(screen.getByTestId("nav-item")).toBeInTheDocument();
    expect(screen.getByText("Favorites")).toBeInTheDocument();
    expect(screen.getByTestId("icon")).toBeInTheDocument();
  });

  it("calls onClick when clicked", () => {
    const onClick = vi.fn();
    render(<NavItem icon={<Star />} label="X" onClick={onClick} testId="x" />);
    fireEvent.click(screen.getByTestId("x"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("shows the count badge when count > 0", () => {
    render(<NavItem icon={<Star />} label="Y" count={7} testId="y" />);
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("hides the count badge when count is 0 or undefined", () => {
    render(<NavItem icon={<Star />} label="Z" count={0} testId="z" />);
    expect(screen.queryByText("0")).toBeNull();
  });

  it("marks the selected state with data-selected", () => {
    render(
      <NavItem
        icon={<Star />}
        label="A"
        selected
        onClick={() => undefined}
        testId="a"
      />,
    );
    expect(screen.getByTestId("a")).toHaveAttribute("data-selected", "true");
  });
});
