import { useState, useEffect, useMemo, useRef } from 'react';
import './overview.css';

type AgentStatus = 'HIRED' | 'RUNNING' | 'WAITING' | 'DONE' | 'FAILED';

interface MemberSummary {
  agent_id: string;
  status: AgentStatus;
  model: string;
  goal_id?: string;
  cost_usd?: number;
  eta_sec?: number;
  started_at?: string;
}

interface BudgetData {
  total_cost_usd?: number;
  limits?: {
    cost_limit_usd?: number;
    time_elapsed_sec?: number;
    time_limit_sec?: number;
  };
}

interface StreamEvent {
  ts: string;
  kind?: string;
  agent_id?: string;
}

type HeatmapGrid = number[][];

function buildHeatmap(events: StreamEvent[]): HeatmapGrid {
  const now = Date.now();
  const grid: HeatmapGrid = Array.from({ length: 7 }, () => new Array(24).fill(0) as number[]);
  for (const ev of events) {
    if (!ev.ts) continue;
    const evMs = new Date(ev.ts).getTime();
    const diffDays = Math.floor((now - evMs) / 86_400_000);
    if (diffDays < 0 || diffDays >= 7) continue;
    const hour = new Date(ev.ts).getHours();
    grid[diffDays][hour]++;
  }
  return grid;
}

function formatEta(etaSec: number): string {
  if (etaSec <= 0) return '완료 예정';
  const h = Math.floor(etaSec / 3600);
  const m = Math.floor((etaSec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

interface KpiCardProps {
  label: string;
  value: number | string;
  variant: 'running' | 'waiting' | 'done' | 'failed' | 'cost' | 'conflict' | 'eta';
}

function KpiCard({ label, value, variant }: KpiCardProps) {
  return (
    <div className={`kpi-card kpi-card--${variant}`}>
      <div className="kpi-value">{value}</div>
      <div className="kpi-label">{label}</div>
    </div>
  );
}

const DAY_LABELS = ['오늘', '-1d', '-2d', '-3d', '-4d', '-5d', '-6d'];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

export default function Overview() {
  const [members, setMembers] = useState<MemberSummary[]>([]);
  const [budget, setBudget] = useState<BudgetData | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchAll() {
      try {
        const [membersRes, budgetRes] = await Promise.all([
          fetch('/api/members'),
          fetch('/api/budget'),
        ]);
        if (cancelled) return;
        if (membersRes.ok) {
          const raw: unknown = await membersRes.json();
          const list: MemberSummary[] = Array.isArray(raw)
            ? (raw as MemberSummary[])
            : Array.isArray((raw as { members?: MemberSummary[] }).members)
              ? (raw as { members: MemberSummary[] }).members
              : [];
          setMembers(list);
        }
        if (budgetRes.ok) {
          const data: BudgetData = await budgetRes.json();
          setBudget(data);
        }
      } catch {
        // silently ignore polling errors
      }
    }

    fetchAll();
    const pollId = setInterval(fetchAll, 10_000);

    const es = new EventSource('/api/events/stream');
    esRef.current = es;
    es.onmessage = (e: MessageEvent) => {
      if (cancelled) return;
      try {
        const ev: StreamEvent = JSON.parse(e.data as string);
        setEvents(prev => {
          const cutoff = Date.now() - 7 * 86_400_000;
          const next = [...prev, ev].filter(x => new Date(x.ts).getTime() > cutoff);
          return next;
        });
      } catch {
        // ignore parse errors
      }
    };

    return () => {
      cancelled = true;
      clearInterval(pollId);
      es.close();
    };
  }, []);

  const kpi = useMemo(() => {
    const running = members.filter(m => m.status === 'RUNNING').length;
    const waiting = members.filter(m => m.status === 'WAITING').length;
    const done = members.filter(m => m.status === 'DONE').length;
    const failed = members.filter(m => m.status === 'FAILED').length;
    const totalCost = budget?.total_cost_usd ?? members.reduce((s, m) => s + (m.cost_usd ?? 0), 0);
    const conflictCount = events.filter(e => e.kind?.includes('conflict')).length;
    const runningWithEta = members.filter(m => m.status === 'RUNNING' && typeof m.eta_sec === 'number');
    const avgEtaSec = runningWithEta.length > 0
      ? runningWithEta.reduce((s, m) => s + (m.eta_sec ?? 0), 0) / runningWithEta.length
      : null;
    return { running, waiting, done, failed, totalCost, conflictCount, avgEtaSec };
  }, [members, budget, events]);

  const heatmap = useMemo(() => buildHeatmap(events), [events]);
  const maxHeat = useMemo(() => Math.max(1, ...heatmap.flat()), [heatmap]);

  const runningMembers = useMemo(
    () => members.filter(m => m.status === 'RUNNING'),
    [members]
  );

  return (
    <div className="overview-page">
      <h1 className="overview-title">Overview</h1>

      <div className="kpi-grid">
        <KpiCard label="RUNNING" value={kpi.running} variant="running" />
        <KpiCard label="WAITING" value={kpi.waiting} variant="waiting" />
        <KpiCard label="DONE" value={kpi.done} variant="done" />
        <KpiCard label="FAILED" value={kpi.failed} variant="failed" />
        <KpiCard label="총 비용" value={`$${kpi.totalCost.toFixed(3)}`} variant="cost" />
        <KpiCard label="충돌" value={kpi.conflictCount} variant="conflict" />
        <KpiCard
          label="평균 ETA"
          value={kpi.avgEtaSec !== null ? formatEta(Math.round(kpi.avgEtaSec)) : '—'}
          variant="eta"
        />
      </div>

      <section className="overview-section">
        <h2>24h 활동 히트맵</h2>
        <div className="heatmap-wrapper">
          <div className="heatmap-hours">
            <span className="heatmap-spacer" />
            {HOURS.map(h => (
              <span key={h} className="heatmap-hour-label">{h}</span>
            ))}
          </div>
          {heatmap.map((row, d) => (
            <div key={d} className="heatmap-row">
              <span className="heatmap-day-label">{DAY_LABELS[d]}</span>
              {row.map((count, h) => (
                <div
                  key={h}
                  className="heatmap-cell"
                  title={`${DAY_LABELS[d]} ${h}:00 — ${count}건`}
                  style={{ opacity: count === 0 ? 0.08 : 0.15 + (count / maxHeat) * 0.85 }}
                />
              ))}
            </div>
          ))}
        </div>
      </section>

      <section className="overview-section">
        <h2>진행 중 멤버 ETA</h2>
        {runningMembers.length === 0 ? (
          <p className="overview-empty">진행 중인 멤버 없음</p>
        ) : (
          <ul className="eta-list">
            {runningMembers.map(m => (
              <li key={m.agent_id} className="eta-item">
                <span className="eta-agent">{m.agent_id}</span>
                <span className="eta-goal" title={m.goal_id}>{m.goal_id ?? '—'}</span>
                <span className="eta-cost">${(m.cost_usd ?? 0).toFixed(3)}</span>
                <span className="eta-time">
                  {typeof m.eta_sec === 'number' ? formatEta(m.eta_sec) : '—'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
