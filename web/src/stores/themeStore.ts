/**
 * Theme store — dark / light toggle backed by localStorage.
 *
 * On init the store reads ``localStorage("minimax-theme")`` and
 * applies the corresponding class to ``document.documentElement``.
 * Calling ``toggle()`` flips the value, persists it, and updates
 * the class — no page reload needed.
 */

import { create } from "zustand";

export type Theme = "dark" | "light";

export interface ThemeState {
  theme: Theme;
  toggle: () => void;
  setTheme: (t: Theme) => void;
}

const STORAGE_KEY = "minimax-theme";

function readStored(): Theme {
  if (typeof window === "undefined") return "dark";
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark") return v;
  } catch {
    // localStorage unavailable (SSR / privacy mode)
  }
  return "dark";
}

function applyTheme(t: Theme): void {
  if (typeof document === "undefined") return;
  const el = document.documentElement;
  if (t === "light") {
    el.classList.add("light");
  } else {
    el.classList.remove("light");
  }
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: readStored(),

  toggle: () => {
    const next = get().theme === "dark" ? "light" : "dark";
    set({ theme: next });
    applyTheme(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Ignore write failures.
    }
  },

  setTheme: (t: Theme) => {
    set({ theme: t });
    applyTheme(t);
    try {
      localStorage.setItem(STORAGE_KEY, t);
    } catch {
      // Ignore write failures.
    }
  },
}));

// Apply on module import so the class is set before first paint.
applyTheme(readStored());
