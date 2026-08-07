function defaultApiBaseUrl() {
  const configuredUrl = import.meta.env.VITE_API_BASE_URL
  if (configuredUrl) return configuredUrl

  if (typeof window === 'undefined') return 'http://localhost:8000'

  const isViteDevelopmentServer = ['localhost', '127.0.0.1'].includes(window.location.hostname) &&
    ['5173', '5174'].includes(window.location.port)
  return isViteDevelopmentServer ? 'http://localhost:8000' : window.location.origin
}

const DEFAULT_BACKEND = {
  id: 'python',
  label: 'SakikoMind',
  baseUrl: defaultApiBaseUrl(),
  port: '8000'
}

export function createInitialSettings() {
  const saved = readSettings()
  return {
    userId: saved.userId || 'u1001',
    conversationId: saved.conversationId || '',
    apiBaseUrl: saved.apiBaseUrl || saved.endpoints?.python || DEFAULT_BACKEND.baseUrl
  }
}

export function saveSettings(settings) {
  localStorage.setItem('sakikomind.frontend.settings', JSON.stringify(settings))
}

export function backendMeta(settings) {
  return {
    ...DEFAULT_BACKEND,
    baseUrl: normalizeBaseUrl(settings.apiBaseUrl || DEFAULT_BACKEND.baseUrl)
  }
}

export async function requestHealth(settings) {
  return requestJson(backendMeta(settings).baseUrl, '/health')
}

export async function requestMonitor(settings) {
  return requestJson(backendMeta(settings).baseUrl, '/monitor')
}

export async function requestKnowledgeStats(settings) {
  return requestJson(backendMeta(settings).baseUrl, '/knowledge/stats')
}

export async function requestSkills(settings) {
  return requestJson(backendMeta(settings).baseUrl, '/skills')
}

export async function reloadSkills(settings) {
  return requestJson(backendMeta(settings).baseUrl, '/skills/reload', { method: 'POST' })
}

export async function requestSearch(settings, query, topK = 5) {
  const params = new URLSearchParams({ query, topK: String(topK) })
  return requestJson(backendMeta(settings).baseUrl, `/search?${params}`, { method: 'POST' })
}

export async function requestChat(settings, message) {
  const meta = backendMeta(settings)
  const payload = buildChatPayload(settings, message)
  const raw = await requestJson(meta.baseUrl, '/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  return normalizeChatResponse(raw)
}

export async function addKnowledge(settings, documents) {
  return requestJson(backendMeta(settings).baseUrl, '/knowledge/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ documents })
  })
}

export async function uploadKnowledge(settings, file) {
  const form = new FormData()
  form.append('file', file)
  return requestJson(backendMeta(settings).baseUrl, '/knowledge/upload', {
    method: 'POST',
    body: form
  })
}

export async function requestHandoffs(settings, status = '') {
  const params = new URLSearchParams({ limit: '50' })
  if (status) params.set('status', status)
  return requestJson(backendMeta(settings).baseUrl, `/handoffs?${params}`)
}

export async function updateHandoffStatus(settings, ticketId, status) {
  return requestJson(backendMeta(settings).baseUrl, `/handoffs/${encodeURIComponent(ticketId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status })
  })
}

function buildChatPayload(settings, message) {
  return {
    message,
    user_id: settings.userId || 'anonymous',
    conv_id: settings.conversationId || undefined
  }
}

function normalizeChatResponse(raw) {
  return {
    traceId: raw.trace_id || raw.traceId || '',
    conversationId: raw.conversation_id || raw.conversationId || raw.conv_id || '',
    response: raw.response || '',
    intent: raw.intent || 'other',
    agentType: raw.agent_type || raw.agentType || '',
    escalated: Boolean(raw.escalated),
    latencyMs: Number(raw.latency_ms ?? raw.latencyMs ?? 0),
    knowledgeUsed: Boolean(raw.knowledge_used ?? raw.knowledgeUsed),
    citations: Array.isArray(raw.citations) ? raw.citations : [],
    skillsUsed: Array.isArray(raw.skills_used) ? raw.skills_used : [],
    handoffTicket: raw.handoff_ticket || raw.handoffTicket || null,
    verified: raw.verified,
    grounded: raw.grounded,
    raw
  }
}

async function requestJson(baseUrl, path, options = {}) {
  const url = `${normalizeBaseUrl(baseUrl)}${path}`
  const response = await fetch(url, options)
  const text = await response.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!response.ok) {
    const detail = typeof data === 'string' ? data : JSON.stringify(data)
    throw new Error(`${response.status} ${response.statusText}: ${detail}`)
  }
  return data
}

function normalizeBaseUrl(value) {
  return String(value || '').replace(/\/+$/, '')
}

function readSettings() {
  try {
    return JSON.parse(localStorage.getItem('sakikomind.frontend.settings') || '{}')
  } catch {
    return {}
  }
}
