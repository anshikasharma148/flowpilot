const BASE = import.meta.env.VITE_API_URL || ''

async function request(method, path, body) {
  const res = await fetch(`${BASE}/api${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 204) return null
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const d = data?.detail
    let msg = typeof d === 'string' ? d : ''
    if (d && typeof d === 'object') {
      msg = [...(d.errors || []), ...(d.missing || []).map((m) => `missing parameter: ${m}`)].join('; ')
    }
    throw new Error(msg || `${res.status} ${res.statusText}`)
  }
  return data
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b) => request('POST', p, b ?? {}),
  put: (p, b) => request('PUT', p, b),
  patch: (p, b) => request('PATCH', p, b),
  del: (p) => request('DELETE', p),
}

export function fmtDate(v) {
  if (!v) return '—'
  const d = new Date(v.endsWith?.('Z') || /[+-]\d\d:\d\d$/.test(v) ? v : `${v}Z`)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function fmtMs(ms) {
  if (ms == null) return '—'
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`
}
