import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import './debates.css';

type Persona = 'advocate' | 'skeptic' | 'pragmatist' | 'arbiter';

interface Message {
  persona: Persona;
  content: string;
  timestamp: string;
}

interface Round {
  round: number;
  escalation_reason?: string;
  messages?: Message[];
}

interface DebateDetailData {
  id: string;
  title?: string;
  status?: string;
  consensus_reached?: boolean;
  integrated_content?: string;
  rounds?: Round[];
}

const PERSONA_COLOR: Record<Persona, string> = {
  advocate:   '#2563eb',
  skeptic:    '#dc2626',
  pragmatist: '#16a34a',
  arbiter:    '#9333ea',
};

type BubbleAlign = 'left' | 'right' | 'center';
const PERSONA_ALIGN: Record<Persona, BubbleAlign> = {
  advocate:   'left',
  skeptic:    'right',
  pragmatist: 'left',
  arbiter:    'center',
};

function PersonaAvatar({ persona }: { persona: Persona }) {
  const color = PERSONA_COLOR[persona] ?? '#6b7280';
  return (
    <div style={{
      width: 32, height: 32, borderRadius: '50%',
      background: color, display: 'flex', alignItems: 'center',
      justifyContent: 'center', color: '#fff',
      fontWeight: 700, fontSize: 13, flexShrink: 0,
    }}>
      {persona[0].toUpperCase()}
    </div>
  );
}

function ChatBubble({ msg }: { msg: Message }) {
  const persona = msg.persona as Persona;
  const align = PERSONA_ALIGN[persona] ?? 'left';
  const color = PERSONA_COLOR[persona] ?? '#6b7280';
  const isRight = align === 'right';
  const isCenter = align === 'center';

  const bubbleRadius = isRight
    ? '0.75rem 0 0.75rem 0.75rem'
    : isCenter
    ? '0.5rem'
    : '0 0.75rem 0.75rem 0.75rem';

  return (
    <div style={{
      display: 'flex',
      flexDirection: isRight ? 'row-reverse' : 'row',
      justifyContent: isCenter ? 'center' : undefined,
      gap: '0.5rem',
      marginBottom: '0.75rem',
    }}>
      {!isCenter && <PersonaAvatar persona={persona} />}
      <div style={{ maxWidth: '65%' }}>
        <div style={{
          fontSize: '0.7rem', color: '#a6adc8', marginBottom: 2,
          textAlign: isRight ? 'right' : isCenter ? 'center' : 'left',
        }}>
          <span style={{ color, fontWeight: 600 }}>{persona}</span>
          {msg.timestamp && (
            <span style={{ marginLeft: 6 }}>
              {new Date(msg.timestamp).toLocaleTimeString()}
            </span>
          )}
        </div>
        <div style={{
          background: '#1e1e2e',
          border: `1px solid ${color}`,
          borderRadius: bubbleRadius,
          padding: '0.5rem 0.75rem',
          fontSize: '0.85rem',
          color: '#cdd6f4',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}>
          {msg.content}
        </div>
      </div>
    </div>
  );
}

function EscalationTimeline({ rounds }: { rounds: Round[] }) {
  return (
    <div className="debate-timeline">
      {rounds.map((r, i) => (
        <div key={r.round} className="timeline-step">
          <div className="timeline-dot" />
          {i < rounds.length - 1 && <div className="timeline-line" />}
          <div className="timeline-content">
            <span className="timeline-round-label">Round {r.round}</span>
            {r.escalation_reason && (
              <span className="timeline-escalation">↑ {r.escalation_reason}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function ConsensusPanel({ data }: { data: DebateDetailData }) {
  const reached = data.consensus_reached ?? false;
  return (
    <div className="consensus-panel">
      <div className="consensus-header">
        <h2>Consensus</h2>
        <span className={`consensus-badge ${reached ? 'consensus-yes' : 'consensus-no'}`}>
          {reached ? '✓ Reached' : '✗ Not reached'}
        </span>
      </div>
      {data.integrated_content ? (
        <pre className="detail-pre">{data.integrated_content}</pre>
      ) : (
        <p style={{ color: '#6b7280', fontStyle: 'italic' }}>No integrated content.</p>
      )}
    </div>
  );
}

export default function DebateDetail() {
  const { id } = useParams<{ id: string }>();
  const [debate, setDebate] = useState<DebateDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    fetch(`/api/debates/${id}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<DebateDetailData>;
      })
      .then(setDebate)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="debate-detail">Loading…</div>;
  if (error) {
    return (
      <div className="debate-detail" style={{ color: 'var(--state-failed)' }}>
        Error: {error}
      </div>
    );
  }
  if (!debate) return <div className="debate-detail">Debate not found.</div>;

  const rounds = debate.rounds ?? [];

  return (
    <div className="debate-detail">
      <Link to="/debates" className="back-link">← Debates</Link>

      <div className="debate-detail-header">
        <h1>{debate.title ?? debate.id}</h1>
        <span className={`debate-status-badge badge-${debate.status ?? 'open'}`}>
          {debate.status ?? 'open'}
        </span>
      </div>

      {/* Escalation timeline */}
      {rounds.length > 0 && (
        <div className="detail-section">
          <h2>Escalation timeline</h2>
          <EscalationTimeline rounds={rounds} />
        </div>
      )}

      {/* Chat buffer grouped by round */}
      <div className="detail-section">
        <h2>Debate</h2>
        {rounds.length === 0 ? (
          <p style={{ color: '#6b7280', fontStyle: 'italic' }}>No messages.</p>
        ) : (
          <div className="debate-chat-buffer">
            {rounds.map(r => (
              <div key={r.round}>
                <div className="debate-round-divider">Round {r.round}</div>
                {(r.messages ?? []).map((msg, i) => (
                  <ChatBubble key={i} msg={msg} />
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Consensus panel */}
      <div className="detail-section">
        <ConsensusPanel data={debate} />
      </div>
    </div>
  );
}
