import type { ComponentType } from 'react';
import {
  UserPlus,
  Play,
  Activity,
  GitMerge,
  AlertTriangle,
  MessageSquare,
  DollarSign,
  Zap,
  CheckCircle2,
  XCircle,
  Mail,
  Heart,
  FileText,
  Package,
  Trash2,
  Users,
} from 'lucide-react';

// 16 canonical SSE event kinds (verifier-required) + backend-emitted aliases
export type EventKind =
  | 'hire'
  | 'spawn'
  | 'status_change'
  | 'merge'
  | 'conflict'
  | 'debate'
  | 'budget'
  | 'llm_call'
  | 'verify'
  | 'done'
  | 'fail'
  | 'message'
  | 'heartbeat'
  | 'plan'
  | 'deliverable'
  | 'cleanup'
  // backend-emitted aliases (lead/timeline.py)
  | 'reply'
  | 'verify_pass'
  | 'verify_fail'
  | 'fire'
  | 'plan_update'
  | 'debate_decided'
  | 'code_janitor'
  | 'conflict_debated'
  | 'error';

export type Severity = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

export interface EventPaletteEntry {
  color: string;
  bgColor: string;
  icon: ComponentType<{ size?: number; className?: string; style?: React.CSSProperties }>;
  label: string;
  severity: Severity;
}

// Member status machine color convention:
//   HIRED    → info      (var(--color-info))
//   RUNNING  → success   (var(--color-success))
//   WAITING  → warning   (var(--color-warning))
//   DONE     → success   (var(--color-success-dim))
//   FAILED   → danger    (var(--color-danger))
export const eventPalette: Record<EventKind, EventPaletteEntry> = {
  // ── 16 canonical kinds ──────────────────────────────────────────────────────
  hire:             { color: 'var(--color-info)',        bgColor: 'var(--color-info-bg)',     icon: UserPlus,      label: 'Hire',            severity: 'info'    },
  spawn:            { color: 'var(--color-info)',        bgColor: 'var(--color-info-bg)',     icon: Play,          label: 'Spawn',           severity: 'info'    },
  status_change:    { color: 'var(--color-fg-muted)',    bgColor: 'var(--color-surface-2)',   icon: Activity,      label: 'Status Change',   severity: 'neutral' },
  merge:            { color: 'var(--color-success)',     bgColor: 'var(--color-success-bg)',  icon: GitMerge,      label: 'Merge',           severity: 'success' },
  conflict:         { color: 'var(--color-warning)',     bgColor: 'var(--color-warning-bg)',  icon: AlertTriangle, label: 'Conflict',        severity: 'warning' },
  debate:           { color: 'var(--color-info)',        bgColor: 'var(--color-info-bg)',     icon: MessageSquare, label: 'Debate',          severity: 'info'    },
  budget:           { color: 'var(--color-warning)',     bgColor: 'var(--color-warning-bg)',  icon: DollarSign,    label: 'Budget',          severity: 'warning' },
  llm_call:         { color: 'var(--color-info)',        bgColor: 'var(--color-info-bg)',     icon: Zap,           label: 'LLM Call',        severity: 'info'    },
  verify:           { color: 'var(--color-success)',     bgColor: 'var(--color-success-bg)',  icon: CheckCircle2,  label: 'Verify',          severity: 'success' },
  done:             { color: 'var(--color-success-dim)', bgColor: 'var(--color-success-bg)',  icon: CheckCircle2,  label: 'Done',            severity: 'success' },
  fail:             { color: 'var(--color-danger)',      bgColor: 'var(--color-danger-bg)',   icon: XCircle,       label: 'Fail',            severity: 'danger'  },
  message:          { color: 'var(--color-fg-muted)',    bgColor: 'var(--color-surface-2)',   icon: Mail,          label: 'Message',         severity: 'neutral' },
  heartbeat:        { color: 'var(--color-fg-muted)',    bgColor: 'var(--color-surface-2)',   icon: Heart,         label: 'Heartbeat',       severity: 'neutral' },
  plan:             { color: 'var(--color-info)',        bgColor: 'var(--color-info-bg)',     icon: FileText,      label: 'Plan',            severity: 'info'    },
  deliverable:      { color: 'var(--color-success)',     bgColor: 'var(--color-success-bg)',  icon: Package,       label: 'Deliverable',     severity: 'success' },
  cleanup:          { color: 'var(--color-fg-muted)',    bgColor: 'var(--color-surface-2)',   icon: Trash2,        label: 'Cleanup',         severity: 'neutral' },

  // ── backend-emitted aliases (lead/timeline.py _render_event) ────────────────
  reply:            { color: 'var(--color-info)',        bgColor: 'var(--color-info-bg)',     icon: MessageSquare, label: 'Reply',           severity: 'info'    },
  verify_pass:      { color: 'var(--color-success)',     bgColor: 'var(--color-success-bg)',  icon: CheckCircle2,  label: 'Verify Pass',     severity: 'success' },
  verify_fail:      { color: 'var(--color-danger)',      bgColor: 'var(--color-danger-bg)',   icon: XCircle,       label: 'Verify Fail',     severity: 'danger'  },
  fire:             { color: 'var(--color-danger)',      bgColor: 'var(--color-danger-bg)',   icon: Users,         label: 'Fire',            severity: 'danger'  },
  plan_update:      { color: 'var(--color-info)',        bgColor: 'var(--color-info-bg)',     icon: FileText,      label: 'Plan Update',     severity: 'info'    },
  debate_decided:   { color: 'var(--color-success)',     bgColor: 'var(--color-success-bg)',  icon: MessageSquare, label: 'Debate Decided',  severity: 'success' },
  code_janitor:     { color: 'var(--color-fg-muted)',    bgColor: 'var(--color-surface-2)',   icon: Trash2,        label: 'Code Janitor',    severity: 'neutral' },
  conflict_debated: { color: 'var(--color-warning)',     bgColor: 'var(--color-warning-bg)',  icon: AlertTriangle, label: 'Conflict Debated',severity: 'warning' },
  error:            { color: 'var(--color-danger)',      bgColor: 'var(--color-danger-bg)',   icon: AlertTriangle, label: 'Error',           severity: 'danger'  },
};

export const fallbackPaletteEntry: EventPaletteEntry = {
  color:    'var(--color-fg-muted)',
  bgColor:  'var(--color-surface-2)',
  icon:     Activity,
  label:    'Event',
  severity: 'neutral',
};

export function getPaletteEntry(kind: string): EventPaletteEntry {
  return (eventPalette as Record<string, EventPaletteEntry>)[kind] ?? fallbackPaletteEntry;
}
