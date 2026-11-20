import { api } from '../api'
import { Badge, Card, ErrorNote, usePoll } from '../components/ui'

export default function Plugins() {
  const [plugins, error, reload] = usePoll(() => api.get('/plugins'), 0)
  const toggle = async (p) => { await api.patch(`/plugins/${p.name}`, { enabled: !p.enabled }); reload() }

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>Plugins</h1>
          <p className="muted">
            Integrations that workflow steps call as <code>plugin.action</code>. Add a new one by dropping a module into
            <code> backend/app/plugins</code> with a <code>@register</code>-decorated class.
          </p>
        </div>
      </header>
      <ErrorNote error={error} />
      <div className="grid-cards">
        {plugins?.map((p) => (
          <Card key={p.name} title={p.name} actions={
            <>
              <Badge value={p.mode} />
              <label className="switch">
                <input type="checkbox" checked={p.enabled} onChange={() => toggle(p)} />
                <span>{p.enabled ? 'enabled' : 'disabled'}</span>
              </label>
            </>
          }>
            <p className="muted small">{p.category}</p>
            <p>{p.description}</p>
            <ul className="actions-list">
              {p.actions.map((a) => (
                <li key={a.name}>
                  <code>{p.name}.{a.name}</code> — {a.description}
                  {Object.keys(a.inputs).length > 0 && (
                    <div className="muted small">inputs: {Object.keys(a.inputs).join(', ')}</div>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </div>
  )
}
