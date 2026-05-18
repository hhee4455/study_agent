# agent-system-dashboard

React + Vite dashboard for the agent-system multi-agent workspace.

## Setup

```bash
npm install
npm run dev
```

## Lint

```bash
npm run lint          # fails on any warning or error
```

ESLint is configured with `--max-warnings 0`. Any violation causes a non-zero exit, which blocks CI.

### Hex color rule (`local/no-hex-color`)

Inline hex color literals and Tailwind arbitrary hex values are **banned**. Use design tokens instead:

```tsx
// BAD
style={{ background: '#0d1117', color: '#e6edf3' }}
className="bg-[#0d1117]"

// GOOD
style={{ background: 'var(--color-bg)', color: 'var(--color-text)' }}
className="bg-surface"
```

**Tokens are defined in `src/styles/tokens.css`.**

To exempt a single truly unavoidable line:

```tsx
// eslint-disable-next-line local/no-hex-color -- <reason>
const CHART_PALETTE = ['#1f6feb', '#3fb950']
```

File-level disables are prohibited.

## Design Token Mapping

All hex values that existed in the codebase before tokenisation, and their replacements:

| Original hex | Token | Description |
|---|---|---|
| `#0d1117` | `--color-bg` | Page / app background |
| `#010409` | `--color-surface` | Nav bar, sidebar background |
| `#161b22` | `--color-surface-2` | Card / panel background |
| `#21262d` | `--color-border` | Dividers and borders |
| `#e6edf3` | `--color-text` | Primary body text |
| `#9aa4b2` | `--color-text-muted` | Secondary / inactive text |
| `#ffffff` | `--color-text-on-accent` | Text rendered on accent backgrounds |
| `#1f6feb` | `--color-accent` | Active nav, interactive elements |
| `#3fb950` | `--color-success` | Success badges, positive deltas |
| `#d29922` | `--color-warning` | Warning states |
| `#f85149` | `--color-danger` | Error / danger states |

The palette is a dark-first unification of the GitHub Dark, Catppuccin Mocha, and Tailwind palettes, converging on GitHub Dark as the base with Catppuccin semantic accent hues.
