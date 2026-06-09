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
        minimax: {
          bg: "var(--minimax-bg)",
          panel: "var(--minimax-panel)",
          border: "var(--minimax-border)",
          fg: "var(--minimax-fg)",
          muted: "var(--minimax-muted)",
          accent: "var(--minimax-accent)",
        },
        status: {
          error: "var(--status-error)",
          warning: "var(--status-warning)",
          success: "var(--status-success)",
          info: "var(--status-info)",
        },
      },
    },
  },
  plugins: [],
};
