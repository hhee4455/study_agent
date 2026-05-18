import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import 'highlight.js/styles/github-dark.css'
import './conflicts.css'

interface ConflictFile {
  filename: string
  size_bytes: number
  modified_at: string | null
}

interface ConflictsListResponse {
  files: ConflictFile[]
}

interface ConflictContentResponse {
  filename: string
  content: string
}

export default function Conflicts() {
  const [files, setFiles] = useState<ConflictFile[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [content, setContent] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [contentLoading, setContentLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/conflicts')
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json() as Promise<ConflictsListResponse>
      })
      .then(data => {
        const sorted = (data.files ?? []).sort((a, b) => {
          if (!a.modified_at) return 1
          if (!b.modified_at) return -1
          return b.modified_at.localeCompare(a.modified_at)
        })
        setFiles(sorted)
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const selectFile = (filename: string) => {
    if (selected === filename) return
    setSelected(filename)
    setContentLoading(true)
    setContent('')
    fetch(`/api/conflicts/${encodeURIComponent(filename)}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json() as Promise<ConflictContentResponse>
      })
      .then(data => setContent(data.content ?? ''))
      .catch((e: Error) => setContent(`Error loading content: ${e.message}`))
      .finally(() => setContentLoading(false))
  }

  if (loading) return <div className="conflicts-page">Loading…</div>
  if (error) {
    return (
      <div className="conflicts-page" style={{ color: '#f85149' }}>
        Error: {error}
      </div>
    )
  }

  return (
    <div className="conflicts-page">
      <h1 className="conflicts-title">Conflicts</h1>
      <div className="conflicts-layout">
        <aside className="conflicts-sidebar">
          {files.length === 0 ? (
            <p className="conflicts-empty">No conflict files found.</p>
          ) : (
            <ul className="conflicts-list">
              {files.map(f => (
                <li
                  key={f.filename}
                  className={`conflicts-list-item${selected === f.filename ? ' active' : ''}`}
                  onClick={() => selectFile(f.filename)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={e => e.key === 'Enter' && selectFile(f.filename)}
                >
                  <span className="conflicts-filename">{f.filename}</span>
                  {f.size_bytes > 0 && (
                    <span className="conflicts-meta">{f.size_bytes} B</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </aside>
        <main className="conflicts-viewer">
          {!selected ? (
            <p className="conflicts-placeholder">Select a conflict file to view its content.</p>
          ) : contentLoading ? (
            <p className="conflicts-placeholder">Loading…</p>
          ) : (
            <div className="conflicts-markdown">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
              >
                {content}
              </ReactMarkdown>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
