/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // ── Existing app palette (preserves internal C object compatibility) ──
        bg: {
          0: '#010608',
          1: '#0a0f14',
          2: '#0f151a',
          3: '#141a20',
        },
        text: {
          1: '#e8e8e8',
          2: '#a0a0a0',
          3: '#606060',
        },
        border: '#1e293b',

        // ── Landing page design system tokens (from ZIP) ──
        void: '#050608',
        'bg-primary': '#080A0E',
        'bg-surface': '#0F1117',
        'bg-elevated': '#151821',
        'bg-overlay': 'rgba(8, 10, 14, 0.85)',
        'border-subtle': '#141922',
        'border-default': '#1E2530',
        'border-active': '#2A3441',
        'text-primary': '#F0F2F5',
        'text-secondary': '#8B95A5',
        'text-muted': '#5A6578',
        'text-inverse': '#080A0E',
        'accent-cyan': '#00D4FF',
        'accent-cyan-dim': 'rgba(0, 212, 255, 0.15)',
        'accent-profit': '#10B981',
        'accent-profit-dim': 'rgba(16, 185, 129, 0.12)',
        'accent-gold': '#F59E0B',
        'accent-gold-dim': 'rgba(245, 158, 11, 0.12)',
        'accent-loss': '#EF4444',
        'accent-loss-dim': 'rgba(239, 68, 68, 0.12)',

        // ── Keep existing named aliases ──
        cyan: '#00d4ff',
        green: '#22c55e',
        red: '#ef4444',
        purple: '#a855f7',
        orange: '#f97316',
        gold: '#fbbf24',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', '"IBM Plex Mono"', '"Fira Code"', 'SF Mono', 'Menlo', 'Consolas', 'monospace'],
      },
      spacing: {
        '18': '4.5rem',
        '22': '5.5rem',
        '88': '22rem',
      },
      boxShadow: {
        'glow': '0 0 20px rgba(0, 212, 255, 0.15)',
        'glow-green': '0 0 20px rgba(34, 197, 94, 0.15)',
      },
      animation: {
        'pulse-glow': 'pulse-glow 3s ease-in-out infinite',
        'gradient-sweep': 'gradient-sweep 2s linear infinite',
        'count-up': 'count-up 1.2s ease-out forwards',
        'shimmer': 'shimmer 1.5s infinite',
      },
      keyframes: {
        'pulse-glow': {
          '0%, 100%': { boxShadow: '0 0 20px rgba(0, 212, 255, 0.15)' },
          '50%': { boxShadow: '0 0 40px rgba(0, 212, 255, 0.3)' },
        },
        'gradient-sweep': {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-1000px 0' },
          '100%': { backgroundPosition: '1000px 0' },
        },
      },
    },
  },
  plugins: [],
}
