import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

interface LLMCallSummary {
  filename: string;
  member: string;
  kind: string;
  model: string;
  cost_usd: number;
  latency_ms: number;
  timestamp: string;
}

interface ApiResponse {
  calls: LLMCallSummary[];
}

function ModelBadge({ model }: { model: string }) {
  const colors: Record<string, string> = {
    sonnet: '#40a02b',
    opus: '#fe640b',
  };
  const bg = colors[model] ?? '#4b5563';
  return (
    <span style={{
      display: 'inline-block',
      padding: '0.1rem 0.5rem',
      borderRadius: '9999px',
      fontSize: '0.7rem',
      fontWeight: 600,
      background: bg,
      color: '#fff',
    }}>
      {model || '—'}
    </span>
  );
}

export default function LLMCalls() {
  const navigate = useNavigate();
  const [calls, setCalls] = useState<LLMCallSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [member, setMember] = useState('');
  const [kind, setKind] = useState('');

  useEffect(() => {
    const params = new URLSearchParams();
    if (member) params.set('member', member);
    if (kind) params.set('kind', kind);
    const qs = params.toString();

    setLoading(true);
    setError(null);
    fetch(`/api/llm-calls${qs ? `?${qs}` : ''}`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<ApiResponse>;
      })
      .then(data => setCalls(data.calls))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [member, kind]);

  const pageStyle: React.CSSProperties = {
    padding: '1.5rem',
    maxWidth: '1100px',
    margin: '0 auto',
  };

  const filterRowStyle: React.CSSProperties = {
    display: 'flex',
    gap: '1rem',
    alignItems: 'center',
    marginBottom: '1rem',
  };

  const selectStyle: React.CSSProperties = {
    background: '#1e1e2e',
    color: '#cdd6f4',
    border: '1px solid #313244',
    borderRadius: '6px',
    padding: '0.3rem 0.6rem',
    fontSize: '0.85rem',
  };

  const tableStyle: React.CSSProperties = {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: '0.85rem',
  };

  const thStyle: React.CSSProperties = {
    textAlign: 'left',
    padding: '0.5rem 0.75rem',
    borderBottom: '1px solid #313244',
    color: '#a6adc8',
    fontWeight: 600,
  };

  const tdStyle: React.CSSProperties = {
    padding: '0.5rem 0.75rem',
    borderBottom: '1px solid #1e1e2e',
  };

  const rowHoverStyle = (idx: number): React.CSSProperties => ({
    cursor: 'pointer',
    background: idx % 2 === 0 ? 'transparent' : '#181825',
  });

  return (
    <div style={pageStyle}>
      <h1 style={{ marginBottom: '1rem' }}>LLM Calls</h1>

      <div style={filterRowStyle}>
        <label style={{ color: '#a6adc8', fontSize: '0.85rem' }}>Member:</label>
        <select style={selectStyle} value={member} onChange={e => setMember(e.target.value)}>
          <option value="">All</option>
          {Array.from(new Set(calls.map(c => c.member))).sort().map(m => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>

        <label style={{ color: '#a6adc8', fontSize: '0.85rem' }}>Kind:</label>
        <select style={selectStyle} value={kind} onChange={e => setKind(e.target.value)}>
          <option value="">All</option>
          {Array.from(new Set(calls.map(c => c.kind))).sort().map(k => (
            <option key={k} value={k}>{k}</option>
          ))}
        </select>
      </div>

      {loading && <div style={{ color: '#a6adc8' }}>Loading…</div>}
      {!loading && error && (
        <div style={{ color: 'var(--state-failed)' }}>Error: {error}</div>
      )}
      {!loading && !error && calls.length === 0 && (
        <div style={{ color: '#6b7280', fontStyle: 'italic' }}>No LLM calls found.</div>
      )}
      {!loading && !error && calls.length > 0 && (
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={thStyle}>Timestamp</th>
              <th style={thStyle}>Member</th>
              <th style={thStyle}>Kind</th>
              <th style={thStyle}>Model</th>
              <th style={thStyle}>Cost (USD)</th>
              <th style={thStyle}>Latency (ms)</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((call, idx) => (
              <tr
                key={call.filename}
                style={rowHoverStyle(idx)}
                onClick={() => navigate(`/llm-calls/${encodeURIComponent(call.filename)}`)}
                onMouseEnter={e => (e.currentTarget.style.background = '#313244')}
                onMouseLeave={e => (e.currentTarget.style.background = idx % 2 === 0 ? 'transparent' : '#181825')}
              >
                <td style={tdStyle}>{call.timestamp ?? '—'}</td>
                <td style={tdStyle}>{call.member}</td>
                <td style={tdStyle}>{call.kind}</td>
                <td style={tdStyle}><ModelBadge model={call.model} /></td>
                <td style={tdStyle}>${(call.cost_usd ?? 0).toFixed(4)}</td>
                <td style={tdStyle}>{call.latency_ms != null ? `${call.latency_ms} ms` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
