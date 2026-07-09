/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        arena: {
          bg: '#05070B',
          card: '#10141B',
          border: 'rgba(255,255,255,.06)',
          'border-hover': 'rgba(255,255,255,.12)',
          'text-primary': '#FFFFFF',
          'text-secondary': '#8B92A5',
          'text-dim': '#5A6275',
          blue: '#3B82F6',
          green: '#10B981',
          orange: '#F59E0B',
          purple: '#8B5CF6',
          red: '#EF4444',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'SF Mono', 'Menlo', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      animation: {
        'fade-in': 'fadeIn 0.3s ease-in-out',
        'slide-in': 'slideIn 0.4s ease-out',
        'pulse-glow': 'pulseGlow 2s ease-in-out infinite',
        'flash-green': 'flashGreen 0.6s ease-out',
        'flash-red': 'flashRed 0.6s ease-out',
        'scroll': 'scroll 30s linear infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideIn: {
          '0%': { transform: 'translateX(100%)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
        pulseGlow: {
          '0%, 100%': { boxShadow: '0 0 4px rgba(139,92,246,.3)' },
          '50%': { boxShadow: '0 0 16px rgba(139,92,246,.6)' },
        },
        flashGreen: {
          '0%': { backgroundColor: 'rgba(16,185,129,.2)' },
          '100%': { backgroundColor: 'transparent' },
        },
        flashRed: {
          '0%': { backgroundColor: 'rgba(239,68,68,.2)' },
          '100%': { backgroundColor: 'transparent' },
        },
        scroll: {
          '0%': { transform: 'translateX(0)' },
          '100%': { transform: 'translateX(-50%)' },
        },
      },
    },
  },
  plugins: [],
}
