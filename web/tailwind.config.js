/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
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
          bg: "#0b0d10",
          panel: "#14181d",
          border: "#22272e",
          fg: "#e6e9ef",
          muted: "#8a93a6",
          accent: "#7c8cff",
        },
      },
    },
  },
  plugins: [],
};
