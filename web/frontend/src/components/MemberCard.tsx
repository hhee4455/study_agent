/**
 * MemberCard — 멤버 목록에서 한 멤버를 카드 형태로 표시.
 * EfficiencyBadge 로 turn/resume/retry 효율 지표를 노출한다.
 */

import { Link } from 'react-router-dom';
import EfficiencyBadge from './EfficiencyBadge';

export interface MemberCardData {
  agent_id: string;
  status: string;
  goal_id?: string;
  cost_usd?: number;
  model?: string;
  turn?: number;
  resume_count?: number;
  hire_retry_count?: number;
}

const STATUS_COLOR: Record<string, string> = {
  RUNNING: '#22c55e',
  WAITING: '#f59e0b',
  DONE: '#6b7280',
  FAILED: '#ef4444',
  HIRED: '#3b82f6',
};

export default function MemberCard({
  agent_id,
  status,
  goal_id,
  cost_usd = 0,
  model,
  turn,
  resume_count,
  hire_retry_count,
}: MemberCardData) {
  const dotColor = STATUS_COLOR[status] ?? '#9ca3af';

  return (
    <div
      style={{
        border: '1px solid #e5e7eb',
        borderRadius: '8px',
        padding: '12px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: '6px',
        background: '#fff',
        boxShadow: '0 1px 3px rgba(0,0,0,0.07)',
      }}
    >
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span
          style={{
            width: 10,
            height: 10,
            borderRadius: '50%',
            background: dotColor,
            flexShrink: 0,
          }}
          title={status}
        />
        <Link
          to={`/members/${agent_id}`}
          style={{ fontWeight: 700, fontSize: '0.95rem', textDecoration: 'none', color: '#111827' }}
        >
          {agent_id}
        </Link>
        <span
          style={{
            fontSize: '0.72rem',
            color: '#6b7280',
            border: '1px solid #e5e7eb',
            borderRadius: '3px',
            padding: '0 4px',
          }}
        >
          {status}
        </span>
        {model && (
          <span
            style={{
              fontSize: '0.72rem',
              color: '#7c3aed',
              border: '1px solid #ddd6fe',
              borderRadius: '3px',
              padding: '0 4px',
            }}
          >
            {model}
          </span>
        )}
      </div>

      {/* Goal + cost */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '0.8rem', color: '#374151' }}>
        {goal_id && <span>Goal: <code>{goal_id}</code></span>}
        <span>${cost_usd.toFixed(4)}</span>
      </div>

      {/* Efficiency badge */}
      <div>
        <EfficiencyBadge
          turn={turn}
          resume_count={resume_count}
          hire_retry_count={hire_retry_count}
          threshold={1}
        />
      </div>
    </div>
  );
}
