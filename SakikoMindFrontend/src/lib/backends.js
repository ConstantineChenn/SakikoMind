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

const MANAGEMENT_TIMEOUT_MS = 15_000
const CHAT_TIMEOUT_MS = 120_000
const HANDOFF_STATUSES = new Set(['open', 'in_progress', 'resolved', 'closed'])

export function createInitialSettings() {
  const saved = readSettings()
  return {
    userId: savedString(saved.userId) || 'u1001',
    conversationId: savedString(saved.conversationId),
    apiBaseUrl: savedString(saved.apiBaseUrl) || savedString(saved.endpoints?.python) || DEFAULT_BACKEND.baseUrl
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

export async function requestHealth(settings, options = {}) {
  return requestJson(backendMeta(settings).baseUrl, '/health', {}, options)
}

export async function requestMonitor(settings, options = {}) {
  return requestJson(backendMeta(settings).baseUrl, '/monitor', {}, options)
}

export async function requestKnowledgeStats(settings, options = {}) {
  return requestJson(backendMeta(settings).baseUrl, '/knowledge/stats', {}, options)
}

export async function requestSkills(settings, options = {}) {
  return validateSkills(await requestJson(backendMeta(settings).baseUrl, '/skills', {}, options))
}

export async function reloadSkills(settings, options = {}) {
  return validateSkills(await requestJson(backendMeta(settings).baseUrl, '/skills/reload', { method: 'POST' }, options))
}

export async function requestSearch(settings, query, topK = 5, options = {}) {
  const params = new URLSearchParams({ query, top_k: String(topK) })
  return requestJson(backendMeta(settings).baseUrl, `/search?${params}`, { method: 'POST' }, options)
}

export async function requestChat(settings, message, options = {}) {
  const meta = backendMeta(settings)
  const payload = buildChatPayload(settings, message)
  const raw = await requestJson(meta.baseUrl, '/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }, { timeoutMs: CHAT_TIMEOUT_MS, ...options })
  return normalizeChatResponse(raw)
}

export async function addKnowledge(settings, documents, options = {}) {
  return requestJson(backendMeta(settings).baseUrl, '/knowledge/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ documents })
  }, options)
}

export async function uploadKnowledge(settings, file, options = {}) {
  const form = new FormData()
  form.append('file', file)
  return requestJson(backendMeta(settings).baseUrl, '/knowledge/upload', {
    method: 'POST',
    body: form
  }, options)
}

export async function requestHandoffs(settings, status = '', options = {}) {
  const params = new URLSearchParams({ limit: '50' })
  if (status) params.set('status', status)
  const data = await requestJson(backendMeta(settings).baseUrl, `/handoffs?${params}`, {}, options)
  requireArray(data.items, 'items')
  data.items.forEach((ticket, index) => validateHandoff(ticket, `items[${index}]`))
  return data
}

export async function updateHandoffStatus(settings, ticketId, status, options = {}) {
  const data = await requestJson(backendMeta(settings).baseUrl, `/handoffs/${encodeURIComponent(ticketId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status })
  }, options)
  return validateHandoff(data, 'handoff')
}

function buildChatPayload(settings, message) {
  return {
    message,
    user_id: settings.userId || 'anonymous',
    conv_id: settings.conversationId || undefined
  }
}

function normalizeChatResponse(raw) {
  requireString(raw.response, 'response')
  for (const field of ['trace_id', 'traceId', 'conversation_id', 'conversationId', 'conv_id', 'intent', 'agent_type', 'agentType']) {
    if (raw[field] !== undefined) requireString(raw[field], field)
  }
  for (const field of ['escalated', 'knowledge_used', 'knowledgeUsed']) {
    if (raw[field] !== undefined && typeof raw[field] !== 'boolean') invalidResponse(`${field} 必须是布尔值`)
  }
  for (const field of ['latency_ms', 'latencyMs']) {
    if (raw[field] !== undefined && (typeof raw[field] !== 'number' || !Number.isFinite(raw[field]) || raw[field] < 0)) {
      invalidResponse(`${field} 必须是非负数字`)
    }
  }
  if (raw.citations !== undefined) {
    requireArray(raw.citations, 'citations')
    raw.citations.forEach((citation, index) => {
      requireObject(citation, `citations[${index}]`)
      requireString(citation.source_id, `citations[${index}].source_id`)
      requireString(citation.title, `citations[${index}].title`)
    })
  }
  if (raw.skills_used !== undefined) {
    requireArray(raw.skills_used, 'skills_used')
    raw.skills_used.forEach((skill, index) => {
      requireObject(skill, `skills_used[${index}]`)
      requireString(skill.name, `skills_used[${index}].name`)
      requireString(skill.version, `skills_used[${index}].version`)
      if (skill.matched_keywords !== undefined) requireStringArray(skill.matched_keywords, `skills_used[${index}].matched_keywords`)
    })
  }
  const handoffTicket = raw.handoff_ticket ?? raw.handoffTicket ?? null
  if (handoffTicket !== null) validateHandoff(handoffTicket, 'handoff_ticket')
  return {
    traceId: raw.trace_id || raw.traceId || '',
    conversationId: raw.conversation_id || raw.conversationId || raw.conv_id || '',
    response: raw.response,
    intent: raw.intent || 'other',
    agentType: raw.agent_type || raw.agentType || '',
    escalated: Boolean(raw.escalated),
    latencyMs: Number(raw.latency_ms ?? raw.latencyMs ?? 0),
    knowledgeUsed: Boolean(raw.knowledge_used ?? raw.knowledgeUsed),
    citations: Array.isArray(raw.citations) ? raw.citations : [],
    skillsUsed: Array.isArray(raw.skills_used) ? raw.skills_used : [],
    handoffTicket,
    verified: raw.verified,
    grounded: raw.grounded,
    raw
  }
}

async function requestJson(baseUrl, path, fetchOptions = {}, { signal, timeoutMs = MANAGEMENT_TIMEOUT_MS } = {}) {
  const url = `${normalizeBaseUrl(baseUrl)}${path}`
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error('请求超时时间必须是正数')
  const controller = new AbortController()
  const cancel = () => controller.abort()
  if (signal?.aborted) throw requestError('AbortError', '请求已取消')
  signal?.addEventListener('abort', cancel, { once: true })
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  try {
    const response = await fetch(url, { ...fetchOptions, signal: controller.signal })
    const text = await response.text()
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}: ${text || '服务未提供错误详情'}`)
    let data
    try {
      data = JSON.parse(text)
    } catch {
      invalidResponse('服务未返回有效 JSON，请检查 API 地址或服务状态')
    }
    requireObject(data, '响应')
    return data
  } catch (error) {
    if (timedOut) throw requestError('TimeoutError', `请求超时（${timeoutMs / 1000} 秒），请稍后重试`)
    if (signal?.aborted) throw requestError('AbortError', '请求已取消')
    throw error
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', cancel)
  }
}

function requestError(name, message) {
  const error = new Error(message)
  error.name = name
  return error
}

function invalidResponse(detail) {
  throw new Error(`服务响应格式错误：${detail}`)
}

function requireObject(value, field) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) invalidResponse(`${field} 必须是对象`)
}

function requireString(value, field) {
  if (typeof value !== 'string' || !value.trim()) invalidResponse(`${field} 必须是非空文本`)
}

function requireArray(value, field) {
  if (!Array.isArray(value)) invalidResponse(`${field} 必须是数组`)
}

function requireStringArray(value, field) {
  requireArray(value, field)
  if (value.some((item) => typeof item !== 'string')) invalidResponse(`${field} 必须是文本数组`)
}

function validateSkills(data) {
  requireArray(data.skills, 'skills')
  data.skills.forEach((skill, index) => {
    const field = `skills[${index}]`
    requireObject(skill, field)
    for (const key of ['name', 'path', 'version']) requireString(skill[key], `${field}.${key}`)
    requireStringArray(skill.agents, `${field}.agents`)
    requireStringArray(skill.keywords, `${field}.keywords`)
  })
  return data
}

function validateHandoff(ticket, field) {
  requireObject(ticket, field)
  for (const key of ['ticket_id', 'priority', 'status', 'reason_label', 'summary', 'created_at']) {
    requireString(ticket[key], `${field}.${key}`)
  }
  if (!HANDOFF_STATUSES.has(ticket.status)) invalidResponse(`${field}.status 不是有效工单状态`)
  return ticket
}

function normalizeBaseUrl(value) {
  return String(value || '').replace(/\/+$/, '')
}

function readSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem('sakikomind.frontend.settings') || '{}')
    return saved !== null && typeof saved === 'object' && !Array.isArray(saved) ? saved : {}
  } catch {
    return {}
  }
}

function savedString(value) {
  return typeof value === 'string' ? value : ''
}
