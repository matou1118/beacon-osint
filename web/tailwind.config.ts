import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: '#fafaf9',
        surface: '#ffffff',
        ink: {
          900: '#18181b',
          700: '#3f3f46',
          600: '#52525b',
          400: '#a1a1aa',
          300: '#d4d4d8',
        },
        accent: {
          // 颜色跟主题走（store/theme.ts 写入的 CSS 变量），见 shared/config/themes.ts
          DEFAULT: 'rgb(var(--accent) / <alpha-value>)',
          hover: 'rgb(var(--accent-hover) / <alpha-value>)',
          soft: 'rgb(var(--accent) / 0.08)',
        },
        topbar: 'rgb(var(--top) / <alpha-value>)',
        sidebar: 'rgb(var(--side) / <alpha-value>)',
        ok: '#15803d',
        warn: '#b45309',
        danger: '#b91c1c',
        info: '#1e40af',
      },
      fontFamily: {
        sans: ['"Geist Sans"', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"Geist Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        sm: '6px',
        md: '8px',
        lg: '12px',
        xl: '16px',
      },
      boxShadow: {
        card: '0 1px 2px rgba(24,24,27,0.04), 0 1px 3px rgba(24,24,27,0.06)',
        pop: '0 4px 12px rgba(24,24,27,0.08), 0 2px 4px rgba(24,24,27,0.04)',
      },
      transitionTimingFunction: {
        spring: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'pulse-soft': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.5' },
        },
      },
      animation: {
        'fade-in': 'fade-in 240ms cubic-bezier(0.16, 1, 0.3, 1)',
        'pulse-soft': 'pulse-soft 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
} satisfies Config
