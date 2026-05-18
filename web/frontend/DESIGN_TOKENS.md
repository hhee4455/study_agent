# Design Tokens — Agent System Dashboard

Dark-first token set. All values defined in `src/index.css` `:root`. Reference via `var(--token-name)` in CSS or Tailwind utility classes.

---

## Color — Surface

| Token | Value | Tailwind class | Usage |
|---|---|---|---|
| `--color-bg-base` | `#0a0a0a` | `bg-bg-base` | Page background |
| `--color-bg-elevated` | `#111111` | `bg-bg-elevated` | Cards, panels, sidebars |
| `--color-bg-overlay` | `#1a1a1a` | `bg-bg-overlay` | Modals, dropdowns backdrop |
| `--color-bg-hover` | `#1f1f1f` | `bg-bg-hover` | Row/item hover state |
| `--color-bg-active` | `#262626` | `bg-bg-active` | Pressed / selected item |

## Color — Foreground

| Token | Value | Tailwind class | Usage |
|---|---|---|---|
| `--color-fg-default` | `#ededed` | `text-fg-default` | Body text, headings |
| `--color-fg-muted` | `#a1a1a1` | `text-fg-muted` | Secondary labels, descriptions |
| `--color-fg-subtle` | `#525252` | `text-fg-subtle` | Disabled text, placeholders |
| `--color-fg-on-accent` | `#ffffff` | `text-fg-on-accent` | Text on accent-colored backgrounds |

## Color — Border

| Token | Value | Tailwind class | Usage |
|---|---|---|---|
| `--color-border-default` | `#262626` | `border-border-default` | Card borders, dividers |
| `--color-border-subtle` | `#1a1a1a` | `border-border-subtle` | Very faint separators |
| `--color-border-strong` | `#404040` | `border-border-strong` | Focus rings, highlighted borders |

## Color — Accent

| Token | Value | Tailwind class | Usage |
|---|---|---|---|
| `--color-accent-default` | `#7c3aed` | `bg-accent-default` | Primary buttons, active nav |
| `--color-accent-hover` | `#6d28d9` | `bg-accent-hover` | Button hover state |
| `--color-accent-muted` | `#1e1033` | `bg-accent-muted` | Accent badge/chip background |
| `--color-accent-fg` | `#a78bfa` | `text-accent-fg` | Accent-tinted text, links |

## Color — Status

| Token | Value | Tailwind class | Usage |
|---|---|---|---|
| `--color-success` | `#22c55e` | `text-success` | Success messages, positive delta |
| `--color-success-muted` | `#052e16` | `bg-success-muted` | Success badge background |
| `--color-warning` | `#f59e0b` | `text-warning` | Warnings, caution states |
| `--color-warning-muted` | `#1c1300` | `bg-warning-muted` | Warning badge background |
| `--color-danger` | `#ef4444` | `text-danger` | Errors, destructive actions |
| `--color-danger-muted` | `#1c0000` | `bg-danger-muted` | Error badge background |
| `--color-info` | `#3b82f6` | `text-info` | Info notices, links |
| `--color-info-muted` | `#0b1a2e` | `bg-info-muted` | Info badge background |

## Color — Agent State

Maps to member state machine (HIRED → RUNNING → WAITING → DONE / FAILED).

| Token | Value | Tailwind class | State |
|---|---|---|---|
| `--color-state-hired` | `#6b7280` | `text-state-hired` | HIRED — onboarding |
| `--color-state-running` | `#3b82f6` | `text-state-running` | RUNNING — active |
| `--color-state-waiting` | `#f59e0b` | `text-state-waiting` | WAITING — blocked |
| `--color-state-done` | `#22c55e` | `text-state-done` | DONE — success |
| `--color-state-failed` | `#ef4444` | `text-state-failed` | FAILED — error |

---

## Typography

| Token | Value | Usage |
|---|---|---|
| `--font-sans` | Inter, Geist, system-ui | Body, UI labels |
| `--font-mono` | Geist Mono, JetBrains Mono | Code, IDs, timestamps |
| `--font-size-xs` | 0.75rem (12px) | Badges, captions |
| `--font-size-sm` | 0.875rem (14px) | Secondary text, table cells |
| `--font-size-base` | 1rem (16px) | Body default |
| `--font-size-lg` | 1.125rem (18px) | Card titles |
| `--font-size-xl` | 1.25rem (20px) | Section headings |
| `--font-size-2xl` | 1.5rem (24px) | Page subtitles |
| `--font-size-3xl` | 1.875rem (30px) | Page titles |
| `--line-height-tight` | 1.25 | Headings |
| `--line-height-normal` | 1.5 | Body text |
| `--line-height-relaxed` | 1.75 | Long-form content |
| `--font-weight-normal` | 400 | Body |
| `--font-weight-medium` | 500 | Labels, buttons |
| `--font-weight-semibold` | 600 | Sub-headings |
| `--font-weight-bold` | 700 | Headings |

---

## Spacing (4px base scale)

| Token | Value | Tailwind class |
|---|---|---|
| `--space-0` | 0px | `p-0` / `m-0` |
| `--space-0.5` | 2px | `p-0.5` |
| `--space-1` | 4px | `p-1` |
| `--space-2` | 8px | `p-2` |
| `--space-3` | 12px | `p-3` |
| `--space-4` | 16px | `p-4` |
| `--space-5` | 20px | `p-5` |
| `--space-6` | 24px | `p-6` |
| `--space-8` | 32px | `p-8` |
| `--space-10` | 40px | `p-10` |
| `--space-12` | 48px | `p-12` |
| `--space-16` | 64px | `p-16` |
| `--space-20` | 80px | `p-20` |
| `--space-24` | 96px | `p-24` |

---

## Border Radius

| Token | Value | Tailwind class | Usage |
|---|---|---|---|
| `--radius-none` | 0px | `rounded-none` | Sharp corners |
| `--radius-sm` | 4px | `rounded-sm` | Badges, chips |
| `--radius-md` | 6px | `rounded-md` | Buttons, inputs |
| `--radius-lg` | 8px | `rounded-lg` | Cards |
| `--radius-xl` | 12px | `rounded-xl` | Modals, panels |
| `--radius-full` | 9999px | `rounded-full` | Avatars, pills |

---

## Shadow

| Token | Usage |
|---|---|
| `--shadow-sm` | Subtle card lift |
| `--shadow-md` | Dropdowns |
| `--shadow-lg` | Modals |
| `--shadow-xl` | Floating panels |

All shadows use black-based rgba + a faint white ring to appear on dark surfaces.

---

## Z-index

| Token | Value | Usage |
|---|---|---|
| `--z-base` | 0 | Default stacking |
| `--z-dropdown` | 100 | Menus, tooltips |
| `--z-sticky` | 200 | Sticky headers |
| `--z-overlay` | 300 | Backdrop overlays |
| `--z-modal` | 400 | Modal dialogs |
| `--z-toast` | 500 | Toast notifications |

---

## Motion

| Token | Value | Usage |
|---|---|---|
| `--ease-out` | `cubic-bezier(0,0,0.2,1)` | Enter transitions |
| `--ease-in-out` | `cubic-bezier(0.4,0,0.2,1)` | Toggle / shared-axis |
| `--duration-fast` | 100ms | Micro-interactions (hover) |
| `--duration-normal` | 200ms | Most UI transitions |
| `--duration-slow` | 350ms | Page/panel enter |
