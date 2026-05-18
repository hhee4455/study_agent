import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import './debates.css';

type DebateStatus = 'resolved' | 'escalated' | 'open';

interface DebateSummary {
  id: string;
  title?: string;
  status?: DebateStatus;
  participants?: number;
  created_at?: string;
}

interface DebatesResponse {
  debates: DebateSummary[];
}

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === 'resolved' ? 'badge-resolved'
    : status === 'escalated' ? 'badge-escalated'
    : 'badge-open';
  return <span className={`debate-status-badge ${cls}`}>{status}</span>;
}

function DebateCard({ debate, onClick }: { debate: DebateSummary; onClick: () => void }) {
  return (
    <div
      className="debate-card"
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={e => e.key === 'Enter' && onClick()}
    >
      <div className="debate-card-header">
        <span className="debate-card-id">{debate.id}</span>
        <StatusBadge status={debate.status ?? 'open'} />
      </div>
      <div className="debate-card-title">{debate.title ?? debate.id}</div>
      <div className="debate-card-meta">
        {debate.participants != null && (
          <span>{debate.participants} participants</span>
        )}
        {debate.created_at && (
          <span>{new Date(debate.created_at).toLocaleString()}</span>
        )}
      </div>
    </div>
  );
}

export default function Debates() {
  const [debates, setDebates] = useState<DebateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const navigate = useNavigate();

  useEffect(() => {
    fetch('/api/debates')
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<DebatesResponse>;
      })
      .then(data => setDebates(data.debates ?? []))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const filtered = debates.filter(d => {
    const q = search.toLowerCase();
    const matchesSearch =
      !q ||
      d.id.toLowerCase().includes(q) ||
      (d.title ?? '').toLowerCase().includes(q);
    const matchesStatus =
      statusFilter === 'all' || (d.status ?? 'open') === statusFilter;
    return matchesSearch && matchesStatus;
  });

  if (loading) return <div className="debates-page">Loading…</div>;
  if (error) {
    return (
      <div className="debates-page" style={{ color: 'var(--state-failed)' }}>
        Error: {error}
      </div>
    );
  }

  return (
    <div className="debates-page">
      <h1>Debates</h1>
      <div className="debates-toolbar">
        <input
          className="debates-search"
          type="text"
          placeholder="Search by id or title…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <select
          className="debates-filter"
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
        >
          <option value="all">All statuses</option>
          <option value="open">Open</option>
          <option value="escalated">Escalated</option>
          <option value="resolved">Resolved</option>
        </select>
      </div>
      {filtered.length === 0 ? (
        <p className="debates-empty">No debates found.</p>
      ) : (
        <div className="debates-grid">
          {filtered.map(d => (
            <DebateCard
              key={d.id}
              debate={d}
              onClick={() => navigate(`/debates/${d.id}`)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
