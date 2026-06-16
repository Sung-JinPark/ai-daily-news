/** @type {import('tailwindcss').Config} */
export default {
  content: ["./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "Pretendard", "system-ui", "sans-serif"],
        serif: ['"Noto Serif KR"', '"IBM Plex Serif"', "Georgia", "serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      colors: {
        ink: {
          DEFAULT: "#0a0a0a",
          soft: "#1a1a1a",
        },
        accent: {
          DEFAULT: "#be1622",
          soft: "#f5e9ea",
        },
      },
      letterSpacing: {
        kicker: "0.18em",
      },
      fontSize: {
        kicker: ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.18em" }],
      },
    },
  },
  plugins: [],
};
