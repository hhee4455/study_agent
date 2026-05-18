import { useState, useEffect } from 'react';

type Goal = {
  id: string;
  title: string;
  assigned: string | null;
  done: boolean;
  model?: 'sonnet' | 'opus' | string;
};

type PlanResp = { goals: Goal[] };

function modelBadgeStyle(model?: string): React.CSSProperties {
  if (model === 'sonnet') {
    return { backgroundColor: '#16a34a', color: '#fff', padding: '2px 7px', borderRadius: 4, fontSize: 11, fontWeight: 600 };
  }
  if (model === 'opus') {
    return { backgroundColor: '#ea580c', color: '#fff', padding: '2px 7px', borderRadius: 4, fontSize: 11, fontWeight: 600 };
  }
  return { backgroundColor: '#6b7280', color: '#fff', padding: '2px 7px', borderRadius: 4, fontSize: 11, fontWeight: 600 };
}

function GoalCard({ goal }: { goal: Goal }) {
  return (
    <div style={{
      background: '#fff',
      border: '1px solid #e5e7eb',
      borderRadius: 8,
      padding: '12px 14px',
      marginBottom: 10,
      boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
        <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#9ca3af' }}>{goal.id}</span>
        {goal.model !== undefined ? (
          <span style={modelBadgeStyle(goal.model)}>{goal.model}</span>
        ) : (
          <span style={modelBadgeStyle()}>—</span>
        )}
      </div>
      <div style={{ fontWeight: 700, fontSize: 14, color: '#111827', marginBottom: goal.assigned ? 6 : 0 }}>
        {goal.title}
      </div>
      {goal.assigned && (
        <div style={{ fontSize: 12, color: '#6b7280' }}>
          {goal.assigned}
        </div>
      )}
    </div>
  );
}

function Column({ title, goals, accent }: { title: string; goals: Goal[]; accent: string }) {
  return (
    <div style={{
      flex: '1 1 0',
      minWidth: 220,
      maxWidth: 380,
      background: '#f9fafb',
      borderRadius: 10,
      padding: '14px 12px',
      border: `2px solid ${accent}`,
    }}>
      <div style={{
        fontWeight: 700,
        fontSize: 15,
        color: accent,
        marginBottom: 12,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
      }}>
        <span>{title}</span>
        <span style={{
          background: accent,
          color: '#fff',
          borderRadius: 12,
          padding: '1px 9px',
          fontSize: 12,
          fontWeight: 600,
        }}>{goals.length}</span>
      </div>
      {goals.length === 0 ? (
        <div style={{ color: '#d1d5db', fontSize: 13, textAlign: 'center', paddingTop: 24 }}>No items</div>
      ) : (
        goals.map(g => <GoalCard key={g.id} goal={g} />)
      )}
    </div>
  );
}

export default function Kanban() {
  const [goals, setGoals] = useState<Goal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchPlan() {
      try {
        const res = await fetch('/api/plan');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: PlanResp = await res.json();
        if (!cancelled) {
          setGoals(data.goals);
          setError(null);
          setLoading(false);
        }
      } catch (e) {
        if (!cancelled) {
          setError((e as Error).message);
          setLoading(false);
        }
      }
    }

    fetchPlan();
    const id = setInterval(fetchPlan, 10000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const pending = goals.filter(g => !g.done && !g.assigned);
  const assigned = goals.filter(g => !g.done && !!g.assigned);
  const done = goals.filter(g => g.done);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 200, fontSize: 16, color: '#6b7280' }}>
        Loading...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 200, fontSize: 15, color: '#dc2626' }}>
        Error: {error}
      </div>
    );
  }

  return (
    <div style={{ padding: '24px 20px', fontFamily: 'system-ui, sans-serif' }}>
      <h1 style={{ fontSize: 22, fontWeight: 800, color: '#111827', marginBottom: 24 }}>Kanban Board</h1>
      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <Column title="Pending" goals={pending} accent="#6366f1" />
        <Column title="Assigned" goals={assigned} accent="#f59e0b" />
        <Column title="Done" goals={done} accent="#22c55e" />
      </div>
    </div>
  );
}
