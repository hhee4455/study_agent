import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  fetchMember,
  MemberDetail as TMemberDetail,
  AgentStatus,
  MailboxMessage,
  VerificationCheck,
} from '../api/members';
import EfficiencyBadge from '../components/EfficiencyBadge';
import './members.css';

function StatusDot({ status }: { status: AgentStatus }) {
  return <span className={`status-dot ${status}`} title={status} />;
}

function ModelBadge({ model }: { model: string }) {
  const cls = model === 'sonnet' || model === 'opus' ? model : 'unknown';
  return <span className={`model-badge ${cls}`}>{model || '—'}</span>;
}

function MailboxThread({ messages }: { messages: MailboxMessage[] }) {
  if (messages.length === 0) {
    return <p style={{ color: '#6b7280', fontStyle: 'italic' }}>No messages yet.</p>;
  }
  return (
    <div className="mailbox-thread">
      {messages.map(m => (
        <div key={m.id} className="mailbox-message">
          <div className="mailbox-msg-meta">
            <span>
              <strong>{m.from}</strong> → <strong>{m.to}</strong>
            </span>
            <span className="mailbox-msg-kind">{m.kind}</span>
            <span>{m.ts}</span>
          </div>
          <div className="mailbox-msg-body">{m.body}</div>
        </div>
      ))}
    </div>
  );
}

function VerificationChecklist({ checks }: { checks: VerificationCheck[] }) {
  if (checks.length === 0) {
    return <p style={{ color: '#6b7280', fontStyle: 'italic' }}>No verification data.</p>;
  }
  return (
    <table className="verification-table">
      <thead>
        <tr>
          <th>Check</th>
          <th>Result</th>
          <th>Detail</th>
        </tr>
      </thead>
      <tbody>
        {checks.map(c => (
          <tr key={c.name}>
            <td>{c.name}</td>
            <td className={c.passed ? 'check-pass' : 'check-fail'}>
              {c.passed ? '✓ PASS' : '✗ FAIL'}
            </td>
            <td>{c.detail ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function MemberDetail() {
  const { id } = useParams<{ id: string }>();
  const [member, setMember] = useState<TMemberDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    fetchMember(id)
      .then(setMember)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="member-detail">Loading…</div>;
  if (error) {
    return (
      <div className="member-detail" style={{ color: 'var(--state-failed)' }}>
        Error: {error}
      </div>
    );
  }
  if (!member) return <div className="member-detail">Member not found.</div>;

  return (
    <div className="member-detail">
      <Link to="/members" className="back-link">← Members</Link>

      {/* Header */}
      <div className="member-detail-header">
        <h1>{member.agent_id}</h1>
        <div className="detail-meta">
          <StatusDot status={member.status} />
          <span>{member.status}</span>
          <ModelBadge model={member.model} />
          <span>${(member.cost_usd ?? 0).toFixed(4)}</span>
          {member.last_resume != null && <span>Resumes: {member.last_resume}</span>}
          {member.goal_id && <span>Goal: {member.goal_id}</span>}
          {member.hired_at && <span>Hired: {member.hired_at}</span>}
        </div>
        <div style={{ marginTop: '8px' }}>
          <EfficiencyBadge
            turn={(member as any).last_msg_id ?? 0}
            resume_count={member.last_resume ?? 0}
            hire_retry_count={(member as any).hire_retry_count ?? 0}
            threshold={1}
          />
        </div>
      </div>

      {/* Brief */}
      <div className="detail-section">
        <h2>Brief</h2>
        {member.brief ? (
          <pre className="detail-pre">{member.brief}</pre>
        ) : (
          <p style={{ color: '#6b7280', fontStyle: 'italic' }}>No brief available.</p>
        )}
      </div>

      {/* Mailbox thread */}
      <div className="detail-section">
        <h2>Mailbox thread</h2>
        <MailboxThread messages={member.mailbox} />
      </div>

      {/* Delivery */}
      <div className="detail-section">
        <h2>Delivery</h2>
        {member.delivery ? (
          <pre className="detail-pre">{member.delivery}</pre>
        ) : (
          <p style={{ color: '#6b7280', fontStyle: 'italic' }}>No delivery yet.</p>
        )}
      </div>

      {/* Verification checks */}
      <div className="detail-section">
        <h2>Verification checks</h2>
        <VerificationChecklist checks={member.verification_checks} />
      </div>
    </div>
  );
}
