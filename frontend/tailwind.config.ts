import type { Config } from 'tailwindcss'

/**
 * The Tailwind config is TypeScript so `sourceMeta.test.ts` can IMPORT it and
 * assert the palette matches, rather than grepping the file as text. Two
 * definitions of "which colour is Olist" is two chances to disagree, and the
 * failure is invisible - a badge in one orange and a chart in another on the
 * same screen.
 */
const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // The four sources each own a colour, and that mapping is used by the
        // source badge, the chart series and the overview cards. It is defined
        // ONCE here rather than repeated as hex values in components, because a
        // palette that disagrees with itself is how a dashboard ends up showing
        // Olist in two different colours on the same screen.
        source: {
          olist: '#f97316',
          dataco: '#38bdf8',
          dunnhumby: '#a78bfa',
          m5: '#34d399',
        },
        ink: {
          // Premium white + gold theme.
          //
          // MAIN BACKGROUND (page): #FFFFFF.
          // SECONDARY BACKGROUND (cards/hero): #FAF9F6.
          950: '#ffffff',
          900: '#1F1F1F',
          850: '#f5f3ec',
          800: '#334155',
          700: '#475569',
          // Text tiers are dark enough to stay readable on the white page and
          // cream cards. The lighter 500 tier is reserved for quiet hints but
          // still clears 4.5:1 contrast on both surfaces.
          600: '#6B6B6B',
          500: '#707070',
          400: '#7c8ba1',
          300: '#9B9B9B',
          200: '#B3B3B3',
          100: '#E0E0E0',
          50: '#F5F5F5',
        },
        // `badge-info` keeps its blue identity so a status colour still
        // carries meaning - it sits on white, not on the dark palette.
        sky: {
          500: '#3B82F6',
          600: '#2563EB',
        },
        gold: {
          700: '#8A6400',
          600: '#A67C00',
          500: '#C9A227',
          400: '#A67C00',
          50: '#FAF6E8',
        },
      },
      fontFamily: {
        sans: [
          'Segoe UI',
          'system-ui',
          '-apple-system',
          'Roboto',
          'Helvetica Neue',
          'Arial',
          'Noto Sans',
          'Liberation Sans',
          'ui-sans-serif',
          'sans-serif',
        ],
        mono: [
          'ui-monospace',
          'SFMono-Regular',
          'Cascadia Mono',
          'Consolas',
          'Menlo',
          'DejaVu Sans Mono',
          'Liberation Mono',
          'monospace',
        ],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(2 6 23 / 0.4), 0 1px 3px 0 rgb(2 6 23 / 0.3)',
        raised: '0 1px 0 0 rgb(255 255 255 / 0.04) inset, 0 8px 24px -10px rgb(2 6 23 / 0.8)',
        pop: '0 0 0 1px rgb(30 41 59 / 0.9), 0 18px 40px -14px rgb(2 6 23 / 0.85)',
      },
      border: {
        // Premium white + gold border.
        main: '#E8E3D5',
      },
      ring: {
        600: '#A67C00',
      },
      backgroundImage: {
        // The hero wash. A barely-there warm tint so the top of the page
        // reads as a distinct region rather than as a flat white plane.
        'hero-wash':
          'linear-gradient(135deg, rgb(201 162 39 / 0.14) 0%, rgb(166 124 0 / 0.08) 38%, transparent 72%)',
        'source-wash':
          'linear-gradient(180deg, rgb(255 255 255 / 0.025) 0%, transparent 100%)',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
        'rise-in': {
          // One entrance, two properties, 240ms. Deliberately not a bounce or
          // a slide from the left: this is a data product and motion exists
          // here to make an arriving thing legible, not to be noticed.
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'pulse-ring': {
          '0%': { boxShadow: '0 0 0 0 rgb(56 189 248 / 0.45)' },
          '70%': { boxShadow: '0 0 0 6px rgb(56 189 248 / 0)' },
          '100%': { boxShadow: '0 0 0 0 rgb(56 189 248 / 0)' },
        },
      },
      animation: {
        // `animation-fill-mode` is deliberately NOT `both`.
        //
        // With `both`, the element holds the animation's `from` state
        // (opacity: 0) until something proves the animation ran. A decorative
        // entrance is not worth being able to hide content: when the
        // compositor never ticks the animation - a backgrounded tab, a
        // non-composited surface, a throttled window - a page renders as
        // completely blank with no error anywhere. That is not a cosmetic bug;
        // it is a dashboard that looks like it has no data.
        //
        // Without a fill mode, the element's own value (opacity 1) applies
        // outside the animation, so a stalled animation costs the entrance
        // effect and nothing else.
        'fade-in': 'fade-in 180ms ease-out',
        'slide-up': 'slide-up 200ms ease-out',
        'rise-in': 'rise-in 240ms cubic-bezier(0.16, 1, 0.3, 1)',
        'pulse-ring': 'pulse-ring 1.8s ease-out infinite',
      },
    },
  },
  plugins: [],
}

export default config
