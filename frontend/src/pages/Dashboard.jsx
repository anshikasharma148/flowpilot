import { api, fmtDate, fmtMs } from '../api'
import { Badge, Card, Empty, ErrorNote, usePoll } from '../components/ui'
import Icon from '../components/icons'

function Stat({ label, value, sub, icon, tone }) {
  return (
    <div className={`stat tone-${tone}`}>
      <div className="stat-icon"><Icon name={icon} size={18} /></div>
      <div className="stat-value">{value ?? '—'}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

function StatusBar({ counts }) {
  const order = ['success', 'partial', 'failed', 'running']
  const total = order.reduce((a, k) => a + (counts?.[k] || 0), 0)
  if (!total) return <Empty>No runs yet.</Empty>
  return (
    <div>
      <div className="bar">
        {order.filter((k) => counts[k]).map((k) => (
          <div key={k} className={`bar-seg seg-${k}`} style={{ width: `${(counts[k] / total) * 100}%` }} title={`${k}: ${counts[k]}`} />
        ))}
      </div>
      <div className="legend">
        {order.filter((k) => counts[k]).map((k) => (
          <span key={k}><i className={`sw seg-${k}`} />{k} {counts[k]}</span>
        ))}
      </div>
    </div>
  )
}

export default function Dashboard({ navigate }) {
  const [stats, error] = usePoll(() => api.get('/stats'), 5000)
  const [runs] = usePoll(() => api.get('/runs?limit=8'), 5000)
  const [schedules] = usePoll(() => api.get('/schedules'), 10000)
  const [alerts] = usePoll(() => api.get('/data/alerts'), 0)

  const upcoming = (schedules || []).filter((s) => s.enabled && s.next_run_at)
    .sort((a, b) => new Date(a.next_run_at) - new Date(b.next_run_at)).slice(0, 5)

  return (
    <div>
      <div className="dash-hero">
        <div>
          <div className="eyebrow">Operations overview</div>
          <h1>Automation Command Center</h1>
          <p>Everything your automation agents have been doing — runs, schedules, tickets and notifications.</p>
        </div>
        <button className="btn hero-btn" onClick={() => navigate('chat')}><Icon name="sparkle" size={16} /> Ask the assistant</button>
      </div>
      <ErrorNote error={error} />
      <div className="stats">
        <Stat icon="workflow" tone="indigo" label="Workflows" value={stats?.workflows} sub="predefined · dynamic · custom" />
        <Stat icon="runs" tone="violet" label="Total runs" value={stats?.runs} sub={stats && Object.entries(stats.runs_by_trigger).map(([k, v]) => `${k} ${v}`).join(' · ')} />
        <Stat icon="clock" tone="sky" label="Active schedules" value={stats?.schedules_active} sub="auto-triggered" />
        <Stat icon="ticket" tone="amber" label="Tickets created" value={stats?.tickets} sub="deduplicated" />
        <Stat icon="mail" tone="emerald" label="Emails" value={stats?.emails} sub="delivered via Brevo / SMTP" />
        <Stat icon="alert" tone="rose" label="Active alerts" value={stats?.alerts} sub={stats?.zones?.length ? stats.zones.join(' · ') : ''} />
      </div>
      <div className="two-col">
        <Card title="Run outcomes"><StatusBar counts={stats?.runs_by_status || {}} /></Card>
        <Card title="Upcoming scheduled runs">
          {upcoming.length === 0 ? <Empty>Nothing scheduled.</Empty> : (
            <ul className="plain">
              {upcoming.map((s) => (
                <li key={s.id}><strong>{fmtDate(s.next_run_at)}</strong> — {s.workflow_code} {s.workflow_name} <span className="muted">({s.description})</span></li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <div className="two-col">
        <Card title="Recent runs" actions={<button className="link" onClick={() => navigate('runs')}>All runs →</button>}>
          {runs?.length === 0 && <Empty>No runs yet — try the Chat.</Empty>}
          <table className="table compact">
            <tbody>
              {runs?.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => navigate('runs', r.id)}>
                  <td>#{r.id}</td><td><strong>{r.workflow_code}</strong> {r.workflow_name}</td>
                  <td><Badge value={r.trigger} /></td><td><Badge value={r.status} /></td>
                  <td className="muted small">{fmtDate(r.started_at)}</td><td className="muted small">{fmtMs(r.duration_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Current alert feed">
          <table className="table compact">
            <thead><tr><th>Device</th><th>Metric</th><th>Severity</th><th>Active (min)</th><th>Ticket</th></tr></thead>
            <tbody>
              {alerts?.map((a) => (
                <tr key={a.uid}>
                  <td className="small">{a.device}</td><td className="small">{a.metric}</td>
                  <td><Badge value={a.intensity} /></td><td>{a.active_minutes}</td><td className="small">{a.ticket_id || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  )
}
