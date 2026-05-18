import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchMembers, MemberCard, AgentStatus } from '../api/members';
import './members.css';

function StatusDot({ status }: { status: AgentStatus }) {
  return <span className={`status-dot ${status}`} title={status} />;
}

function ModelBadge({ model }: { model: string }) {
  const cls = model === 'sonnet' || model === 'opus' ? model : 'unknown';
  return <span className={`model-badge ${cls}`}>{model || '—'}</span>;
}

function MemberCardItem({ card }: { card: MemberCard }) {
  return (
    <Link to={`/members/${card.agent_id}`} className="member-card">
      <div className="member-card-header">
        <StatusDot status={card.status} />
        <span className="member-card-id">{card.agent_id}</span>
        <ModelBadge model={card.model} />
      </div>
      <div className="member-card-goal" title={card.goal_id}>
        {card.goal_id || '—'}
      </div>
      <div className="member-card-cost">
        ${(card.cost_usd ?? 0).toFixed(2)}
      </div>
    </Link>
  );
}

export default function Members() {
  const [cards, setCards] = useState<MemberCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchMembers()
      .then(setCards)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="members-page">Loading…</div>;
  if (error) {
    return (
      <div className="members-page" style={{ color: 'var(--state-failed)' }}>
        Error: {error}
      </div>
    );
  }

  return (
    <div className="members-page">
      <h1>Members</h1>
      {cards.length === 0 ? (
        <p className="members-empty">No members yet.</p>
      ) : (
        <div className="members-grid">
          {cards.map(c => (
            <MemberCardItem key={c.agent_id} card={c} />
          ))}
        </div>
      )}
    </div>
  );
}
