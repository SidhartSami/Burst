/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        base: "#0a0a0f",
        panel: "#111118",
        accent: "#3b82f6",
        success: "#22c55e"
      }
    }
  },
  plugins: []
};
