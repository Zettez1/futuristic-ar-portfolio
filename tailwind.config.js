/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./privacy.html", "./js/*.js"],
  theme: {
    extend: {
      fontFamily: {
        display: ["Space Grotesk", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"]
      }
    }
  },
  plugins: []
};