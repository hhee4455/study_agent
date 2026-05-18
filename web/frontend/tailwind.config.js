/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx,js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg: {
          base:     'var(--color-bg-base)',
          elevated: 'var(--color-bg-elevated)',
          overlay:  'var(--color-bg-overlay)',
          hover:    'var(--color-bg-hover)',
          active:   'var(--color-bg-active)',
        },
        fg: {
          default:   'var(--color-fg-default)',
          muted:     'var(--color-fg-muted)',
          subtle:    'var(--color-fg-subtle)',
          'on-accent': 'var(--color-fg-on-accent)',
        },
        border: {
          default: 'var(--color-border-default)',
          subtle:  'var(--color-border-subtle)',
          strong:  'var(--color-border-strong)',
        },
        accent: {
          default: 'var(--color-accent-default)',
          hover:   'var(--color-accent-hover)',
          muted:   'var(--color-accent-muted)',
          fg:      'var(--color-accent-fg)',
        },
        success: {
          DEFAULT: 'var(--color-success)',
          muted:   'var(--color-success-muted)',
        },
        warning: {
          DEFAULT: 'var(--color-warning)',
          muted:   'var(--color-warning-muted)',
        },
        danger: {
          DEFAULT: 'var(--color-danger)',
          muted:   'var(--color-danger-muted)',
        },
        info: {
          DEFAULT: 'var(--color-info)',
          muted:   'var(--color-info-muted)',
        },
        state: {
          hired:   'var(--color-state-hired)',
          running: 'var(--color-state-running)',
          waiting: 'var(--color-state-waiting)',
          done:    'var(--color-state-done)',
          failed:  'var(--color-state-failed)',
        },
      },
      spacing: {
        '0.5': 'var(--space-0\\.5)',
        '1':   'var(--space-1)',
        '2':   'var(--space-2)',
        '3':   'var(--space-3)',
        '4':   'var(--space-4)',
        '5':   'var(--space-5)',
        '6':   'var(--space-6)',
        '8':   'var(--space-8)',
        '10':  'var(--space-10)',
        '12':  'var(--space-12)',
        '16':  'var(--space-16)',
        '20':  'var(--space-20)',
        '24':  'var(--space-24)',
      },
      borderRadius: {
        none: 'var(--radius-none)',
        sm:   'var(--radius-sm)',
        md:   'var(--radius-md)',
        lg:   'var(--radius-lg)',
        xl:   'var(--radius-xl)',
        full: 'var(--radius-full)',
      },
      boxShadow: {
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        xl: 'var(--shadow-xl)',
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
      fontSize: {
        xs:   'var(--font-size-xs)',
        sm:   'var(--font-size-sm)',
        base: 'var(--font-size-base)',
        lg:   'var(--font-size-lg)',
        xl:   'var(--font-size-xl)',
        '2xl': 'var(--font-size-2xl)',
        '3xl': 'var(--font-size-3xl)',
      },
    },
  },
  plugins: [],
};
