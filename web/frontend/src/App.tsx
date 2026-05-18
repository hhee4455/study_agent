import './styles/tokens.css'
import { Routes, Route, Link, useLocation } from 'react-router-dom'

import Kanban from './pages/Kanban'
import Members from './pages/Members'
import MemberDetail from './pages/MemberDetail'
import Cost from './pages/Cost'
import Debates from './pages/Debates'
import DebateDetail from './pages/DebateDetail'
import LLMCalls from './pages/LLMCalls'
import LLMCallDetail from './pages/LLMCallDetail'
import Conflicts from './pages/Conflicts'
import LiveTimeline from './components/LiveTimeline'

const NAV = [
  { to: '/', label: 'Plan', match: (p: string) => p === '/' },
  { to: '/members', label: 'Members', match: (p: string) => p.startsWith('/members') },
  { to: '/cost', label: 'Cost', match: (p: string) => p.startsWith('/cost') },
  { to: '/debates', label: 'Debates', match: (p: string) => p.startsWith('/debates') },
  { to: '/llm-calls', label: 'LLM Calls', match: (p: string) => p.startsWith('/llm-calls') },
  { to: '/conflicts', label: 'Conflicts', match: (p: string) => p.startsWith('/conflicts') },
]

function Nav() {
  const { pathname } = useLocation()
  return (
    <nav style={navStyle}>
      <div style={brandStyle}>agent-system dashboard</div>
      <ul style={navListStyle}>
        {NAV.map((item) => {
          const active = item.match(pathname)
          return (
            <li key={item.to}>
              <Link
                to={item.to}
                style={{
                  ...linkStyle,
                  background: active ? 'var(--color-accent)' : 'transparent',
                  color: active ? 'var(--color-text-on-accent)' : 'var(--color-text-muted)',
                }}
              >
                {item.label}
              </Link>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

function NotFound() {
  return <div style={{ padding: 24, color: 'var(--color-text-muted)' }}>404 — 페이지 없음</div>
}

export default function App() {
  return (
    <div style={appStyle}>
      <Nav />
      <div style={mainStyle}>
        <div style={contentStyle}>
          <Routes>
            <Route path="/" element={<Kanban />} />
            <Route path="/members" element={<Members />} />
            <Route path="/members/:id" element={<MemberDetail />} />
            <Route path="/cost" element={<Cost />} />
            <Route path="/debates" element={<Debates />} />
            <Route path="/debates/:id" element={<DebateDetail />} />
            <Route path="/llm-calls" element={<LLMCalls />} />
            <Route path="/llm-calls/:filename" element={<LLMCallDetail />} />
            <Route path="/conflicts" element={<Conflicts />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </div>
        <aside style={asideStyle}>
          <LiveTimeline />
        </aside>
      </div>
    </div>
  )
}

const appStyle: React.CSSProperties = {
  minHeight: '100vh',
  background: 'var(--color-bg)',
  color: 'var(--color-text)',
  fontFamily:
    '-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,Roboto,Helvetica,Arial,sans-serif',
}

const navStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 24,
  padding: '12px 24px',
  borderBottom: '1px solid var(--color-border)',
  background: 'var(--color-surface)',
  position: 'sticky',
  top: 0,
  zIndex: 10,
}

const brandStyle: React.CSSProperties = {
  fontWeight: 600,
  fontSize: 14,
  letterSpacing: 0.3,
  color: 'var(--color-text)',
}

const navListStyle: React.CSSProperties = {
  display: 'flex',
  gap: 8,
  listStyle: 'none',
  margin: 0,
  padding: 0,
}

const linkStyle: React.CSSProperties = {
  display: 'inline-block',
  padding: '6px 12px',
  borderRadius: 6,
  fontSize: 13,
  textDecoration: 'none',
  transition: 'background 120ms,color 120ms',
}

const mainStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'minmax(0,1fr) 320px',
  gap: 0,
  minHeight: 'calc(100vh - 49px)',
}

const contentStyle: React.CSSProperties = {
  padding: 24,
  minWidth: 0,
}

const asideStyle: React.CSSProperties = {
  borderLeft: '1px solid var(--color-border)',
  background: 'var(--color-surface)',
  minWidth: 0,
}
