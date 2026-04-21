const BASE_URL = ''

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options
  })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  }
  return res.json()
}

export const api = {
  get(path) {
    return request(path)
  },
  post(path, body) {
    return request(path, { method: 'POST', body: JSON.stringify(body) })
  },
  put(path, body) {
    return request(path, { method: 'PUT', body: JSON.stringify(body) })
  }
}
