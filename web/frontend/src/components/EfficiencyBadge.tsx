/**
 * EfficiencyBadge — 멤버별 효율 지표 배지.
 *
 * 각 값이 threshold 를 초과하면 1~3 step 경고 색상으로 표시한다.
 *   step 1 (yellow): value > threshold      (기본 >1)
 *   step 2 (orange): value > threshold + 1  (기본 >2)
 *   step 3 (red):    value > threshold + 2  (기본 >3)
 *
 * 라벨 형식: T{turn} R{resume} H{retry}
 */

import React from 'react';

export interface EfficiencyBadgeProps {
  turn?: number;
  resume_count?: number;
  hire_retry_count?: number;
  /** 경고 임계값 — 이 값 초과 시 경고 발생 (기본 1) */
  threshold?: number;
}

type WarnLevel = 0 | 1 | 2 | 3;

function warnLevel(value: number, threshold: number): WarnLevel {
  if (value > threshold + 2) return 3;
  if (value > threshold + 1) return 2;
  if (value > threshold) return 1;
  return 0;
}

const WARN_COLORS: Record<WarnLevel, string> = {
  0: '#6b7280',  // neutral gray
  1: '#ca8a04',  // yellow — warning step 1
  2: '#ea580c',  // orange — warning step 2
  3: '#dc2626',  // red    — warning step 3
};

const WARN_BG: Record<WarnLevel, string> = {
  0: 'transparent',
  1: '#fefce8',
  2: '#fff7ed',
  3: '#fef2f2',
};

function MetricChip({
  label,
  value,
  threshold,
}: {
  label: string;
  value: number;
  threshold: number;
}) {
  const level = warnLevel(value, threshold);
  const color = WARN_COLORS[level];
  const bg = WARN_BG[level];

  return (
    <span
      title={level >= 1 ? `warn level ${level}` : undefined}
      style={{
        display: 'inline-block',
        fontFamily: 'monospace',
        fontSize: '0.72rem',
        fontWeight: 600,
        color,
        background: bg,
        border: `1px solid ${level >= 1 ? color : '#d1d5db'}`,
        borderRadius: '3px',
        padding: '0 4px',
        marginRight: '2px',
        lineHeight: '1.5',
      }}
    >
      {label}
      {value}
    </span>
  );
}

export default function EfficiencyBadge({
  turn = 0,
  resume_count = 0,
  hire_retry_count = 0,
  threshold = 1,
}: EfficiencyBadgeProps) {
  const maxLevel = Math.max(
    warnLevel(turn, threshold),
    warnLevel(resume_count, threshold),
    warnLevel(hire_retry_count, threshold),
  ) as WarnLevel;

  const containerStyle: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '2px',
    padding: '1px 4px',
    borderRadius: '4px',
    border: maxLevel >= 1 ? `1px solid ${WARN_COLORS[maxLevel]}` : '1px solid #e5e7eb',
    background: maxLevel >= 3 ? '#fef2f2' : maxLevel >= 2 ? '#fff7ed' : 'transparent',
  };

  return (
    <span style={containerStyle} aria-label="efficiency metrics">
      <MetricChip label="T" value={turn} threshold={threshold} />
      <MetricChip label="R" value={resume_count} threshold={threshold} />
      <MetricChip label="H" value={hire_retry_count} threshold={threshold} />
    </span>
  );
}
