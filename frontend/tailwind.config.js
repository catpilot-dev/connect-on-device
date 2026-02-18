/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{svelte,js,ts}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        surface: {
          50: '#f0f1f4',
          100: '#d1d5de',
          200: '#a3aab8',
          300: '#747d93',
          400: '#565f73',
          500: '#3a4254',
          600: '#2a3040',
          700: '#1e2433',
          800: '#161b28',
          900: '#0f131d',
          950: '#090c13',
        },
        engage: {
          green: '#22c55e',
          blue: '#3b82f6',
          orange: '#f59e0b',
          red: '#ef4444',
          grey: '#6b7280',
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
}
