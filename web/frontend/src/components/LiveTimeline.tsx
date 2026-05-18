import { useState, useEffect, useMemo } from 'react';

interface TimelineEvent {
  timestamp?: string;
  actor?: string;
  kind?: string;
  summary?: string;
  [k: string]: unknown;
}

function formatTs(iso?: string): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return iso.slice(0, 19);
  }
}

const styles = {
  panel: {
    position: 'fixed' as const,
    right: 0,
    top: 0,
    height: '100vh',
    width: '340px',
    background: '#1a1a2e',
    color: '#e0e0e0',
    display: 'flex',
    flexDirection: 'column' as const,
    fontFamily: 'monospace',
    fontSize: '12px',
    boxShadow: '-2px 0 8px rgba(0,0,0,0.4)',
    zIndex: 1000,
  },
  header: {
    padding: '12px 12px 8px',
    borderBottom: '1px solid #2a2a4a',
    flexShrink: 0,
  },
  title: {
    margin: '0 0 8px',
    fontSize: '13px',
    fontWeight: 700,
    color: '#a0c4ff',
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
  },
  dot: (error: boolean) => ({
    width: '8px',
    height: '8px',
    borderRadius: '50%',
    background: error ? '#ff6b6b' : '#51cf66',
    flexShrink: 0,
  }),
  errorLabel: {
    fontSize: '10px',
    color: '#ff6b6b',
    marginLeft: '4px',
  },
  filters: {
    display: 'flex',
    gap: '6px',
  },
  select: {
    flex: 1,
    background: '#0f0f23',
    color: '#c0c0d0',
    border: '1px solid #3a3a5a',
    borderRadius: '4px',
    padding: '3px 6px',
    fontSize: '11px',
    cursor: 'pointer',
  },
  list: {
    flex: 1,
    overflowY: 'auto' as const,
    padding: '8px',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '6px',
  },
  card: {
    background: '#16213e',
    border: '1px solid #2a2a4a',
    borderRadius: '6px',
    padding: '8px 10px',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '3px',
  },
  cardRow: {
    display: 'flex',
    gap: '6px',
    alignItems: 'baseline',
  },
  ts: {
    color: '#7a7a9a',
    fontSize: '10px',
    flexShrink: 0,
  },
  actor: {
    color: '#a0c4ff',
    fontWeight: 600,
    flexShrink: 0,
  },
  kind: {
    color: '#ffd43b',
    flexShrink: 0,
  },
  summary: {
    color: '#c0c0d0',
    wordBreak: 'break-word' as const,
    lineHeight: '1.4',
  },
  empty: {
    color: '#4a4a6a',
    textAlign: 'center' as const,
    padding: '24px 0',
  },
};

export default function LiveTimeline() {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [error, setError] = useState(false);
  const [actorFilter, setActorFilter] = useState('');
  const [kindFilter, setKindFilter] = useState('');

  useEffect(() => {
    const es = new EventSource('/api/events/stream');

    es.onmessage = (e) => {
      try {
        const parsed: TimelineEvent = JSON.parse(e.data);
        // cap at 200 to prevent unbounded memory growth
        setEvents((prev) => [parsed, ...prev].slice(0, 200));
        setError(false);
      } catch {
        // ignore malformed frames
      }
    };

    es.onerror = () => setError(true);

    return () => es.close();
  }, []);

  const actors = useMemo(
    () => Array.from(new Set(events.map((e) => e.actor).filter(Boolean) as string[])).sort(),
    [events],
  );

  const kinds = useMemo(
    () => Array.from(new Set(events.map((e) => e.kind).filter(Boolean) as string[])).sort(),
    [events],
  );

  const filtered = useMemo(
    () =>
      events.filter(
        (e) =>
          (!actorFilter || e.actor === actorFilter) &&
          (!kindFilter || e.kind === kindFilter),
      ),
    [events, actorFilter, kindFilter],
  );

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <h2 style={styles.title}>
          <span style={styles.dot(error)} />
          Live Timeline
          {error && <span style={styles.errorLabel}>reconnecting…</span>}
        </h2>
        <div style={styles.filters}>
          <select
            style={styles.select}
            value={actorFilter}
            onChange={(e) => setActorFilter(e.target.value)}
          >
            <option value="">All actors</option>
            {actors.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
          <select
            style={styles.select}
            value={kindFilter}
            onChange={(e) => setKindFilter(e.target.value)}
          >
            <option value="">All kinds</option>
            {kinds.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
        </div>
      </div>

      <div style={styles.list}>
        {filtered.length === 0 && (
          <div style={styles.empty}>no events</div>
        )}
        {filtered.map((ev, i) => {
          const text =
            ev.summary ?? (ev.message as string | undefined) ?? (ev.text as string | undefined) ?? '';
          return (
            <div key={i} style={styles.card}>
              <div style={styles.cardRow}>
                <span style={styles.ts}>{formatTs(ev.timestamp)}</span>
                {ev.actor && <span style={styles.actor}>{ev.actor}</span>}
                {ev.kind && <span style={styles.kind}>[{ev.kind}]</span>}
              </div>
              {text && <div style={styles.summary}>{text}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
