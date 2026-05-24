import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // graph palette (used in cy stylesheet too)
        node: {
          drug: "#3b82f6",        // blue
          gene: "#ec4899",        // pink/red
          variant: "#f97316",     // orange
          drug_class: "#a855f7",  // purple
          phenotype: "#eab308",   // yellow
        },
        risk: "#ef4444",
        ink: {
          50: "#f8fafc",
          900: "#0b0f17",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
