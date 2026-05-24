/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0a0e1a",
        surface: "#111827",
        border: "#1f2937",
        long: "#10b981",
        short: "#ef4444",
        watch: "#f59e0b",
        muted: "#6b7280",
      },
      fontFamily: {
        mono: ["'JetBrains Mono'", "monospace"],
      },
    },
  },
  plugins: [],
};
