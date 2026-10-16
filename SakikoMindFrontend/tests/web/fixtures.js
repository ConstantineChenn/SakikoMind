import { test as base, expect } from '@playwright/test'

export const API_ORIGIN = 'http://sakiko.test'
export const SETTINGS_KEY = 'sakikomind.frontend.settings'
export const sampleTicket = {
  ticket_id: 'T-WEB-001', status: 'open', priority: 'P1',
  reason_label: '账单风险', summary: '重复扣款需要人工核实',
  trace_id: 'trace-ticket-001', created_at: '2026-10-14T09:00:00+08:00'
}
export const sampleSkill = {
  name: 'refund-policy', path: 'billing/refund', version: '1.0.0',
  owner: 'support', agents: ['Billing'], keywords: ['退款', '账单']
}
export const sampleChat = {
  response: '测试答复：请提供订单号以查询处理进度。',
  conversation_id: 'conv-web-001', trace_id: 'trace-web-001',
  intent: 'general', agent_type: 'General', latency_ms: 125,
  knowledge_used: false, escalated: false, citations: [], skills_used: []
}
export const sampleMonitor = {
  agent_stats: { General: { success_rate: 0.95, avg_ms: 125, routing_score: 0.9 } },
  tool_stats: { knowledge_search: { success_rate: 1, avg_latency_ms: 12, circuit_state: 'closed', consecutive_fails: 0 } },
  active_alerts: [], recent_alerts: [], suggestions: []
}

// These fixtures validate browser behavior against controlled API contracts.
// They do not run the real model, ChromaDB, routing policy, or Skills engine.
export const test = base.extend({
  api: async ({ page }, use, testInfo) => {
    const state = {
      tickets: [], skills: [structuredClone(sampleSkill)],
      chat: structuredClone(sampleChat), monitor: structuredClone(sampleMonitor),
      chunks: 55, searchResults: [{ id: 'doc-1', title: '退款规则', content: '审核后原路退回。', score: 0.92 }]
    }
    const requests = []
    const overrides = new Map()
    const pageErrors = []
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await page.addInitScript(({ key, origin }) => {
      if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({
        apiBaseUrl: origin, userId: 'web-test-user', conversationId: ''
      }))
    }, { key: SETTINGS_KEY, origin: API_ORIGIN })

    await page.route(`${API_ORIGIN}/**`, async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const key = `${request.method()} ${url.pathname}`
      let body
      try { body = request.postDataJSON() } catch { body = request.postData() }
      requests.push({ method: request.method(), path: url.pathname, query: Object.fromEntries(url.searchParams), body })
      const fulfill = (json, status = 200) => route.fulfill({
        status, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' }, body: JSON.stringify(json)
      })
      if (request.method() === 'OPTIONS') {
        await route.fulfill({ status: 204, headers: {
          'access-control-allow-origin': '*', 'access-control-allow-methods': 'GET,POST,PATCH,OPTIONS',
          'access-control-allow-headers': 'content-type'
        } })
        return
      }
      const custom = overrides.get(key)
      if (custom) {
        await custom({ route, request, url, body, state, fulfill })
        return
      }
      switch (key) {
        case 'GET /health': await fulfill({ status: 'ok' }); break
        case 'GET /knowledge/stats': await fulfill({ total_chunks: state.chunks }); break
        case 'GET /monitor': await fulfill(state.monitor); break
        case 'GET /skills': await fulfill({ skills: state.skills }); break
        case 'POST /skills/reload': await fulfill({ skills: state.skills, count: state.skills.length }); break
        case 'GET /handoffs': await fulfill({ items: state.tickets }); break
        case 'POST /chat': await fulfill(state.chat); break
        case 'POST /search': await fulfill({ results: state.searchResults }); break
        case 'POST /knowledge/add': state.chunks += body.documents.length; await fulfill({ added: body.documents.length }); break
        case 'POST /knowledge/upload': state.chunks += 1; await fulfill({ added: 1 }); break
        default:
          if (request.method() === 'PATCH' && url.pathname.startsWith('/handoffs/')) {
            const id = decodeURIComponent(url.pathname.slice('/handoffs/'.length))
            const ticket = state.tickets.find((item) => item.ticket_id === id)
            if (!ticket) { await fulfill({ detail: 'ticket not found' }, 404); break }
            ticket.status = body.status
            await fulfill(ticket)
          } else await fulfill({ detail: `Unexpected mock endpoint: ${key}` }, 500)
      }
    })
    const api = {
      state, requests, pageErrors,
      override(method, endpoint, handler) { overrides.set(`${method} ${endpoint}`, handler) },
      calls(method, endpoint) { return requests.filter((item) => item.method === method && item.path === endpoint) },
      async open() {
        await page.goto('/')
        await expect(page.getByRole('heading', { name: '智能客服工作台', exact: true })).toBeVisible()
        await expect.poll(() => requests.filter((item) => item.method === 'GET').length).toBeGreaterThanOrEqual(5)
        // Wait for the five fulfilled initialization requests to settle, rather than sleeping.
        await expect(page.locator('.status-panel')).toContainText('55')
      }
    }
    await use(api)
    await testInfo.attach('mock-http-requests', { body: JSON.stringify(requests, null, 2), contentType: 'application/json' })
    if (pageErrors.length) await testInfo.attach('page-errors', { body: pageErrors.join('\n'), contentType: 'text/plain' })
  }
})

export { expect }
export async function navigate(page, name) {
  await page.getByRole('navigation', { name: '工作区导航' }).getByRole('button', { name, exact: true }).click()
}
export async function send(page, message = '请查询我的订单') {
  await page.getByPlaceholder('输入问题，例如：我想申请退款，订单号是 #12345').fill(message)
  await page.getByRole('button', { name: '发送', exact: true }).click()
}
export function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
