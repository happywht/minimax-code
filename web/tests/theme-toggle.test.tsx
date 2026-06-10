import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { ThemeToggle } from "../src/components/ThemeToggle";
import { useThemeStore } from "../src/stores/themeStore";

describe("ThemeToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("light");
    useThemeStore.getState().setTheme("dark");
  });

  it("toggles theme, updates accessible label, and persists the choice", () => {
    render(<ThemeToggle />);

    expect(screen.getByTestId("theme-toggle")).toHaveAccessibleName("Switch to light mode");
    fireEvent.click(screen.getByTestId("theme-toggle"));

    expect(document.documentElement).toHaveClass("light");
    expect(localStorage.getItem("minimax-theme")).toBe("light");
    expect(screen.getByTestId("theme-toggle")).toHaveAccessibleName("Switch to dark mode");
  });
});
