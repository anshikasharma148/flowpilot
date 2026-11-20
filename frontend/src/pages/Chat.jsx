import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Markdown } from '../components/ui'
import Icon from '../components/icons'

const SAMPLES = [
  { tag: 'Assignment prompt 1', icon: 'clock', title: 'Daily alert digest with tickets',
    text: 'Every day at 9 AM, check all unresolved down and high operational alerts, generate a summary, create a ticket for each critical incident and email the report to the operations team.' },
  { tag: 'Assignment prompt 2', icon: 'ticket', title: 'Ticket long-running outages',
    text: 'Create a high-priority Jira ticket for all devices that have been down for more than 15 minutes.' },
  { tag: 'Assignment prompt 3', icon: 'gauge', title: 'Regional performance report',
    text: 'Generate the performance report for Mumbai for last 6 hours and email it to test@gmail.com' },
  { tag: 'Monitor', icon: 'runs', title: 'Check the last run',
    text: "What's the status of my last run?" },
  { tag: 'Define', icon: 'workflow', title: 'Create a workflow (no run)',
    text: 'Create a workflow that fetches LINK alerts and emails the report to noc@example.com' },
  { tag: 'Monitor', icon: 'clock', title: 'Review schedules',
    text: 'Show my schedules' },
]

const QUICK = [
  { label: 'Last run', icon: 'runs', text: "What's the status of my last run?" },
  { label: 'Schedules', icon: 'clock', text: 'Show my schedules' },
  { label: 'Tickets', icon: 'ticket', text: 'How many tickets have been created?' },
  { label: 'Overview', icon: 'dashboard', text: 'Give me an overview' },
]

const AGENT_SHORT = {
  'Workflow Selection Agent': 'Select',
  'Workflow Generation Agent': 'Generate',
  'Parameter Validation Agent': 'Validate',
  'Plugin Selection Agent': 'Plugins',
  'Execution Agent': 'Execute',
  'Reporting Agent': 'Report',
  'Monitoring Agent': 'Monitor',
}

function getSession() {
  try {
    let s = localStorage.getItem('chat-session')
    if (!s) {
      s = crypto.randomUUID().replace(/-/g, '')
      localStorage.setItem('chat-session', s)
    }
    return s
  } catch {
    return Math.random().toString(36).slice(2)
  }
}

function Pipeline({ trace }) {
  const [open, setOpen] = useState(false)
  if (!trace?.length) return null
  return (
    <div className="pipeline-wrap">
      <button className="pipeline" onClick={() => setOpen(!open)} title="Show agent details">
        {trace.map((t, i) => (
          <span key={i} className="pipe-step">
            <span className={`pipe-dot ${/needs|fail|blocked|error/i.test(t.summary) ? 'warn' : ''}`}>
              <Icon name="check" size={10} strokeWidth={3} />
            </span>
            {AGENT_SHORT[t.agent] || t.agent}
            {i < trace.length - 1 && <span className="pipe-line" />}
          </span>
        ))}
        <span className="pipe-toggle">{open ? 'Hide' : 'Details'}</span>
      </button>
      {open && (
        <ol className="trace-list">
          {trace.map((t, i) => (
            <li key={i}>
              <div><strong>{t.agent}</strong> <span className="muted">— {t.summary}</span>
                {t.detail?.source && <span className="src-pill">{t.detail.source}</span>}</div>
              {t.detail?.reasoning && <div className="muted small">{t.detail.reasoning}</div>}
              {t.detail?.steps && <div className="small mono muted">{t.detail.steps.join('  →  ')}</div>}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

export default function Chat({ navigate }) {
  const [session, setSession] = useState(getSession)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)

  useEffect(() => {
    api.get(`/chat/${session}`).then(setMessages).catch(() => setMessages([]))
  }, [session])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, busy])

  const send = async (text) => {
    const msg = (text ?? input).trim()
    if (!msg || busy) return
    setInput('')
    setMessages((m) => [...m, { id: `u${Date.now()}`, role: 'user', content: msg }])
    setBusy(true)
    try {
      const r = await api.post('/chat', { message: msg, session_id: session })
      setMessages((m) => [...m, { id: `a${Date.now()}`, role: 'assistant', content: r.reply, meta: r }])
    } catch (e) {
      setMessages((m) => [...m, { id: `e${Date.now()}`, role: 'assistant', content: `⚠️ ${e.message}`, meta: {} }])
    } finally {
      setBusy(false)
    }
  }

  const newChat = () => {
    const s = crypto.randomUUID().replace(/-/g, '')
    try {
      localStorage.setItem('chat-session', s)
    } catch {
      /* storage unavailable */
    }
    setSession(s)
  }

  const empty = messages.length === 0

  return (
    <div className="chat-page">
      <div className="chat-top">
        <div className="chat-title">
          <div className="chat-title-icon"><Icon name="sparkle" size={18} /></div>
          <div>
            <h1>AI Assistant</h1>
            <p className="muted small">Ask in plain English — agents select, validate, run or schedule the right workflow.</p>
          </div>
        </div>
        {!empty && <button className="btn" onClick={newChat}><Icon name="plus" size={16} /> New chat</button>}
      </div>

      <div className="chat-log">
        {empty && (
          <div className="welcome">
            <div className="hero">
              <div className="hero-badge"><Icon name="zap" size={14} /> 6 cooperating AI agents · LangGraph</div>
              <h2 className="hero-title">What should we <span className="grad">automate</span> today?</h2>
              <p className="hero-sub">
                Describe a task — summarise alerts, raise tickets, build performance reports, email the team — run it now or put it on a schedule.
              </p>
            </div>
            <div className="prompt-grid">
              {SAMPLES.map((s) => (
                <button key={s.text} className="prompt-card" onClick={() => send(s.text)}>
                  <div className="prompt-head">
                    <span className="prompt-icon"><Icon name={s.icon} size={16} /></span>
                    <span className="prompt-tag">{s.tag}</span>
                  </div>
                  <div className="prompt-title">{s.title}</div>
                  <div className="prompt-text">{s.text}</div>
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            <div className={`avatar ${m.role}`}><Icon name={m.role === 'user' ? 'user' : 'bot'} size={16} /></div>
            <div className="bubble">
              {m.role === 'assistant' ? <Markdown text={m.content} /> : m.content}
              {m.role === 'assistant' && (
                <div className="msg-foot">
                  <Pipeline trace={m.meta?.trace} />
                  <div className="row">
                    {m.meta?.run_id && (
                      <button className="pill-link" onClick={() => navigate('runs', m.meta.run_id)}>
                        <Icon name="runs" size={14} /> Open run #{m.meta.run_id}
                      </button>
                    )}
                    {m.meta?.schedule_id && (
                      <button className="pill-link" onClick={() => navigate('schedules')}>
                        <Icon name="clock" size={14} /> View schedule #{m.meta.schedule_id}
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="msg assistant">
            <div className="avatar assistant"><Icon name="bot" size={16} /></div>
            <div className="bubble typing">
              <span className="typing-dots"><i /><i /><i /></span> Agents are working on it…
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="composer-wrap">
        {!empty && (
          <div className="chips">
            {SAMPLES.slice(0, 3).map((s, i) => (
              <button key={s.text} className="chip" onClick={() => send(s.text)} disabled={busy}>
                <Icon name={s.icon} size={13} /> Prompt {i + 1}
              </button>
            ))}
            <span className="chip-sep" />
            {QUICK.map((q) => (
              <button key={q.text} className="chip chip-quiet" onClick={() => send(q.text)} disabled={busy}>
                <Icon name={q.icon} size={13} /> {q.label}
              </button>
            ))}
          </div>
        )}
        <form className="composer" onSubmit={(e) => { e.preventDefault(); send() }}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
            placeholder="Ask anything… e.g. Performance report for Mangaluru for the last 6 hours, email it to me@example.com"
            rows={2}
          />
          <button className="send-btn" disabled={busy || !input.trim()} aria-label="Send">
            <Icon name="send" size={18} />
          </button>
        </form>
        <div className="composer-hint">Enter to send · Shift+Enter for a new line</div>
      </div>
    </div>
  )
}
