import { useState } from 'react'
import { api, fmtDate, fmtMs } from '../api'
import { Badge, Card, Empty, ErrorNote, JsonView, Markdown, usePoll } from '../components/ui'

function stepDuration(s) {
  if (!s.started_at || !s.finished_at) return null
  return new Date(s.finished_at) - new Date(s.started_at)
}

function RunDetail({ id }) {
  const [run, error] = usePoll(() => api.get(`/runs/${id}`), 0, [id])
  const [tab, setTab] = useState('report')
  if (error) return <ErrorNote error={error} />
  if (!run) return <Card><div className="muted">Loading…</div></Card>
  return (
    <Card title={`Run #${run.id} · ${run.workflow_code} ${run.workflow_name}`} actions={<Badge value={run.status} />}>
      <div className="kv">
        <div><span>Trigger</span><Badge value={run.trigger} />{run.schedule_id && <span className="muted small"> schedule #{run.schedule_id}</span>}</div>
        <div><span>Started</span>{fmtDate(run.started_at)}</div>
        <div><span>Duration</span>{fmtMs(run.duration_ms)}</div>
        <div><span>Params</span><code className="small">{JSON.stringify(run.params)}</code></div>
      </div>
      {run.error && <div className="error-note">{run.error}</div>}
      <div className="tabs">
        {['report', 'steps', 'output'].map((t) => (
          <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      {tab === 'report' && <Markdown text={run.report} />}
      {tab === 'steps' && (
        <ol className="timeline">
          {run.steps.map((s) => (
            <li key={s.id} className={`tl-${s.status}`}>
              <div className="row between">
                <div><strong>{s.step_id}</strong> <code>{s.plugin}.{s.action}</code></div>
                <div className="row"><span className="muted small">{fmtMs(stepDuration(s))}</span><Badge value={s.status} /></div>
              </div>
              {s.error && <div className="error-note small">{s.error}</div>}
              <details>
                <summary className="small">inputs / output</summary>
                <div className="two-col">
                  <div><div className="muted small">inputs</div><JsonView value={s.inputs} /></div>
                  <div><div className="muted small">output</div><JsonView value={s.output} /></div>
                </div>
              </details>
            </li>
          ))}
        </ol>
      )}
      {tab === 'output' && <JsonView value={run.output} />}
    </Card>
  )
}

export default function Runs({ navigate, param }) {
  const [filter, setFilter] = useState('')
  const [runs, error] = usePoll(() => api.get(`/runs?limit=100${filter ? `&status=${filter}` : ''}`), 4000, [filter])
  const selected = param ? Number(param) : runs?.[0]?.id

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Runs</h1>
          <p className="muted">Live monitor of every execution — chat, manual and scheduled. Refreshes every few seconds.</p>
        </div>
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All statuses</option>
          {['success', 'partial', 'failed', 'running'].map((s) => <option key={s}>{s}</option>)}
        </select>
      </header>
      <ErrorNote error={error} />
      <div className="split">
        <div className="list-col">
          {runs?.length === 0 && <Empty>No runs yet.</Empty>}
          {runs?.map((r) => (
            <button key={r.id} className={`list-item ${selected === r.id ? 'active' : ''}`} onClick={() => navigate('runs', r.id)}>
              <div className="row between"><strong>#{r.id} {r.workflow_code}</strong><Badge value={r.status} /></div>
              <div className="clamp">{r.workflow_name}</div>
              <div className="muted small">{r.trigger} · {fmtDate(r.started_at)} · {fmtMs(r.duration_ms)}</div>
            </button>
          ))}
        </div>
        <div className="detail-col">{selected ? <RunDetail id={selected} /> : <Empty>Select a run.</Empty>}</div>
      </div>
    </div>
  )
}
