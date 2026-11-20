import { useEffect, useState } from 'react'
import { api } from '../api'
import { Badge, Card, ErrorNote, JsonView, Modal, usePoll } from '../components/ui'
import { ScheduleForm } from './Schedules'

const TEMPLATE = {
  name: 'My custom workflow',
  description: 'Fetch LINK alerts and email a report when any exist',
  params_schema: {
    email_to: { type: 'email_list', required: true, description: 'Recipients' },
  },
  definition: {
    steps: [
      { id: 'fetch', plugin: 'alerts', action: 'fetch', inputs: { metrics: ['LINK'] },
        branches: [{ when: 'steps.fetch.count == 0', goto: 'END' }] },
      { id: 'report', plugin: 'report', action: 'alert_report',
        inputs: { title: 'Link alerts', data: '$steps.fetch', summary: '{{steps.fetch.count}} link alert(s)' } },
      { id: 'notify', plugin: 'email', action: 'send',
        inputs: { to: '$params.email_to', subject: 'Link alerts', body: '$steps.report.markdown' } },
    ],
    output: { report: '$steps.report.markdown' },
  },
}

function StepFlow({ steps }) {
  return (
    <ol className="flow">
      {steps.map((s, i) => (
        <li key={s.id} className="flow-step">
          <div className="flow-num">{i + 1}</div>
          <div className="flow-body">
            <div><strong>{s.id}</strong> <code>{s.plugin}.{s.action}</code>
              {s.on_error === 'continue' && <span className="tag">continue on error</span>}</div>
            {s.when && <div className="small"><span className="tag cond">when</span> <code>{s.when}</code></div>}
            {s.branches?.map((b, j) => (
              <div key={j} className="small"><span className="tag branch">branch</span> if <code>{b.when}</code> → <strong>{b.goto}</strong></div>
            ))}
            {s.inputs && Object.keys(s.inputs).length > 0 && (
              <div className="small muted inputs">
                {Object.entries(s.inputs).map(([k, v]) => (
                  <span key={k}>{k}=<code>{typeof v === 'string' ? v : JSON.stringify(v)}</code> </span>
                ))}
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  )
}

export function ParamInputs({ schema, values, onChange }) {
  const entries = Object.entries(schema || {})
  if (!entries.length) return <p className="muted small">This workflow takes no parameters.</p>
  return (
    <div className="form-grid">
      {entries.map(([name, spec]) => {
        const v = values[name] ?? ''
        const set = (val) => onChange({ ...values, [name]: val })
        const label = (
          <span>{name}{spec.required && <span className="req">*</span>} <span className="muted small">({spec.type})</span></span>
        )
        if (spec.type === 'boolean') {
          return (
            <label key={name} className="field">
              {label}
              <select value={String(v === '' ? spec.default ?? '' : v)} onChange={(e) => set(e.target.value === 'true')}>
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            </label>
          )
        }
        if (spec.enum && spec.type !== 'list') {
          return (
            <label key={name} className="field">
              {label}
              <select value={v || spec.default || ''} onChange={(e) => set(e.target.value)}>
                {spec.enum.map((o) => <option key={o}>{o}</option>)}
              </select>
            </label>
          )
        }
        const placeholder = spec.default != null ? `default: ${Array.isArray(spec.default) ? spec.default.join(', ') : spec.default}` : spec.description
        return (
          <label key={name} className="field">
            {label}
            <input value={Array.isArray(v) ? v.join(', ') : v} placeholder={placeholder} onChange={(e) => set(e.target.value)} />
            {spec.description && <span className="hint">{spec.description}{spec.enum ? ` — ${spec.enum.join(', ')}` : ''}</span>}
          </label>
        )
      })}
    </div>
  )
}

function Editor({ initial, code, onClose, onSaved }) {
  const [text, setText] = useState(JSON.stringify(initial, null, 2))
  const [errors, setErrors] = useState([])
  const [msg, setMsg] = useState('')

  const parse = () => {
    try {
      return JSON.parse(text)
    } catch (e) {
      setErrors([`Invalid JSON: ${e.message}`])
      return null
    }
  }
  const validate = async () => {
    const body = parse()
    if (!body) return
    const r = await api.post('/workflows/validate', body)
    setErrors(r.errors)
    setMsg(r.errors.length ? '' : 'Definition is valid ✓')
  }
  const save = async () => {
    const body = parse()
    if (!body) return
    try {
      const w = code ? await api.put(`/workflows/${code}`, body) : await api.post('/workflows', body)
      onSaved(w)
    } catch (e) {
      setErrors([e.message])
    }
  }
  return (
    <Modal title={code ? `Edit ${code}` : 'New custom workflow'} onClose={onClose} wide>
      <p className="muted small">
        Steps call <code>plugin.action</code>. Reference values with <code>$params.x</code> / <code>$steps.id.field</code>, interpolate with
        <code>{' {{steps.id.field}}'}</code>, add <code>when</code> conditions and <code>branches</code> (goto a step id or END).
      </p>
      <textarea className="code-editor" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      {errors.length > 0 && <div className="error-note">{errors.map((e) => <div key={e}>{e}</div>)}</div>}
      {msg && <div className="ok-note">{msg}</div>}
      <div className="row end">
        <button className="btn" onClick={validate}>Validate</button>
        <button className="btn primary" onClick={save}>Save workflow</button>
      </div>
    </Modal>
  )
}

export default function Workflows({ navigate, param }) {
  const [list, error, reload] = usePoll(() => api.get('/workflows'), 0)
  const [selected, setSelected] = useState(param || 'WF001')
  const [values, setValues] = useState({})
  const [running, setRunning] = useState(false)
  const [runErr, setRunErr] = useState(null)
  const [editor, setEditor] = useState(null)
  const [scheduling, setScheduling] = useState(false)

  const wf = list?.find((w) => w.code === selected) || list?.[0]
  useEffect(() => {
    setValues({})
  }, [selected])

  const run = async () => {
    setRunning(true)
    setRunErr(null)
    try {
      const r = await api.post(`/workflows/${wf.code}/run`, { params: values })
      navigate('runs', r.id)
    } catch (e) {
      setRunErr(e)
    } finally {
      setRunning(false)
    }
  }
  const remove = async () => {
    if (!confirm(`Delete workflow ${wf.code}? Its schedules will be disabled.`)) return
    await api.del(`/workflows/${wf.code}`)
    setSelected('WF001')
    reload()
  }

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Workflows</h1>
          <p className="muted">Predefined, AI-generated (dynamic) and custom workflows. Each one is compiled into a LangGraph state graph at run time.</p>
        </div>
        <button className="btn primary" onClick={() => setEditor({ initial: TEMPLATE })}>+ Custom workflow</button>
      </header>
      <ErrorNote error={error} />
      <div className="split">
        <div className="list-col">
          {list?.map((w) => (
            <button key={w.code} className={`list-item ${wf?.code === w.code ? 'active' : ''}`} onClick={() => setSelected(w.code)}>
              <div className="row between"><strong>{w.code}</strong><Badge value={w.kind} /></div>
              <div>{w.name}</div>
              <div className="muted small clamp">{w.description}</div>
            </button>
          ))}
        </div>
        {wf && (
          <div className="detail-col">
            <Card
              title={`${wf.code} · ${wf.name}`}
              actions={
                <>
                  <button className="btn" onClick={() => setEditor({
                    initial: { name: `${wf.name} (copy)`, description: wf.description, params_schema: wf.params_schema, definition: wf.definition },
                  })}>Duplicate</button>
                  {wf.kind !== 'predefined' && (
                    <>
                      <button className="btn" onClick={() => setEditor({
                        code: wf.code,
                        initial: { name: wf.name, description: wf.description, params_schema: wf.params_schema, definition: wf.definition },
                      })}>Edit</button>
                      <button className="btn danger" onClick={remove}>Delete</button>
                    </>
                  )}
                </>
              }
            >
              <p>{wf.description}</p>
              <h3>Steps</h3>
              <StepFlow steps={wf.definition.steps} />
              <details>
                <summary className="small">Raw definition</summary>
                <JsonView value={{ params_schema: wf.params_schema, definition: wf.definition }} />
              </details>
            </Card>
            <Card title="Run or schedule">
              <ParamInputs schema={wf.params_schema} values={values} onChange={setValues} />
              <ErrorNote error={runErr} />
              <div className="row end">
                <button className="btn" onClick={() => setScheduling(true)}>Schedule…</button>
                <button className="btn primary" onClick={run} disabled={running}>{running ? 'Running…' : 'Run now'}</button>
              </div>
            </Card>
          </div>
        )}
      </div>
      {editor && (
        <Editor {...editor} onClose={() => setEditor(null)} onSaved={(w) => { setEditor(null); reload(); setSelected(w.code) }} />
      )}
      {scheduling && wf && (
        <Modal title={`Schedule ${wf.code}`} onClose={() => setScheduling(false)}>
          <ScheduleForm workflow={wf} initialParams={values} onDone={() => { setScheduling(false); navigate('schedules') }} />
        </Modal>
      )}
    </div>
  )
}
