/**
 * ThemeToggle — Sun / Moon icon button that flips the theme.
 *
 * Reads ``useThemeStore`` and calls ``toggle()`` on click.
 * Intentionally tiny — no popover, no label, just the icon.
 */

import { Moon, Sun } from "lucide-react";
import { IconButton } from "../../ui/IconButton";
import { strings } from "../../ui/strings";
import { useThemeStore } from "../../stores/themeStore";

export interface ThemeToggleProps {
  testId?: string;
}

export function ThemeToggle({ testId = "theme-toggle" }: ThemeToggleProps): JSX.Element {
  const theme = useThemeStore((s) => s.theme);
  const toggle = useThemeStore((s) => s.toggle);

  return (
    <IconButton
      type="button"
      data-testid={testId}
      onClick={toggle}
      aria-label={theme === "dark" ? strings.layout.theme.toLight : strings.layout.theme.toDark}
      title={theme === "dark" ? strings.layout.theme.light : strings.layout.theme.dark}
    >
      {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
    </IconButton>
  );
}
