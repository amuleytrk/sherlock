/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        bg:           "var(--bg)",
        surface:      "var(--surface)",
        "surface-2":  "var(--surface-2)",
        "surface-3":  "var(--surface-3)",
        ink:          "var(--ink)",
        "ink-dim":    "var(--ink-dim)",
        "ink-muted":  "var(--ink-muted)",
        outline:      "var(--outline)",
        "outline-soft": "var(--outline-soft)",
        primary:      "var(--primary)",
        "primary-fg": "var(--primary-fg)",
        success:      "var(--success)",
        warn:         "var(--warn)",
        danger:       "var(--danger)",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ['"Space Grotesk"', "ui-monospace", "monospace"],
      },
      borderRadius: {
        DEFAULT: "4px",
        sm:  "2px",
        md:  "6px",
        lg:  "8px",
        xl:  "12px",
        full: "9999px",
      },
    },
  },
  plugins: [],
};
