import { test, expect } from '@playwright/test'

const chat = {
  trace_id: 'trace-web-adapter',
  conv_id: 'conv-web-adapter',
  response: '已查询到退款政策。',
  intent: 'refund',
  agent_type: 'service',
  escalated: false,
  latency_ms: 12,
  knowledge_used: false,
  citations: [],
  skills_used: [],
  handoff_ticket: null
}

const ticket = {
  ticket_id: 'ticket-web-adapter',
  priority: 'P1',
  status: 'open',
  reason_label: '账务争议',
  summary: '用户报告重复扣款',
  created_at: '2026-10-14T09:00:00Z'
}

const skills = {
  count: 1,
  skills: [{ name: 'refund', version: 'v1', path: 'refund.md', agents: ['service'], keywords: ['退款'] }]
}

test.beforeEach(async ({ page }) => {
  // Isolate the mounted workspace's read requests from any local live backend.
  const initialData = {
    '/health': { status: 'ok' },
    '/knowledge/stats': { total_chunks: 0 },
    '/monitor': { agent_stats: {}, tool_stats: {}, active_alerts: [], recent_alerts: [], suggestions: [] },
    '/skills': { count: 0, skills: [] },
    '/handoffs': { items: [] }
  }
  await page.route(/\/(health|monitor|knowledge\/stats|skills|handoffs)(?:\?.*)?$/, (route) => {
    const data = initialData[new URL(route.request().url()).pathname]
    return data ? route.fulfill({ json: data }) : route.fallback()
  })
  await page.goto('/')
})

async function respond(page, data, options = {}) {
  await page.route('**/__adapter/**', (route) => route.fulfill({
    status: options.status ?? 200,
    contentType: options.contentType ?? 'application/json',
    body: options.body ?? JSON.stringify(data)
  }))
}

async function invoke(page, method, args = []) {
  return page.evaluate(async ({ method, args }) => {
    const api = await import('/src/lib/backends.js')
    const settings = { apiBaseUrl: `${location.origin}/__adapter`, userId: 'web-test-user', conversationId: '' }
    try {
      return { ok: true, data: await api[method](settings, ...args) }
    } catch (error) {
      return { ok: false, name: error.name, message: error.message }
    }
  }, { method, args })
}

test('search sends the Python top_k contract with a nondefault value', async ({ page }) => {
  let request
  await page.route('**/__adapter/search?**', (route) => {
    request = route.request()
    return route.fulfill({ json: { query: '退款 & 续费', results: [] } })
  })
  expect((await invoke(page, 'requestSearch', ['退款 & 续费', 2])).ok).toBe(true)
  const url = new URL(request.url())
  expect(request.method()).toBe('POST')
  expect(url.searchParams.get('query')).toBe('退款 & 续费')
  expect(url.searchParams.get('top_k')).toBe('2')
  expect(url.searchParams.has('topK')).toBe(false)
})

test('chat preserves request fields and valid response metadata', async ({ page }) => {
  let body
  await page.route('**/__adapter/chat', (route) => {
    body = route.request().postDataJSON()
    return route.fulfill({ json: chat })
  })
  const result = await invoke(page, 'requestChat', ['退款规则'])
  expect(body).toEqual({ message: '退款规则', user_id: 'web-test-user' })
  expect(result).toMatchObject({ ok: true, data: { response: chat.response, conversationId: chat.conv_id, traceId: chat.trace_id } })
})

for (const [label, body] of [
  ['HTML', '<html>reverse proxy error</html>'],
  ['empty body', ''],
  ['JSON null', 'null'],
  ['JSON array', '[]']
]) {
  test(`successful status with ${label} is rejected`, async ({ page }) => {
    await respond(page, null, { body, contentType: label === 'HTML' ? 'text/html' : 'application/json' })
    const result = await invoke(page, 'requestChat', ['测试'])
    expect(result.ok).toBe(false)
    expect(result.message).toContain('响应格式错误')
  })
}

for (const [label, response] of [['missing', undefined], ['blank', '  '], ['object', { text: '错误结构' }]]) {
  test(`chat ${label} response text is rejected`, async ({ page }) => {
    await respond(page, { ...chat, response })
    const result = await invoke(page, 'requestChat', ['测试'])
    expect(result.ok).toBe(false)
    expect(result.message).toContain('response')
  })
}

test('HTTP errors retain status and server detail', async ({ page }) => {
  await respond(page, { detail: '人工工单服务未初始化' }, { status: 503 })
  const result = await invoke(page, 'requestHandoffs')
  expect(result.ok).toBe(false)
  expect(result.message).toContain('503')
  expect(result.message).toContain('人工工单服务未初始化')
})

test('network failure is rejected instead of returning success', async ({ page }) => {
  await page.route('**/__adapter/**', (route) => route.abort('failed'))
  const result = await invoke(page, 'requestHealth')
  expect(result.ok).toBe(false)
  expect(result.message).toBeTruthy()
})

async function delayResponse(page, data) {
  await page.route('**/__adapter/**', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 250))
    // An intentionally aborted fetch may already be closed when the delay ends.
    await route.fulfill({ json: data }).catch(() => {})
  })
}

test('chat uses its explicit timeout and can retry after a timeout', async ({ page }) => {
  await delayResponse(page, chat)
  const result = await invoke(page, 'requestChat', ['测试', { timeoutMs: 40 }])
  expect(result).toMatchObject({ ok: false, name: 'TimeoutError' })
  expect(result.message).toContain('请求超时')
  await page.unroute('**/__adapter/**')
  await respond(page, chat)
  expect((await invoke(page, 'requestChat', ['重试', { timeoutMs: 2000 }])).ok).toBe(true)
})

test('management request uses its explicit timeout', async ({ page }) => {
  await delayResponse(page, { status: 'ok' })
  expect(await invoke(page, 'requestHealth', [{ timeoutMs: 40 }])).toMatchObject({ ok: false, name: 'TimeoutError' })
})

test('default deadlines keep chat pending after management times out', async ({ page }) => {
  await page.clock.install()
  let requests = 0
  await page.route('**/__adapter/**', () => { requests += 1 })
  await page.evaluate(async () => {
    const api = await import('/src/lib/backends.js')
    const settings = { apiBaseUrl: `${location.origin}/__adapter`, userId: 'web-test-user' }
    window.__adapterDeadlines = { chat: 'pending', health: 'pending' }
    for (const [key, pending] of [
      ['chat', api.requestChat(settings, '测试')],
      ['health', api.requestHealth(settings)]
    ]) {
      pending.then(
        () => { window.__adapterDeadlines[key] = 'success' },
        (error) => { window.__adapterDeadlines[key] = error.name }
      )
    }
  })
  await expect.poll(() => requests).toBe(2)
  await page.clock.fastForward(15_100)
  await expect.poll(() => page.evaluate(() => window.__adapterDeadlines)).toEqual({ chat: 'pending', health: 'TimeoutError' })
  await page.clock.fastForward(105_000)
  await expect.poll(() => page.evaluate(() => window.__adapterDeadlines.chat)).toBe('TimeoutError')
})

test('caller cancellation aborts an in-flight chat', async ({ page }) => {
  await delayResponse(page, chat)
  const result = await page.evaluate(async () => {
    const api = await import('/src/lib/backends.js')
    const controller = new AbortController()
    const pending = api.requestChat({ apiBaseUrl: `${location.origin}/__adapter` }, '测试', { signal: controller.signal, timeoutMs: 2000 })
    setTimeout(() => controller.abort(), 30)
    try {
      await pending
      return { ok: true }
    } catch (error) {
      return { ok: false, name: error.name, message: error.message }
    }
  })
  expect(result).toMatchObject({ ok: false, name: 'AbortError' })
  expect(result.message).toContain('取消')
})

test('already cancelled request sends no network request', async ({ page }) => {
  let requests = 0
  await page.route('**/__adapter/**', (route) => {
    requests += 1
    return route.fulfill({ json: chat })
  })
  const result = await page.evaluate(async () => {
    const api = await import('/src/lib/backends.js')
    const controller = new AbortController()
    controller.abort()
    try {
      await api.requestChat({ apiBaseUrl: `${location.origin}/__adapter` }, '测试', { signal: controller.signal })
      return 'success'
    } catch (error) {
      return error.name
    }
  })
  expect(result).toBe('AbortError')
  expect(requests).toBe(0)
})

test('valid skills response preserves data', async ({ page }) => {
  await respond(page, skills)
  expect(await invoke(page, 'requestSkills')).toEqual({ ok: true, data: skills })
})

for (const method of ['requestSkills', 'reloadSkills']) {
  test(`${method} rejects malformed skill arrays`, async ({ page }) => {
    await respond(page, { ...skills, skills: [{ ...skills.skills[0], agents: null }] })
    const result = await invoke(page, method)
    expect(result.ok).toBe(false)
    expect(result.message).toContain('skills[0].agents')
  })
}

test('missing skills list is rejected', async ({ page }) => {
  await respond(page, { count: 1 })
  expect(await invoke(page, 'requestSkills')).toMatchObject({ ok: false, message: expect.stringContaining('skills') })
})

test('valid handoff list and update responses are preserved', async ({ page }) => {
  await respond(page, { items: [ticket] })
  expect(await invoke(page, 'requestHandoffs')).toEqual({ ok: true, data: { items: [ticket] } })
  await page.unroute('**/__adapter/**')
  await respond(page, { ...ticket, status: 'resolved' })
  expect(await invoke(page, 'updateHandoffStatus', [ticket.ticket_id, 'resolved'])).toEqual({ ok: true, data: { ...ticket, status: 'resolved' } })
})

test('missing handoff priority is rejected before rendering', async ({ page }) => {
  const { priority, ...broken } = ticket
  await respond(page, { items: [broken] })
  expect(await invoke(page, 'requestHandoffs')).toMatchObject({ ok: false, message: expect.stringContaining('priority') })
})

test('malformed handoff in chat is rejected before rendering', async ({ page }) => {
  await respond(page, { ...chat, handoff_ticket: { ...ticket, priority: 1 } })
  expect(await invoke(page, 'requestChat', ['测试'])).toMatchObject({ ok: false, message: expect.stringContaining('priority') })
})

test('unknown handoff status from update is rejected', async ({ page }) => {
  await respond(page, { ...ticket, status: 'unknown' })
  expect(await invoke(page, 'updateHandoffStatus', [ticket.ticket_id, 'resolved'])).toMatchObject({ ok: false, message: expect.stringContaining('status') })
})

for (const value of ['null', '[]', '"saved-string"', '{invalid', '{"userId":23,"conversationId":[],"apiBaseUrl":{}}']) {
  test(`invalid persisted settings ${value} safely use defaults`, async ({ page }) => {
    const result = await page.evaluate(async (value) => {
      const api = await import('/src/lib/backends.js')
      localStorage.setItem('sakikomind.frontend.settings', value)
      return api.createInitialSettings()
    }, value)
    expect(result.userId).toBe('u1001')
    expect(result.conversationId).toBe('')
    expect(typeof result.apiBaseUrl).toBe('string')
    expect(result.apiBaseUrl).not.toBe('')
  })
}

test('valid saved and legacy endpoint settings remain compatible', async ({ page }) => {
  const result = await page.evaluate(async () => {
    const api = await import('/src/lib/backends.js')
    localStorage.setItem('sakikomind.frontend.settings', JSON.stringify({ userId: 'saved-user', conversationId: 'saved-conv', endpoints: { python: 'http://localhost:8999/' } }))
    return api.createInitialSettings()
  })
  expect(result).toEqual({ userId: 'saved-user', conversationId: 'saved-conv', apiBaseUrl: 'http://localhost:8999/' })
})
