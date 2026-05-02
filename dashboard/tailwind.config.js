/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          900: "#080a0f",
          800: "#0f1320",
          700: "#161b2c",
          600: "#1c2236",
          500: "#293152",
        },
        accent: {
          DEFAULT: "#6ee7b7",
          alt: "#60a5fa",
        },
        warn: "#fbbf24",
        bad: "#f87171",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Inter",
          "system-ui",
          "sans-serif",
        ],
        mono: ["ui-monospace", "JetBrains Mono", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
