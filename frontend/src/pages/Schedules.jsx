import { useState } from 'react'
import { api, fmtDate } from '../api'
import { Badge, Card, Empty, ErrorNote, Modal, usePoll } from '../components/ui'
import { ParamInputs } from './Workflows'

export function ScheduleForm({ workflow, initialParams = {}, onDone }) {
  const [f, setF] = useState({ frequency: 'daily', time: '09:00', day_of_week: 'mon', day_of_month: 1, interval_minutes: 60, run_at: '' })
  const [params, setParams] = useState(initialParams)
  const [err, setErr] = useState(null)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    setErr(null)
    try {
      await api.post('/schedules', {
        workflow_code: workflow.code, ...f,
        day_of_month: Number(f.day_of_month), interval_minutes: Number(f.interval_minutes),
        run_at: f.frequency === 'once' && f.run_at ? f.run_at : null, params,
      })
      onDone()
    } catch (e2) {
      setErr(e2)
    }
  }
  return (
    <form onSubmit={submit}>
      <div className="form-grid">
        <label className="field">Frequency
          <select value={f.frequency} onChange={set('frequency')}>
            {['once', 'daily', 'weekly', 'monthly', 'interval'].map((o) => <option key={o}>{o}</option>)}
          </select>
        </label>
        {['daily', 'weekly', 'monthly'].includes(f.frequency) && (
          <label className="field">Time<input type="time" value={f.time} onChange={set('time')} /></label>
        )}
        {f.frequency === 'weekly' && (
          <label className="field">Day of week
            <select value={f.day_of_week} onChange={set('day_of_week')}>
              {['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun', 'weekdays'].map((d) => <option key={d}>{d}</option>)}
            </select>
          </label>
        )}
        {f.frequency === 'monthly' && (
          <label className="field">Day of month<input type="number" min="1" max="28" value={f.day_of_month} onChange={set('day_of_month')} /></label>
        )}
        {f.frequency === 'interval' && (
          <label className="field">Every (minutes)<input type="number" min="1" value={f.interval_minutes} onChange={set('interval_minutes')} /></label>
        )}
        {f.frequency === 'once' && (
          <label className="field">Run at<input type="datetime-local" value={f.run_at} onChange={set('run_at')} required /></label>
        )}
      </div>
      <h3>Parameters</h3>
      <ParamInputs schema={workflow.params_schema} values={params} onChange={setParams} />
      <ErrorNote error={err} />
      <div className="row end"><button className="btn primary">Create schedule</button></div>
    </form>
  )
}

export default function Schedules({ navigate }) {
  const [list, error, reload] = usePoll(() => api.get('/schedules'), 10000)
  const [workflows] = usePoll(() => api.get('/workflows'), 0)
  const [creating, setCreating] = useState(null)
  const [busy, setBusy] = useState(null)

  const toggle = async (s) => { await api.patch(`/schedules/${s.id}`, { enabled: !s.enabled }); reload() }
  const remove = async (s) => {
    if (!confirm(`Delete schedule #${s.id}?`)) return
    await api.del(`/schedules/${s.id}`)
    reload()
  }
  const runNow = async (s) => {
    setBusy(s.id)
    try {
      const r = await api.post(`/schedules/${s.id}/run`)
      navigate('runs', r.id)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Schedules</h1>
          <p className="muted">Workflows that run automatically. Created from chat (“every day at 9 AM …”) or here.</p>
        </div>
        <div className="row">
          <select defaultValue="" onChange={(e) => { setCreating(workflows?.find((w) => w.code === e.target.value)); e.target.value = '' }}>
            <option value="" disabled>+ New schedule for…</option>
            {workflows?.map((w) => <option key={w.code} value={w.code}>{w.code} · {w.name}</option>)}
          </select>
        </div>
      </header>
      <ErrorNote error={error} />
      <Card>
        {list?.length === 0 && <Empty>No schedules yet. Try the first sample prompt in Chat.</Empty>}
        {list?.length > 0 && (
          <table className="table">
            <thead>
              <tr><th>#</th><th>Workflow</th><th>When</th><th>Next run</th><th>Last run</th><th>Source</th><th>Status</th><th /></tr>
            </thead>
            <tbody>
              {list.map((s) => (
                <tr key={s.id}>
                  <td>{s.id}</td>
                  <td><strong>{s.workflow_code}</strong> <span className="muted">{s.workflow_name}</span>
                    <div className="muted small mono">{JSON.stringify(s.params)}</div></td>
                  <td>{s.description}{s.cron && <div className="muted small mono">{s.cron}</div>}</td>
                  <td>{fmtDate(s.next_run_at)}</td>
                  <td>{fmtDate(s.last_run_at)}</td>
                  <td><Badge value={s.created_from} /></td>
                  <td><Badge value={s.enabled ? 'active' : 'paused'} /></td>
                  <td className="actions">
                    <button className="btn sm" onClick={() => runNow(s)} disabled={busy === s.id}>{busy === s.id ? '…' : 'Run now'}</button>
                    <button className="btn sm" onClick={() => toggle(s)}>{s.enabled ? 'Pause' : 'Resume'}</button>
                    <button className="btn sm danger" onClick={() => remove(s)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {creating && (
        <Modal title={`Schedule ${creating.code} · ${creating.name}`} onClose={() => setCreating(null)}>
          <ScheduleForm workflow={creating} onDone={() => { setCreating(null); reload() }} />
        </Modal>
      )}
    </div>
  )
}
