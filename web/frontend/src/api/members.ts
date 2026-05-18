export type AgentStatus = 'HIRED' | 'RUNNING' | 'WAITING' | 'DONE' | 'FAILED';

export interface MemberCard {
  agent_id: string;
  status: AgentStatus;
  goal_id: string;
  model: string;
  cost_usd: number;
  last_resume: number;
  hired_at: string;
  completed_at: string | null;
  last_error: string;
  last_session_id: string;
  last_msg_id: number;
}

export interface MailboxMessage {
  id: number;
  from: string;
  to: string;
  kind: string;
  ts: string;
  body: string;
  ref: number | null;
}

export interface VerificationCheck {
  name: string;
  passed: boolean;
  detail?: string;
}

export interface MemberDetail extends MemberCard {
  brief: string | null;
  mailbox: MailboxMessage[];
  delivery: string | null;
  verification_checks: VerificationCheck[];
}

const BASE = '/api';

export async function fetchMembers(): Promise<MemberCard[]> {
  const res = await fetch(`${BASE}/members`);
  if (!res.ok) throw new Error(`Failed to fetch members: ${res.statusText}`);
  return res.json() as Promise<MemberCard[]>;
}

async function safeContent(url: string): Promise<string | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    const data = await res.json() as { content?: string };
    return data.content ?? null;
  } catch {
    return null;
  }
}

async function safeMessages(url: string): Promise<MailboxMessage[]> {
  try {
    const res = await fetch(url);
    if (!res.ok) return [];
    return res.json() as Promise<MailboxMessage[]>;
  } catch {
    return [];
  }
}

export async function fetchMember(id: string): Promise<MemberDetail> {
  const res = await fetch(`${BASE}/members/${id}`);
  if (!res.ok) throw new Error(`Member not found: ${id}`);
  const record = await res.json() as MemberCard;

  const [brief, mailbox, delivery] = await Promise.all([
    safeContent(`${BASE}/members/${id}/brief`),
    safeMessages(`${BASE}/members/${id}/mailbox`),
    safeContent(`${BASE}/members/${id}/delivery`),
  ]);

  return {
    ...record,
    brief,
    mailbox,
    delivery,
    verification_checks: [],
  };
}
