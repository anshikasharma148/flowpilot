import { useState } from 'react'
import { api, fmtDate } from '../api'
import { Badge, Card, Empty, ErrorNote, Markdown, usePoll } from '../components/ui'

export default function Outputs({ navigate }) {
  const [tab, setTab] = useState('tickets')
  const [tickets, e1] = usePoll(() => api.get('/tickets'), 8000)
  const [emails, e2] = usePoll(() => api.get('/emails'), 8000)
  const [open, setOpen] = useState(null)

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Tickets &amp; Emails</h1>
          <p className="muted">Side effects produced by workflows. Without Jira/SMTP credentials they are stored locally (simulated).</p>
        </div>
      </header>
      <div className="tabs">
        <button className={`tab ${tab === 'tickets' ? 'active' : ''}`} onClick={() => setTab('tickets')}>Tickets ({tickets?.length ?? 0})</button>
        <button className={`tab ${tab === 'emails' ? 'active' : ''}`} onClick={() => setTab('emails')}>Emails ({emails?.length ?? 0})</button>
      </div>
      <ErrorNote error={e1 || e2} />
      {tab === 'tickets' && (
        <Card>
          {tickets?.length === 0 && <Empty>No tickets yet.</Empty>}
          {tickets?.length > 0 && (
            <table className="table">
              <thead><tr><th>Key</th><th>Title</th><th>Priority</th><th>Status</th><th>System</th><th>Run</th><th>Created</th></tr></thead>
              <tbody>
                {tickets.map((t) => (
                  <tr key={t.id} onClick={() => setOpen(open === `t${t.id}` ? null : `t${t.id}`)} className="clickable">
                    <td><strong>{t.key}</strong></td>
                    <td>{t.title}{open === `t${t.id}` && <pre className="json">{t.description}</pre>}</td>
                    <td><Badge value={t.priority} /></td>
                    <td>{t.status}</td>
                    <td><Badge value={t.system} /></td>
                    <td>{t.run_id && <button className="link" onClick={(e) => { e.stopPropagation(); navigate('runs', t.run_id) }}>#{t.run_id}</button>}</td>
                    <td>{fmtDate(t.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
      {tab === 'emails' && (
        <Card>
          {emails?.length === 0 && <Empty>No emails yet.</Empty>}
          {emails?.map((m) => (
            <details key={m.id} className="email">
              <summary>
                <Badge value={m.status} /> <strong>{m.subject}</strong>
                <span className="muted"> → {m.to} · {fmtDate(m.created_at)}</span>
                {m.run_id && <button className="link" onClick={(e) => { e.preventDefault(); navigate('runs', m.run_id) }}>run #{m.run_id}</button>}
              </summary>
              {m.error && <div className="error-note">{m.error}</div>}
              <div className="email-body"><Markdown text={m.body} /></div>
            </details>
          ))}
        </Card>
      )}
    </div>
  )
}
