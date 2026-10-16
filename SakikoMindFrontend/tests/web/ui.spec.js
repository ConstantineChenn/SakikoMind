import {
  test, expect, navigate, send, deferred,
  sampleChat, sampleTicket, sampleSkill, SETTINGS_KEY, API_ORIGIN
} from './fixtures.js'

const question = (page) => page.getByPlaceholder('输入问题，例如：我想申请退款，订单号是 #12345')
const assistant = (page) => page.locator('.message.assistant')
const visibleFeedback = (page, text) => page.getByText(text, { exact: true }).filter({ visible: true })
const file = { name: 'refund.md', mimeType: 'text/markdown', buffer: Buffer.from('# 退款政策\n审核后原路退回。') }

async function assertWorkspaceSpacing(page, panel, testInfo, label) {
  const geometry = await page.evaluate((selector) => {
    const content = document.querySelector(selector).getBoundingClientRect()
    const header = document.querySelector('.workspace-header').getBoundingClientRect()
    const feedback = document.querySelector('.workspace-feedback')
    const notices = [...feedback.querySelectorAll('p')]
      .filter((node) => node.getClientRects().length > 0)
      .map((node) => node.getBoundingClientRect())
    const lastNotice = notices.at(-1)
    return {
      contentGap: content.top - (lastNotice?.bottom ?? header.bottom),
      feedbackUnusedHeight: notices.length
        ? feedback.getBoundingClientRect().height - (lastNotice.bottom - notices[0].top)
        : 0,
      feedbackMessageCount: notices.length
    }
  }, panel)
  await testInfo.attach(`workspace-layout-${label}`, { body: JSON.stringify(geometry, null, 2), contentType: 'application/json' })
  expect(geometry.contentGap, 'Business content must follow its header or feedback without a large blank band').toBeLessThanOrEqual(40)
  expect(geometry.contentGap, 'Business content must not overlap feedback').toBeGreaterThanOrEqual(-1)
  expect(geometry.feedbackUnusedHeight, 'Feedback must size to its messages instead of consuming the content area').toBeLessThanOrEqual(24)
}

test.describe('Web UI — deterministic API mocks, not live model evaluation', () => {
  test.afterEach(async ({ api }) => {
    expect(api.pageErrors, 'The UI must not produce uncaught browser exceptions').toEqual([])
  })

  test('UI01 navigates all five workspaces with compact empty and populated feedback', async ({ page, api }, testInfo) => {
    const healthGate = deferred()
    api.override('GET', '/health', async ({ fulfill }) => { await healthGate.promise; await fulfill({ status: 'ok' }) })
    try {
      await api.open()
      for (const phase of ['empty', 'populated']) {
        if (phase === 'populated') {
          healthGate.resolve()
          await expect(page.getByRole('status')).toHaveText('健康检查已完成')
        } else await expect(page.locator('.workspace-feedback')).toBeHidden()
        for (const [tab, title, panel] of [
          ['知识库', '知识库管理', '.workspace-knowledge'],
          ['工单', '人工工单中心', '.workspace-handoffs'],
          ['监控', '运行监控中心', '.monitor-workspace'],
          ['设置', '系统设置与 Skill 治理', '.settings-workspace'],
          ['对话', '智能客服工作台', '.chat-panel']
        ]) {
          await navigate(page, tab)
          await expect(page.locator('.workspace-header h2')).toHaveText(title)
          await expect(page.locator(panel)).toBeVisible()
          await expect(page.locator('.workspace-nav button.active')).toHaveText(tab)
          await assertWorkspaceSpacing(page, panel, testInfo, `${phase}-${tab}`)
        }
      }
    } finally { healthGate.resolve() }
  })

  test('UI02 empty or whitespace questions never send a chat request', async ({ page, api }) => {
    await api.open()
    await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled()
    await question(page).fill('  \n  ')
    await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled()
    await page.locator('.composer').dispatchEvent('submit')
    expect(api.calls('POST', '/chat')).toHaveLength(0)
    await expect(page.locator('.message')).toHaveCount(0)
  })

  test('UI03 demo buttons populate editable drafts without making API calls', async ({ page, api }) => {
    await api.open()
    for (const [name, text] of [['退款政策', '首次购买专业版'], ['401 排障', '登录接口返回 401'], ['高风险转人工', '陌生设备提醒']]) {
      await page.getByRole('button', { name, exact: true }).click()
      await expect(question(page)).toHaveValue(new RegExp(text))
    }
    expect(api.calls('POST', '/chat')).toHaveLength(0)
  })

  test('UI04 trims question text and sends the configured user without a fabricated conversation', async ({ page, api }) => {
    await api.open()
    await send(page, '  订单 #12345 的物流进度？  ')
    await expect(assistant(page)).toContainText(sampleChat.response)
    expect(api.calls('POST', '/chat')[0].body).toEqual({ message: '订单 #12345 的物流进度？', user_id: 'web-test-user' })
    await expect(question(page)).toHaveValue('')
  })

  for (const [id, agent, intent, message] of [
    ['UI05', 'General', 'general', '营业时间是什么？'],
    ['UI06', 'Technical', 'technical', '登录接口返回 401'],
    ['UI07', 'Billing', 'billing', '如何申请退款？']
  ]) {
    test(`${id} displays ${agent} metadata returned by the API (routing itself is mocked)`, async ({ page, api }) => {
      api.state.chat = { ...sampleChat, agent_type: agent, intent, response: `${agent} 的固定测试答复` }
      await api.open()
      await send(page, message)
      await expect(assistant(page)).toContainText(`${agent} 的固定测试答复`)
      await expect(assistant(page).locator('.message-tags')).toContainText(`${intent} · ${agent}`)
      expect(api.calls('POST', '/chat')).toHaveLength(1)
    })
  }

  test('UI08 renders three supplied citations, Skills, latency and trace accurately', async ({ page, api }) => {
    api.state.chat = {
      ...sampleChat, knowledge_used: true,
      citations: [1, 2, 3].map((n) => ({ source_id: `policy-${n}`, title: `政策资料 ${n}`, chunk: n, score: 0.9 - n / 100 })),
      skills_used: [{ name: 'refund-policy', version: '1.0.0', matched_keywords: ['退款'] }]
    }
    await api.open()
    await send(page)
    await expect(page.locator('.citations li')).toHaveCount(3)
    for (const n of [1, 2, 3]) await expect(page.locator('.citations')).toContainText(`policy-${n}`)
    await expect(page.locator('.skill-usage')).toContainText('refund-policy 1.0.0')
    await expect(assistant(page)).toContainText('Trace trace-web-001')
    await expect(assistant(page)).toContainText('耗时 125 ms')
    await expect(assistant(page)).toContainText('RAG')
  })

  test('UI09 a response without retrieved evidence does not invent citation or Skill blocks', async ({ page, api }) => {
    await api.open()
    await send(page)
    await expect(assistant(page)).toContainText(sampleChat.response)
    await expect(page.locator('.citations')).toHaveCount(0)
    await expect(page.locator('.skill-usage')).toHaveCount(0)
  })

  test('UI10 pending requests disable sending and ignore duplicate form submit events', async ({ page, api }) => {
    const gate = deferred()
    api.override('POST', '/chat', async ({ fulfill }) => { await gate.promise; await fulfill(sampleChat) })
    await api.open()
    try {
      await send(page, '第一条问题')
      await expect.poll(() => api.calls('POST', '/chat').length).toBe(1)
      await question(page).fill('重复问题')
      await expect(page.getByRole('button', { name: '发送中', exact: true })).toBeDisabled()
      await page.locator('.composer').dispatchEvent('submit')
      gate.resolve()
      await expect(assistant(page)).toContainText(sampleChat.response)
      expect(api.calls('POST', '/chat')).toHaveLength(1)
      await expect(page.locator('.message.user')).toHaveCount(1)
    } finally { gate.resolve() }
  })

  test('UI11 HTTP errors remain visible in chat and a later request can recover', async ({ page, api }) => {
    api.override('POST', '/chat', ({ fulfill }) => fulfill({ detail: '模型暂不可用' }, 503))
    await api.open()
    await send(page)
    await expect(assistant(page)).toContainText('503')
    await expect(assistant(page)).toContainText('请求失败')
    await expect(page.locator('.assistant-thinking')).toHaveCount(0)
    api.override('POST', '/chat', ({ fulfill }) => fulfill(sampleChat))
    await send(page, '重新查询')
    await expect(assistant(page).last()).toContainText(sampleChat.response)
  })

  test('UI12 network failure releases the composer and permits retry', async ({ page, api }) => {
    api.override('POST', '/chat', ({ route }) => route.abort('failed'))
    await api.open()
    await send(page)
    await expect(assistant(page)).toContainText('请求失败')
    api.override('POST', '/chat', ({ fulfill }) => fulfill(sampleChat))
    await send(page, '网络恢复后的问题')
    await expect(assistant(page).last()).toContainText(sampleChat.response)
  })

  test('UI13 second turn carries the server conversation identifier', async ({ page, api }) => {
    await api.open()
    await send(page, '第一轮')
    await expect(assistant(page)).toHaveCount(1)
    await send(page, '第二轮')
    await expect(assistant(page)).toHaveCount(2)
    expect(api.calls('POST', '/chat')[1].body.conv_id).toBe('conv-web-001')
  })

  test('UI14 new conversation clears messages and persisted conversation identity', async ({ page, api }) => {
    await api.open()
    await send(page)
    await expect(assistant(page)).toHaveCount(1)
    await page.getByRole('button', { name: '新建会话', exact: true }).click()
    await expect(page.locator('.message')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: '开始一次客服对话' })).toBeVisible()
    expect(await page.evaluate((key) => JSON.parse(localStorage.getItem(key)).conversationId, SETTINGS_KEY)).toBe('')
    await send(page, '全新会话的问题')
    await expect(assistant(page)).toHaveCount(1)
    expect(api.calls('POST', '/chat')[1].body).not.toHaveProperty('conv_id')
  })

  test('UI15 starting a new conversation while a request is pending cannot leak its stale answer', async ({ page, api }) => {
    const gate = deferred()
    const oldHandled = deferred()
    let chatNumber = 0
    api.override('POST', '/chat', async ({ fulfill }) => {
      chatNumber += 1
      if (chatNumber === 1) {
        await gate.promise
        try { await fulfill({ ...sampleChat, response: '不应出现在新会话的旧答复', conversation_id: 'old-conversation' }) }
        finally { oldHandled.resolve() }
      } else await fulfill({ ...sampleChat, response: '新会话的有效答复', conversation_id: 'new-conversation' })
    })
    await api.open()
    try {
      await send(page, '旧会话问题')
      await expect.poll(() => api.calls('POST', '/chat').length).toBe(1)
      await page.getByRole('button', { name: '新建会话', exact: true }).click()
      gate.resolve()
      await oldHandled.promise
      await send(page, '新会话问题')
      await expect(assistant(page).last()).toContainText('新会话的有效答复')
      await expect(page.locator('.message')).not.toContainText(['不应出现在新会话的旧答复'])
      await expect(assistant(page)).toHaveCount(1)
      expect(api.calls('POST', '/chat')[1].body).not.toHaveProperty('conv_id')
    } finally { gate.resolve() }
  })

  test('UI16 knowledge search sends the contract query and renders supplied results', async ({ page, api }) => {
    await api.open()
    await navigate(page, '知识库')
    await page.getByPlaceholder('退款多久能到账').fill('退款规则')
    await page.getByRole('button', { name: '检索', exact: true }).click()
    await expect(page.locator('.result-item')).toContainText('审核后原路退回。')
    expect(api.calls('POST', '/search')[0].query).toEqual({ query: '退款规则', top_k: '5' })
  })

  test('UI17 blank knowledge query, title or content disables invalid operations', async ({ page, api }) => {
    await api.open()
    await navigate(page, '知识库')
    await page.getByPlaceholder('退款多久能到账').fill('   ')
    await expect(page.getByRole('button', { name: '检索', exact: true })).toBeDisabled()
    await page.getByPlaceholder('退款补充政策').fill('   ')
    await expect(page.getByRole('button', { name: '添加文档', exact: true })).toBeDisabled()
    await page.getByPlaceholder('退款补充政策').fill('有效标题')
    await page.getByPlaceholder('输入知识库内容').fill('  ')
    await expect(page.getByRole('button', { name: '添加文档', exact: true })).toBeDisabled()
    expect(api.calls('POST', '/knowledge/add')).toHaveLength(0)
    expect(api.calls('POST', '/search')).toHaveLength(0)
  })

  test('UI18 document import shows actual added_chunks=3 after refreshing statistics', async ({ page, api }) => {
    api.override('POST', '/knowledge/add', ({ fulfill, state }) => { state.chunks += 3; return fulfill({ added_chunks: 3 }) })
    await api.open()
    await navigate(page, '知识库')
    await page.getByPlaceholder('退款补充政策').fill(' 补充政策 ')
    await page.getByPlaceholder('输入知识库内容').fill('  新的可检索政策内容  ')
    await page.getByRole('button', { name: '添加文档', exact: true }).click()
    await expect(visibleFeedback(page, '已导入 3 条知识')).toBeVisible()
    expect(api.calls('POST', '/knowledge/add')[0].body).toEqual({ documents: [{ title: '补充政策', content: '新的可检索政策内容' }] })
    await navigate(page, '监控')
    await expect(page.locator('.metric-card').filter({ hasText: '知识片段' }).locator('strong')).toHaveText('58')
  })

  test('UI19 document import errors are visible in the knowledge workspace and retain the draft', async ({ page, api }) => {
    api.override('POST', '/knowledge/add', ({ fulfill }) => fulfill({ detail: '文档索引失败' }, 500))
    await api.open()
    await navigate(page, '知识库')
    await page.getByRole('button', { name: '添加文档', exact: true }).click()
    await expect(page.getByText(/文档索引失败/).filter({ visible: true })).toBeVisible()
    await expect(page.getByPlaceholder('退款补充政策')).toHaveValue('退款补充政策')
    await expect(page.getByRole('button', { name: '添加文档', exact: true })).toBeEnabled()
  })

  test('UI20 multipart upload shows the backend chunk count and resets the file input', async ({ page, api }) => {
    api.override('POST', '/knowledge/upload', ({ fulfill }) => fulfill({ added_chunks: 3 }))
    await api.open()
    await navigate(page, '知识库')
    await page.locator('input[type=file]').setInputFiles(file)
    await expect(visibleFeedback(page, '文件导入完成：3 条知识')).toBeVisible()
    expect(api.calls('POST', '/knowledge/upload')[0].body).toContain('filename="refund.md"')
    await expect(page.locator('input[type=file]')).toHaveValue('')
  })

  test('UI21 upload failure is visible and selecting the same file again retries successfully', async ({ page, api }) => {
    api.override('POST', '/knowledge/upload', ({ fulfill }) => fulfill({ detail: '文件解析失败' }, 422))
    await api.open()
    await navigate(page, '知识库')
    await page.locator('input[type=file]').setInputFiles(file)
    await expect(page.getByText(/文件解析失败/).filter({ visible: true })).toBeVisible()
    api.override('POST', '/knowledge/upload', ({ fulfill }) => fulfill({ added_chunks: 1 }))
    await page.locator('input[type=file]').setInputFiles(file)
    await expect(visibleFeedback(page, '文件导入完成：1 条知识')).toBeVisible()
    expect(api.calls('POST', '/knowledge/upload')).toHaveLength(2)
  })

  test('UI22 a pending upload prevents a second upload or document mutation', async ({ page, api }) => {
    const gate = deferred()
    api.override('POST', '/knowledge/upload', async ({ fulfill }) => { await gate.promise; await fulfill({ added_chunks: 1 }) })
    await api.open()
    await navigate(page, '知识库')
    try {
      await page.locator('input[type=file]').setInputFiles(file)
      await expect.poll(() => api.calls('POST', '/knowledge/upload').length).toBe(1)
      await expect(page.locator('input[type=file]')).toBeDisabled()
      await expect(page.getByRole('button', { name: '添加文档', exact: true })).toBeDisabled()
      gate.resolve()
      await expect(visibleFeedback(page, '文件导入完成：1 条知识')).toBeVisible()
      expect(api.calls('POST', '/knowledge/upload')).toHaveLength(1)
      expect(api.calls('POST', '/knowledge/add')).toHaveLength(0)
    } finally { gate.resolve() }
  })

  test('UI23 failed search clears stale results and shows a visible failure', async ({ page, api }) => {
    await api.open()
    await navigate(page, '知识库')
    await page.getByRole('button', { name: '检索', exact: true }).click()
    await expect(page.locator('.result-item')).toHaveCount(1)
    api.override('POST', '/search', ({ fulfill }) => fulfill({ detail: '检索服务不可用' }, 503))
    await page.getByRole('button', { name: '检索', exact: true }).click()
    await expect(page.getByText(/检索服务不可用/).filter({ visible: true })).toBeVisible()
    await expect(page.locator('.result-item')).toHaveCount(0)
  })

  test('UI24 ticket center shows only open and in-progress work items', async ({ page, api }) => {
    api.state.tickets = ['open', 'in_progress', 'resolved', 'closed'].map((status) => ({ ...sampleTicket, ticket_id: `T-${status}`, status }))
    await api.open()
    await navigate(page, '工单')
    await expect(page.locator('.handoff-row')).toHaveCount(2)
    await expect(page.locator('.handoff-list')).toContainText('T-open')
    await expect(page.locator('.handoff-list')).toContainText('T-in_progress')
    await expect(page.locator('.handoff-list')).not.toContainText('T-resolved')
  })

  test('UI25 ticket transition to in_progress sends PATCH and preserves the work item', async ({ page, api }) => {
    api.state.tickets = [structuredClone(sampleTicket)]
    await api.open()
    await navigate(page, '工单')
    await page.getByLabel('处理状态').selectOption('in_progress')
    await expect(visibleFeedback(page, '工单 T-WEB-001 已更新为 in_progress')).toBeVisible()
    await expect(page.getByLabel('处理状态')).toHaveValue('in_progress')
    expect(api.calls('PATCH', '/handoffs/T-WEB-001')[0].body).toEqual({ status: 'in_progress' })
  })

  test('UI26 resolving a ticket removes it from pending work', async ({ page, api }) => {
    api.state.tickets = [structuredClone(sampleTicket)]
    await api.open()
    await navigate(page, '工单')
    await page.getByLabel('处理状态').selectOption('resolved')
    await expect(page.locator('.handoff-row')).toHaveCount(0)
    await expect(page.getByText('暂无人工工单。发送高风险问题后会自动出现在这里。')).toBeVisible()
    expect(api.calls('PATCH', '/handoffs/T-WEB-001')[0].body.status).toBe('resolved')
  })

  test('UI27 failed ticket transition restores the actual status and displays the error', async ({ page, api }) => {
    api.state.tickets = [structuredClone(sampleTicket)]
    api.override('PATCH', '/handoffs/T-WEB-001', ({ fulfill }) => fulfill({ detail: '工单更新被拒绝' }, 409))
    await api.open()
    await navigate(page, '工单')
    await page.getByLabel('处理状态').selectOption('resolved')
    await expect(page.getByText(/工单更新被拒绝/).filter({ visible: true })).toBeVisible()
    await expect(page.getByLabel('处理状态')).toHaveValue('open')
    await expect(page.getByLabel('处理状态')).toBeEnabled()
    await expect(page.locator('.handoff-row')).toHaveCount(1)
  })

  test('UI28 escalated chat shows ticket evidence and reloads the ticket center', async ({ page, api }) => {
    api.override('POST', '/chat', ({ fulfill, state }) => {
      state.tickets = [structuredClone(sampleTicket)]
      return fulfill({ ...sampleChat, escalated: true, handoff_ticket: sampleTicket })
    })
    await api.open()
    await send(page, '重复扣款，请转人工')
    await expect(page.locator('.handoff-ticket')).toContainText('T-WEB-001')
    await expect(assistant(page)).toContainText('转人工')
    await navigate(page, '工单')
    await expect(page.locator('.handoff-row')).toContainText('重复扣款需要人工核实')
    expect(api.calls('GET', '/handoffs').length).toBeGreaterThanOrEqual(2)
  })

  test('UI29 monitor presents supplied agent, tool, alert and suggestion data', async ({ page, api }) => {
    api.state.monitor.active_alerts = [{ metric: 'latency', ts: '2026-10-14T09:00:00', severity: 'warning', message: '延迟偏高' }]
    api.state.monitor.recent_alerts = [...api.state.monitor.active_alerts]
    api.state.monitor.suggestions = [{ priority: 1, title: '检查检索耗时', action: '查看检索追踪信息' }]
    await api.open()
    await navigate(page, '监控')
    await expect(page.locator('.monitor-row').filter({ hasText: 'General' })).toContainText('成功率 95%')
    await expect(page.locator('.monitor-row').filter({ hasText: 'knowledge_search' })).toContainText('熔断 closed')
    await expect(page.locator('.alert-row')).toContainText('延迟偏高')
    await expect(page.locator('.suggestion-row')).toContainText('P1 · 检查检索耗时')
  })

  test('UI30 partial monitor failure updates successful statistics and warns about stale data', async ({ page, api }) => {
    await api.open()
    api.state.chunks = 77
    api.override('GET', '/monitor', ({ fulfill }) => fulfill({ detail: '监控后端故障' }, 503))
    await navigate(page, '监控')
    await page.getByRole('button', { name: '刷新全局状态', exact: true }).click()
    await expect(page.locator('.metric-card').filter({ hasText: '知识片段' }).locator('strong')).toHaveText('77')
    await expect(page.getByText(/监控后端故障/).filter({ visible: true })).toBeVisible()
    await expect(page.getByText(/可能已过期/).filter({ visible: true })).toBeVisible()
    await expect(page.locator('.monitor-row').filter({ hasText: 'General' })).toContainText('95%')
  })

  test('UI31 Skills reload displays the returned version and count', async ({ page, api }) => {
    await api.open()
    api.state.skills = [{ ...sampleSkill, version: '2.0.0' }]
    await navigate(page, '设置')
    await page.getByRole('button', { name: '热更新', exact: true }).click()
    await expect(page.locator('.skill-item')).toContainText('2.0.0')
    await expect(visibleFeedback(page, '已热更新 1 个 Skills')).toBeVisible()
    expect(api.calls('POST', '/skills/reload')).toHaveLength(1)
  })

  test('UI32 malformed Skills metadata produces an error without crashing or replacing valid entries', async ({ page, api }) => {
    await api.open()
    api.override('POST', '/skills/reload', ({ fulfill }) => fulfill({ count: 1, skills: [{ ...sampleSkill, agents: null, keywords: '退款' }] }))
    await navigate(page, '设置')
    await page.getByRole('button', { name: '热更新', exact: true }).click()
    await expect(page.getByText(/服务响应格式错误/).filter({ visible: true })).toBeVisible()
    await expect(page.locator('.skill-item')).toContainText('1.0.0')
    await navigate(page, '对话')
    await expect(question(page)).toBeVisible()
  })

  test('UI33 user input, model output and citation titles render as text, not executable markup', async ({ page, api }) => {
    const payload = '<img src=x onerror="window.__sakikoXss=1"><script>window.__sakikoXss=2</script>'
    api.state.chat = { ...sampleChat, response: payload, citations: [{ source_id: 'unsafe-title', title: payload, chunk: 0, score: 1 }] }
    await api.open()
    await send(page, payload)
    await expect(assistant(page).locator('p').first()).toHaveText(payload)
    await expect(page.locator('.message.user p')).toHaveText(payload)
    await expect(page.locator('.citations')).toContainText(payload)
    await expect(page.locator('.messages img, .messages script')).toHaveCount(0)
    expect(await page.evaluate(() => window.__sakikoXss)).toBeUndefined()
  })

  test('UI34 settings persist across reload and normalize API links', async ({ page, api }) => {
    await api.open()
    await navigate(page, '设置')
    await page.getByLabel('用户 ID', { exact: true }).fill('persistent-user')
    await page.getByLabel('用户 ID', { exact: true }).blur()
    await page.getByLabel('API 地址', { exact: true }).fill(`${API_ORIGIN}///`)
    await page.getByLabel('API 地址', { exact: true }).blur()
    await page.reload()
    await navigate(page, '设置')
    await expect(page.getByLabel('用户 ID', { exact: true })).toHaveValue('persistent-user')
    await expect(page.getByRole('link', { name: 'API 文档', exact: true })).toHaveAttribute('href', `${API_ORIGIN}/docs`)
    await navigate(page, '对话')
    await send(page)
    await expect(assistant(page)).toHaveCount(1)
    expect(api.calls('POST', '/chat')[0].body.user_id).toBe('persistent-user')
  })

  test('UI35 narrow screen keeps navigation, composer and management panels within the viewport', async ({ page, api }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await api.open()
    for (const [tab, panel] of [['对话', '.chat-panel'], ['知识库', '.workspace-knowledge'], ['工单', '.workspace-handoffs'], ['监控', '.monitor-workspace'], ['设置', '.settings-workspace']]) {
      await navigate(page, tab)
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
      expect(overflow, `${tab} should not require page-level horizontal scrolling`).toBeLessThanOrEqual(1)
      await assertWorkspaceSpacing(page, panel, testInfo, `narrow-${tab}`)
    }
    await navigate(page, '对话')
    await send(page, '窄屏浏览器中的中文问题')
    await expect(assistant(page)).toContainText(sampleChat.response)
  })
})
