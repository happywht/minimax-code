/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      colors: {
        // Legacy palette — mapped onto the v0.9 token layer.
        minimax: {
          bg: "var(--minimax-bg)",
          panel: "var(--minimax-panel)",
          border: "var(--minimax-border)",
          fg: "var(--minimax-fg)",
          muted: "var(--minimax-muted)",
          accent: "var(--minimax-accent)",
        },
        // New semantic tokens
        surface: {
          0: "var(--surface-0)",
          1: "var(--surface-1)",
          2: "var(--surface-2)",
          3: "var(--surface-3)",
          overlay: "var(--surface-overlay)",
        },
        line: {
          DEFAULT: "var(--line)",
          strong: "var(--line-strong)",
        },
        ink: {
          0: "var(--ink-0)",
          1: "var(--ink-1)",
          2: "var(--ink-2)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          hover: "var(--accent-hover)",
          active: "var(--accent-active)",
          contrast: "var(--accent-contrast)",
        },
        status: {
          error: "var(--status-error)",
          warning: "var(--status-warning)",
          success: "var(--status-success)",
          info: "var(--status-info)",
        },
      },
      backgroundColor: {
        "accent-subtle": "var(--accent-subtle)",
      },
      boxShadow: {
        pop: "var(--shadow-pop)",
        modal: "var(--shadow-modal)",
      },
      borderRadius: {
        md: "8px",
      },
    },
  },
  plugins: [],
};
