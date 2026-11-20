import { Component, useEffect, useState } from 'react'
import { api } from './api'
import Icon from './components/icons'
import Dashboard from './pages/Dashboard'
import Chat from './pages/Chat'
import Workflows from './pages/Workflows'
import Runs from './pages/Runs'
import Schedules from './pages/Schedules'
import Outputs from './pages/Outputs'
import Plugins from './pages/Plugins'

class ErrorBoundary extends Component {
  state = { error: null }
  static getDerivedStateFromError(error) {
    return { error }
  }
  render() {
    if (this.state.error) {
      return (
        <div className="card">
          <h2>Something went wrong on this page</h2>
          <div className="error-note">{String(this.state.error.message || this.state.error)}</div>
          <button className="btn" onClick={() => this.setState({ error: null })}>Try again</button>
        </div>
      )
    }
    return this.props.children
  }
}

const SECTIONS = [
  {
    label: 'Workspace',
    pages: [
      { id: 'chat', label: 'AI Assistant', icon: 'chat', el: Chat },
      { id: 'dashboard', label: 'Dashboard', icon: 'dashboard', el: Dashboard },
    ],
  },
  {
    label: 'Automation',
    pages: [
      { id: 'workflows', label: 'Workflows', icon: 'workflow', el: Workflows },
      { id: 'runs', label: 'Run Monitor', icon: 'runs', el: Runs },
      { id: 'schedules', label: 'Schedules', icon: 'clock', el: Schedules },
    ],
  },
  {
    label: 'Integrations',
    pages: [
      { id: 'outputs', label: 'Tickets & Emails', icon: 'inbox', el: Outputs },
      { id: 'plugins', label: 'Plugins', icon: 'plug', el: Plugins },
    ],
  },
]
const PAGES = SECTIONS.flatMap((s) => s.pages)

function readHash() {
  const [page, param] = window.location.hash.replace(/^#\/?/, '').split('/')
  return { page: PAGES.some((p) => p.id === page) ? page : 'chat', param }
}

function StatusPanel({ health }) {
  if (!health) return null
  if (health.status !== 'ok') {
    return (
      <div className="status-panel">
        <div className="status-row"><span className="dot bad" />API offline</div>
        <div className="status-sub">Start the backend on port 8000</div>
      </div>
    )
  }
  const claude = health.llm.mode === 'llm'
  return (
    <div className="status-panel">
      <div className="status-title">System status</div>
      <div className="status-row"><span className="dot ok pulse" />API online</div>
      <div className="status-row" title={health.llm.last_error || ''}>
        <span className={`dot ${claude ? 'ok' : 'warn'}`} />
        {claude ? health.llm.model : 'Rule-based NLU'}
      </div>
      <div className="status-row"><span className="dot ok" />Scheduler · {health.timezone}</div>
    </div>
  )
}

export default function App() {
  const [route, setRoute] = useState(readHash())
  const [health, setHealth] = useState(null)

  useEffect(() => {
    const onHash = () => setRoute(readHash())
    window.addEventListener('hashchange', onHash)
    api.get('/health').then(setHealth).catch(() => setHealth({ status: 'down' }))
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const navigate = (page, param) => {
    window.location.hash = `/${page}${param ? `/${param}` : ''}`
  }
  const Page = PAGES.find((p) => p.id === route.page).el

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Icon name="zap" size={20} strokeWidth={2.2} /></div>
          <div>
            <div className="brand-name">FlowPilot</div>
            <div className="brand-tag">Agentic Automation</div>
          </div>
        </div>
        <nav>
          {SECTIONS.map((s) => (
            <div key={s.label} className="nav-section">
              <div className="nav-label">{s.label}</div>
              {s.pages.map((p) => (
                <button key={p.id} className={`nav-item ${route.page === p.id ? 'active' : ''}`} onClick={() => navigate(p.id)}>
                  <Icon name={p.icon} />
                  <span>{p.label}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>
        <StatusPanel health={health} />
      </aside>
      <main className="content">
        <ErrorBoundary key={route.page}>
          <Page navigate={navigate} param={route.param} />
        </ErrorBoundary>
      </main>
    </div>
  )
}
