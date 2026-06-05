/**
 * ThemeToggle — Sun / Moon icon button that flips the theme.
 *
 * Reads ``useThemeStore`` and calls ``toggle()`` on click.
 * Intentionally tiny — no popover, no label, just the icon.
 */

import { Moon, Sun } from "lucide-react";
import { useThemeStore } from "../stores/themeStore";

export interface ThemeToggleProps {
  testId?: string;
}

export function ThemeToggle({ testId = "theme-toggle" }: ThemeToggleProps): JSX.Element {
  const theme = useThemeStore((s) => s.theme);
  const toggle = useThemeStore((s) => s.toggle);

  return (
    <button
      type="button"
      data-testid={testId}
      onClick={toggle}
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      title={theme === "dark" ? "Light mode" : "Dark mode"}
      className="flex h-7 w-7 items-center justify-center rounded-md text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
    >
      {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
    </button>
  );
}
