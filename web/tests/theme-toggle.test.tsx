import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { ThemeToggle } from "../src/components/layout/ThemeToggle";
import { useThemeStore } from "../src/stores/themeStore";

describe("ThemeToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("light");
    document.documentElement.classList.remove("dark");
    useThemeStore.getState().setTheme("dark");
  });

  it("toggles theme, updates accessible label, and persists the choice", () => {
    render(<ThemeToggle />);

    expect(document.documentElement).toHaveClass("dark");
    expect(document.documentElement).not.toHaveClass("light");
    expect(screen.getByTestId("theme-toggle")).toHaveAccessibleName("Switch to light mode");
    fireEvent.click(screen.getByTestId("theme-toggle"));

    expect(document.documentElement).toHaveClass("light");
    expect(document.documentElement).not.toHaveClass("dark");
    expect(localStorage.getItem("minimax-theme")).toBe("light");
    expect(screen.getByTestId("theme-toggle")).toHaveAccessibleName("Switch to dark mode");
  });
});
