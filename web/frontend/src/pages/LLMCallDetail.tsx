import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';

interface LLMCallDetail {
  filename: string;
  member: string;
  kind: string;
  ts: string;
  size_bytes: number;
  content: Record<string, unknown> | string;
  parse_error?: string;
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

function CollapsibleBlock({ label, text }: { label: string; text: string }) {
  const [open, setOpen] = useState(true);
  return (
    <div style={{ marginBottom: '1.25rem' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          background: 'none',
          border: 'none',
          color: '#89b4fa',
          cursor: 'pointer',
          fontSize: '0.95rem',
          fontWeight: 600,
          padding: 0,
          marginBottom: '0.4rem',
        }}
      >
        {open ? '▾' : '▸'} {label}
      </button>
      {open && (
        <pre style={{
          background: '#181825',
          border: '1px solid #313244',
          borderRadius: '6px',
          padding: '0.75rem 1rem',
          overflowX: 'auto',
          overflowY: 'auto',
          maxHeight: '400px',
          fontSize: '0.78rem',
          color: '#cdd6f4',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          margin: 0,
        }}>
          {text || '(empty)'}
        </pre>
      )}
    </div>
  );
}

function getStr(content: Record<string, unknown>, key: string): string {
  const val = content[key];
  if (val == null) return '';
  if (typeof val === 'string') return val;
  return JSON.stringify(val, null, 2);
}

export default function LLMCallDetail() {
  const { filename } = useParams<{ filename: string }>();
  const [detail, setDetail] = useState<LLMCallDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!filename) return;
    setLoading(true);
    setError(null);
    fetch(`/api/llm-calls/${encodeURIComponent(filename)}`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<LLMCallDetail>;
      })
      .then(setDetail)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [filename]);

  const pageStyle: React.CSSProperties = {
    padding: '1.5rem',
    maxWidth: '900px',
    margin: '0 auto',
  };

  const metaGridStyle: React.CSSProperties = {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
    gap: '0.75rem',
    background: '#181825',
    border: '1px solid #313244',
    borderRadius: '8px',
    padding: '1rem',
    marginBottom: '1.5rem',
  };

  const metaItemStyle: React.CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    gap: '0.2rem',
  };

  const metaLabelStyle: React.CSSProperties = {
    fontSize: '0.7rem',
    color: '#6b7280',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  };

  const metaValueStyle: React.CSSProperties = {
    fontSize: '0.9rem',
    color: '#cdd6f4',
  };

  if (loading) return <div style={pageStyle}>Loading…</div>;
  if (error) {
    return (
      <div style={pageStyle}>
        <Link to="/llm-calls" style={{ color: '#89b4fa', textDecoration: 'none' }}>← LLM Calls</Link>
        <div style={{ color: 'var(--state-failed)', marginTop: '1rem' }}>Error: {error}</div>
      </div>
    );
  }
  if (!detail) return <div style={pageStyle}>Not found.</div>;

  const content = typeof detail.content === 'object' && detail.content !== null
    ? detail.content as Record<string, unknown>
    : null;

  const model = content ? String(content.model ?? '') : '';
  const costUsd = content ? (content.cost_usd as number | undefined) : undefined;
  const latencyMs = content ? (content.latency_ms as number | undefined) : undefined;
  const systemPrompt = content ? getStr(content, 'system_prompt') : '';
  const userPrompt = content ? getStr(content, 'user_prompt') : '';
  const rawResponse = content ? getStr(content, 'raw_response') : '';
  const rawContent = typeof detail.content === 'string' ? detail.content : JSON.stringify(detail.content, null, 2);

  return (
    <div style={pageStyle}>
      <Link to="/llm-calls" style={{ color: '#89b4fa', textDecoration: 'none', fontSize: '0.9rem' }}>
        ← LLM Calls
      </Link>

      <h1 style={{ marginTop: '0.75rem', marginBottom: '1rem', fontSize: '1.1rem', color: '#a6adc8', wordBreak: 'break-all' }}>
        {detail.filename}
      </h1>

      <div style={metaGridStyle}>
        <div style={metaItemStyle}>
          <span style={metaLabelStyle}>Timestamp</span>
          <span style={metaValueStyle}>{detail.ts || '—'}</span>
        </div>
        <div style={metaItemStyle}>
          <span style={metaLabelStyle}>Member</span>
          <span style={metaValueStyle}>{detail.member || '—'}</span>
        </div>
        <div style={metaItemStyle}>
          <span style={metaLabelStyle}>Kind</span>
          <span style={metaValueStyle}>{detail.kind || '—'}</span>
        </div>
        <div style={metaItemStyle}>
          <span style={metaLabelStyle}>Model</span>
          <span style={metaValueStyle}>{model ? <ModelBadge model={model} /> : '—'}</span>
        </div>
        <div style={metaItemStyle}>
          <span style={metaLabelStyle}>Cost (USD)</span>
          <span style={metaValueStyle}>{costUsd != null ? `$${costUsd.toFixed(4)}` : '—'}</span>
        </div>
        <div style={metaItemStyle}>
          <span style={metaLabelStyle}>Latency</span>
          <span style={metaValueStyle}>{latencyMs != null ? `${latencyMs} ms` : '—'}</span>
        </div>
        <div style={metaItemStyle}>
          <span style={metaLabelStyle}>Size</span>
          <span style={metaValueStyle}>{detail.size_bytes} B</span>
        </div>
      </div>

      {detail.parse_error && (
        <div style={{
          background: '#3b1219',
          border: '1px solid #f38ba8',
          borderRadius: '6px',
          padding: '0.75rem 1rem',
          marginBottom: '1.25rem',
          color: '#f38ba8',
          fontSize: '0.85rem',
        }}>
          Parse error: {detail.parse_error}
        </div>
      )}

      {content ? (
        <>
          {systemPrompt && <CollapsibleBlock label="System Prompt" text={systemPrompt} />}
          {userPrompt && <CollapsibleBlock label="User Prompt" text={userPrompt} />}
          {rawResponse && <CollapsibleBlock label="Raw Response" text={rawResponse} />}
          {!systemPrompt && !userPrompt && !rawResponse && (
            <CollapsibleBlock label="Raw Content" text={rawContent} />
          )}
        </>
      ) : (
        <CollapsibleBlock label="Raw Content" text={rawContent} />
      )}
    </div>
  );
}
