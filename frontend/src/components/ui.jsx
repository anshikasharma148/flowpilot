import { useEffect, useMemo, useState } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ gfm: true, breaks: false })

export function Markdown({ text }) {
  const html = useMemo(() => DOMPurify.sanitize(marked.parse(text || '')), [text])
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />
}

export function Badge({ value }) {
  const v = String(value ?? '').toLowerCase()
  return <span className={`badge badge-${v}`}>{value}</span>
}

export function Card({ title, actions, children, className = '' }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <h2>{title}</h2>
          <div className="card-actions">{actions}</div>
        </header>
      )}
      {children}
    </section>
  )
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>
}

export function ErrorNote({ error }) {
  if (!error) return null
  return <div className="error-note">{String(error.message || error)}</div>
}

export function Modal({ title, onClose, children, wide }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-label={title}>
        <header className="card-head">
          <h2>{title}</h2>
          <button className="btn ghost" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  )
}

/** Poll a loader every `ms` milliseconds; returns [data, error, reload]. */
export function usePoll(loader, ms = 5000, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    const run = () =>
      loader()
        .then((d) => alive && (setData(d), setError(null)))
        .catch((e) => alive && setError(e))
    run()
    const id = ms ? setInterval(run, ms) : null
    return () => {
      alive = false
      if (id) clearInterval(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ms, ...deps])
  return [data, error, () => setTick((t) => t + 1)]
}

export function JsonView({ value }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>
}
