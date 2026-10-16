<template>
  <main class="app-shell">
    <header class="site-header">
      <section class="brand">
        <img class="brand-avatar" src="./assets/sakiko-avatar.png" alt="Sakiko 客服头像" />
        <div>
          <h1>Sakiko Console</h1>
          <p>可观测、可追溯的企业智能客服 Agent</p>
        </div>
      </section>
      <button class="settings-shortcut" type="button" @click="activeWorkspace = 'settings'">
        系统设置
      </button>
    </header>

    <section class="workspace">
      <header class="workspace-header">
        <div>
          <span class="eyebrow">SakikoMind Workspace</span>
          <h2>{{ workspacePresentation.title }}</h2>
          <p>{{ workspacePresentation.description }} · {{ currentBackend.baseUrl }}</p>
        </div>
        <div class="header-actions">
          <nav class="workspace-nav" aria-label="工作区导航">
            <button
              v-for="item in workspaceTabs"
              :key="item.id"
              :class="{ active: activeWorkspace === item.id }"
              :aria-pressed="activeWorkspace === item.id"
              @click="activeWorkspace = item.id"
            >
              {{ item.label }}
            </button>
          </nav>
          <button
            v-if="activeWorkspace === 'chat'"
            class="secondary-button"
            type="button"
            @click="startNewConversation"
            :disabled="operationBusy"
          >
            新建会话
          </button>
          <a :href="docsUrl" target="_blank" rel="noreferrer">API 文档</a>
        </div>
      </header>

      <div class="workspace-feedback" aria-live="polite">
        <p v-if="statusText" role="status">{{ statusText }}</p>
        <p v-if="statsError" role="alert">{{ statsError }}</p>
      </div>

      <section v-show="activeWorkspace === 'chat'" class="chat-panel">
        <div class="messages" ref="messageList">
          <article v-for="item in messages" :key="item.id" :class="['message', item.role]">
            <div class="message-meta">
              <span>{{ item.role === 'user' ? '用户' : currentBackend.label }}</span>
              <span class="message-tags">
                <small v-if="item.meta">{{ item.meta }}</small>
                <small v-if="item.latencyMs">耗时 {{ formatMilliseconds(item.latencyMs) }}</small>
                <code v-if="item.traceId">Trace {{ item.traceId }}</code>
              </span>
            </div>
            <p>{{ item.content }}</p>
            <section v-if="item.citations?.length" class="citations">
              <strong>参考资料</strong>
              <ul>
                <li v-for="citation in item.citations" :key="`${citation.source_id}-${citation.chunk}`">
                  <span>{{ citation.title }}</span>
                  <small>{{ citation.source_id }} · 相关度 {{ citation.score }}</small>
                </li>
              </ul>
            </section>
            <section v-if="item.skills?.length" class="skill-usage">
              <strong>生效规则</strong>
              <ul>
                <li v-for="skill in item.skills" :key="skill.name">
                  {{ skill.name }} {{ skill.version }}
                  <small v-if="skill.matched_keywords?.length">· {{ skill.matched_keywords.join('、') }}</small>
                </li>
              </ul>
            </section>
            <section v-if="item.handoffTicket" class="handoff-ticket">
              <div class="handoff-title">
                <strong>人工升级工单</strong>
                <span :class="['priority', item.handoffTicket.priority.toLowerCase()]">{{ item.handoffTicket.priority }}</span>
              </div>
              <p>{{ item.handoffTicket.reason_label }} · {{ item.handoffTicket.status }}</p>
              <p>{{ item.handoffTicket.summary }}</p>
              <small>
                工单号 {{ item.handoffTicket.ticket_id }}
                <template v-if="item.handoffTicket.trace_id"> · Trace {{ item.handoffTicket.trace_id }}</template>
                · 已写入 SakikoMind 内置工单中心
              </small>
            </section>
          </article>
          <div v-if="messages.length === 0" class="empty-state">
            <h3>开始一次客服对话</h3>
            <p>向 SakikoMind 智能客服提问，查看 Agent 路由、RAG 与人工升级结果。</p>
            <section class="starter-benefits">
              <article>
                <strong>可解释回答</strong>
                <span>每次业务回答附带 RAG 参考资料与相关度。</span>
              </article>
              <article>
                <strong>受控人工升级</strong>
                <span>高风险场景自动生成可追溯的内置工单。</span>
              </article>
              <article>
                <strong>全链路可观测</strong>
                <span>Trace、阶段延迟与告警状态可在监控区查看。</span>
              </article>
            </section>
          </div>
          <div v-if="chatBusy" class="assistant-thinking">
            <span class="thinking-dot"></span>
            SakikoMind 正在检索知识、路由 Agent 并生成回复…
          </div>
        </div>

        <section class="demo-library">
          <div>
            <strong>演示捷径</strong>
            <span>填入场景后点击发送，适合录屏展示</span>
          </div>
          <button
            v-for="scenario in demoScenarios"
            :key="scenario.id"
            class="demo-button"
            type="button"
            @click="applyDemo(scenario)"
          >
            {{ scenario.title }}
          </button>
        </section>

        <form class="composer" @submit.prevent="sendMessage">
          <textarea v-model="draft" aria-label="客服问题" rows="3" placeholder="输入问题，例如：我想申请退款，订单号是 #12345"></textarea>
          <button :disabled="busy || !draft.trim()">{{ chatBusy ? '发送中' : '发送' }}</button>
        </form>
      </section>

      <section
        v-show="activeWorkspace === 'knowledge' || activeWorkspace === 'handoffs'"
        :class="['tools-grid', `workspace-${activeWorkspace}`]"
      >
        <article v-show="activeWorkspace === 'knowledge'" class="tool-panel">
          <div class="panel-heading">
            <h2>知识库检索</h2>
            <span class="pill soft">RAG</span>
          </div>
          <div class="inline-form">
            <input v-model="searchQuery" aria-label="知识库检索词" placeholder="退款多久能到账" />
            <button @click="searchKnowledge" :disabled="busy || !searchQuery.trim()">检索</button>
          </div>
          <div class="result-list">
            <article v-for="item in searchResults" :key="item.id || item.title" class="result-item">
              <strong>{{ item.title || '未命名结果' }}</strong>
              <span>score {{ item.score ?? '-' }}</span>
              <p>{{ item.content }}</p>
            </article>
          </div>
        </article>

        <article v-show="activeWorkspace === 'knowledge'" class="tool-panel">
          <div class="panel-heading">
            <h2>导入知识</h2>
            <span class="pill soft">Docs</span>
          </div>
          <label>
            <span>标题</span>
            <input v-model="docTitle" placeholder="退款补充政策" />
          </label>
          <label>
            <span>内容</span>
            <textarea v-model="docContent" rows="5" placeholder="输入知识库内容"></textarea>
          </label>
          <div class="actions">
            <button @click="submitKnowledge" :disabled="busy || !docTitle.trim() || !docContent.trim()">添加文档</button>
            <label class="file-button">
              上传文件
              <input type="file" accept=".txt,.md,.json" :disabled="busy" @change="handleUpload" />
            </label>
          </div>
        </article>

        <article v-show="activeWorkspace === 'handoffs'" class="tool-panel handoff-panel">
          <div class="panel-heading">
            <h2>人工工单中心</h2>
            <div class="panel-actions">
              <span class="pill soft">{{ handoffs.length }}</span>
              <button @click="loadHandoffs" :disabled="busy">刷新</button>
            </div>
          </div>
          <p class="panel-tip">高风险对话会自动创建并持久化到 SakikoMind 内置工单库；已解决和已关闭工单会从当前待办中自动移除。</p>
          <div v-if="handoffs.length" class="handoff-list">
            <article v-for="ticket in handoffs" :key="ticket.ticket_id" class="handoff-row">
              <div class="handoff-row-heading">
                <strong>{{ ticket.reason_label }}</strong>
                <span :class="['priority', ticket.priority.toLowerCase()]">{{ ticket.priority }}</span>
              </div>
              <p>{{ ticket.summary }}</p>
              <small>
                {{ ticket.ticket_id }}
                <template v-if="ticket.trace_id"> · Trace {{ ticket.trace_id }}</template>
                · {{ ticket.created_at }}
              </small>
              <label>
                <span>处理状态</span>
                <select
                  :value="ticket.status"
                  :disabled="busy"
                  @change="changeHandoffStatus(ticket, $event)"
                >
                  <option value="open">待处理</option>
                  <option value="in_progress">处理中</option>
                  <option value="resolved">已解决</option>
                  <option value="closed">已关闭</option>
                </select>
              </label>
            </article>
          </div>
          <p v-else class="panel-tip">暂无人工工单。发送高风险问题后会自动出现在这里。</p>
        </article>
      </section>

      <section v-show="activeWorkspace === 'monitor'" class="monitor-workspace">
        <section class="metric-grid">
          <article class="metric-card">
            <span>服务健康</span>
            <strong :class="healthOk ? 'ok' : 'muted'">{{ healthLabel }}</strong>
            <small>FastAPI /health</small>
          </article>
          <article class="metric-card">
            <span>知识片段</span>
            <strong>{{ knowledgeCount }}</strong>
            <small>ChromaDB RAG 文档</small>
          </article>
          <article class="metric-card">
            <span>待处理工单</span>
            <strong>{{ handoffs.length }}</strong>
            <small>open + in_progress</small>
          </article>
          <article class="metric-card">
            <span>活动告警</span>
            <strong :class="activeAlerts.length ? 'alert-count' : 'ok'">{{ activeAlerts.length }}</strong>
            <small>由 /monitor 提供</small>
          </article>
        </section>

        <section class="monitor-grid">
          <article class="tool-panel">
            <div class="panel-heading">
              <h2>Agent 运行状态</h2>
              <button @click="refreshWorkspace" :disabled="busy">刷新全局状态</button>
            </div>
            <div class="monitor-list">
              <article v-for="agent in agentStats" :key="agent.name" class="monitor-row">
                <strong>{{ agent.name }}</strong>
                <span>成功率 {{ formatPercent(agent.success_rate) }}</span>
                <span>平均 {{ formatMilliseconds(agent.avg_ms) }}</span>
                <small>路由评分 {{ formatScore(agent.routing_score) }}</small>
              </article>
              <p v-if="!agentStats.length" class="panel-tip">等待第一条对话后显示 Agent 运行数据。</p>
            </div>
          </article>

          <article class="tool-panel">
            <div class="panel-heading">
              <h2>工具与熔断状态</h2>
              <span class="pill soft">{{ toolStats.length }}</span>
            </div>
            <div class="monitor-list">
              <article v-for="tool in toolStats" :key="tool.name" class="monitor-row">
                <strong>{{ tool.name }}</strong>
                <span>成功率 {{ formatPercent(tool.success_rate) }}</span>
                <span>平均 {{ formatMilliseconds(tool.avg_latency_ms) }}</span>
                <small>熔断 {{ tool.circuit_state || 'closed' }} · 连续失败 {{ tool.consecutive_fails ?? 0 }}</small>
              </article>
              <p v-if="!toolStats.length" class="panel-tip">当前没有工具运行记录。</p>
            </div>
          </article>

          <article class="tool-panel">
            <div class="panel-heading">
              <h2>告警与恢复</h2>
              <span :class="['pill', activeAlerts.length ? '' : 'soft']">{{ activeAlerts.length }} 活动</span>
            </div>
            <div class="monitor-list">
              <article v-for="alert in recentAlerts" :key="`${alert.metric}-${alert.ts}`" class="alert-row">
                <strong>{{ alert.resolved ? '已恢复' : alert.severity }}</strong>
                <span>{{ alert.message }}</span>
                <small>{{ alert.resolved_at || alert.ts }}</small>
              </article>
              <p v-if="!recentAlerts.length" class="panel-tip">暂无告警，系统运行正常。</p>
            </div>
          </article>

          <article class="tool-panel">
            <div class="panel-heading">
              <h2>优化建议</h2>
              <a :href="metricsUrl" target="_blank" rel="noreferrer">打开 Prometheus 指标</a>
            </div>
            <div class="monitor-list">
              <article v-for="suggestion in monitorSuggestions" :key="suggestion.title" class="suggestion-row">
                <strong>P{{ suggestion.priority }} · {{ suggestion.title }}</strong>
                <span>{{ suggestion.action }}</span>
              </article>
              <p v-if="!monitorSuggestions.length" class="panel-tip">暂无优化建议。</p>
            </div>
          </article>
        </section>
      </section>

      <section v-show="activeWorkspace === 'settings'" class="settings-workspace">
        <article class="tool-panel service-settings-panel">
          <div class="panel-heading">
            <div>
              <span class="eyebrow">Connection</span>
              <h2>服务配置</h2>
            </div>
            <span class="pill">Python FastAPI</span>
          </div>
          <p class="panel-tip">配置本次演示连接、用户与会话标识。修改后会自动保存在当前浏览器。</p>
          <label>
            <span>API 地址</span>
            <input v-model="settings.apiBaseUrl" :disabled="busy" @change="persist" placeholder="http://localhost:8000" />
          </label>
          <label>
            <span>用户 ID</span>
            <input v-model="settings.userId" :disabled="busy" @change="persist" placeholder="u1001" />
          </label>
          <label>
            <span>会话 ID</span>
            <input v-model="settings.conversationId" :disabled="busy" @change="persist" placeholder="自动生成" />
          </label>
          <div class="actions">
            <button @click="checkHealth">健康检查</button>
            <button @click="loadStats">刷新状态</button>
          </div>
        </article>

        <article class="tool-panel status-panel">
          <div class="panel-heading">
            <div>
              <span class="eyebrow">Runtime</span>
              <h2>服务状态</h2>
            </div>
            <span :class="['status-dot', healthOk ? 'online' : 'offline']"></span>
          </div>
          <dl>
            <div>
              <dt>服务端</dt>
              <dd>Python FastAPI</dd>
            </div>
            <div>
              <dt>健康状态</dt>
              <dd :class="healthOk ? 'ok' : 'muted'">{{ healthLabel }}</dd>
            </div>
            <div>
              <dt>知识片段</dt>
              <dd>{{ knowledgeCount }}</dd>
            </div>
            <div>
              <dt>业务 Skills</dt>
              <dd>{{ skills.length }}</dd>
            </div>
          </dl>
        </article>

        <article class="tool-panel skills-panel settings-skills-panel">
          <div class="panel-heading">
            <div>
              <span class="eyebrow">Governance</span>
              <h2>业务 Skills</h2>
            </div>
            <div class="panel-actions">
              <span class="pill soft">{{ skills.length }}</span>
              <button @click="refreshSkills" :disabled="busy">热更新</button>
            </div>
          </div>
          <p>集中查看运行时加载的业务规范、版本、负责人和适用 Agent。</p>
          <div class="skill-list">
            <article v-for="skill in skills" :key="skill.path" class="skill-item">
              <strong>{{ skill.name }}</strong>
              <span>{{ skill.version }} · {{ skill.owner || '未指定负责人' }}</span>
              <small>{{ skill.agents.join(', ') || 'all' }} · {{ skill.keywords.slice(0, 4).join('、') }}</small>
            </article>
          </div>
        </article>
      </section>
    </section>

    <footer class="site-footer">
      <span>网站贡献者</span>
      <a href="https://space.bilibili.com/288645075" target="_blank" rel="noreferrer">@君士坦丁堡堡</a>
      <small>感谢参与 SakikoMind 的设计、测试与持续完善</small>
    </footer>
  </main>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  addKnowledge,
  backendMeta,
  createInitialSettings,
  requestChat,
  requestHealth,
  requestHandoffs,
  requestKnowledgeStats,
  requestMonitor,
  requestSearch,
  requestSkills,
  reloadSkills,
  saveSettings,
  updateHandoffStatus,
  uploadKnowledge
} from './lib/backends'

const settings = reactive(createInitialSettings())
const messages = ref([])
const draft = ref('')
const operationBusy = ref(false)
const chatBusy = ref(false)
const busy = computed(() => operationBusy.value || chatBusy.value)
let chatGeneration = 0
let chatController = null
const healthOk = ref(false)
const healthLabel = ref('未检查')
const statusText = ref('')
const statsError = ref('')
const knowledgeCount = ref('-')
const skills = ref([])
const searchQuery = ref('退款多久能到账')
const searchResults = ref([])
const docTitle = ref('退款补充政策')
const docContent = ref('大促期间退款审核时间可能延长到 3-5 个工作日。')
const handoffs = ref([])
const messageList = ref(null)
const activeHandoffStatuses = new Set(['open', 'in_progress'])
const activeWorkspace = ref('chat')
const monitorData = ref({
  agent_stats: {},
  tool_stats: {},
  active_alerts: [],
  recent_alerts: [],
  suggestions: []
})
const workspaceTabs = [
  { id: 'chat', label: '对话' },
  { id: 'knowledge', label: '知识库' },
  { id: 'handoffs', label: '工单' },
  { id: 'monitor', label: '监控' },
  { id: 'settings', label: '设置' }
]
const demoScenarios = [
  {
    id: 'refund',
    title: '退款政策',
    prompt: '首次购买专业版后多久可以申请退款？'
  },
  {
    id: 'technical',
    title: '401 排障',
    prompt: '登录接口返回 401，应该如何排查？'
  },
  {
    id: 'handoff',
    title: '高风险转人工',
    prompt: '登录时出现陌生设备提醒，而且本月银行卡被重复扣款，请转人工处理。'
  }
]

const currentBackend = computed(() => backendMeta(settings))
const docsUrl = computed(() => `${currentBackend.value.baseUrl}/docs`)
const metricsUrl = computed(() => `${currentBackend.value.baseUrl}/metrics`)
const workspacePresentation = computed(() => ({
  chat: {
    title: '智能客服工作台',
    description: '对话、引用、规则与升级结果一处查看'
  },
  knowledge: {
    title: '知识库管理',
    description: '检索和维护订阅制 SaaS 业务政策'
  },
  handoffs: {
    title: '人工工单中心',
    description: '跟进高风险请求与人工处理状态'
  },
  monitor: {
    title: '运行监控中心',
    description: '观察 Agent、工具、告警与优化建议'
  },
  settings: {
    title: '系统设置与 Skill 治理',
    description: '集中管理服务连接、运行状态与业务规范'
  }
}[activeWorkspace.value]))
const agentStats = computed(() => toMonitorEntries(monitorData.value.agent_stats))
const toolStats = computed(() => toMonitorEntries(monitorData.value.tool_stats))
const activeAlerts = computed(() => monitorData.value.active_alerts || [])
const recentAlerts = computed(() => monitorData.value.recent_alerts || [])
const monitorSuggestions = computed(() => monitorData.value.suggestions || [])

watch(
  () => settings.conversationId,
  () => persist()
)

onMounted(() => {
  refreshWorkspace()
})

onBeforeUnmount(() => chatController?.abort())

function persist() {
  saveSettings(settings)
}

async function sendMessage() {
  const content = draft.value.trim()
  if (!content || busy.value) return
  const generation = ++chatGeneration
  const controller = new AbortController()
  chatController = controller
  const requestSettings = { ...settings }
  messages.value.push({ id: createClientId(), role: 'user', content })
  draft.value = ''
  chatBusy.value = true
  try {
    const response = await requestChat(requestSettings, content, { signal: controller.signal })
    if (generation !== chatGeneration) return
    if (response.conversationId && !settings.conversationId) {
      settings.conversationId = response.conversationId
      persist()
    }
    const meta = [
      response.intent,
      response.agentType,
      response.knowledgeUsed ? 'RAG' : '',
      response.escalated ? '转人工' : ''
    ].filter(Boolean).join(' · ')
    messages.value.push({
      id: createClientId(),
      role: 'assistant',
      content: response.response,
      meta,
      traceId: response.traceId,
      latencyMs: response.latencyMs,
      citations: response.citations,
      skills: response.skillsUsed,
      handoffTicket: response.handoffTicket
    })
    if (response.handoffTicket) await loadHandoffs()
  } catch (error) {
    if (generation !== chatGeneration) return
    messages.value.push({
      id: createClientId(),
      role: 'assistant',
      content: error.message,
      meta: '请求失败'
    })
  } finally {
    if (generation === chatGeneration) {
      chatController = null
      chatBusy.value = false
      await nextTick()
      messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'smooth' })
    }
  }
}

function createClientId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `message-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

async function checkHealth() {
  try {
    const data = await requestHealth(settings)
    healthOk.value = data.status === 'ok'
    healthLabel.value = data.status || 'ok'
    statusText.value = '健康检查已完成'
  } catch (error) {
    healthOk.value = false
    healthLabel.value = '不可用'
    statusText.value = error.message
  }
}

async function loadStats() {
  const [stats, monitor] = await Promise.allSettled([
    requestKnowledgeStats(settings),
    requestMonitor(settings)
  ])
  const failures = []
  if (stats.status === 'fulfilled') {
    knowledgeCount.value = stats.value.total_chunks ?? stats.value.totalChunks ?? '-'
  } else {
    failures.push(`知识统计刷新失败：${stats.reason.message}`)
  }
  if (monitor.status === 'fulfilled') {
    monitorData.value = monitor.value
  } else {
    failures.push(`监控刷新失败：${monitor.reason.message}`)
  }
  statsError.value = failures.length ? `${failures.join('；')}。失败部分保留上次数据，可能已过期。` : ''
}

async function loadSkills() {
  try {
    const data = await requestSkills(settings)
    skills.value = data.skills || []
  } catch (error) {
    statusText.value = error.message
  }
}

async function loadHandoffs() {
  try {
    const data = await requestHandoffs(settings)
    handoffs.value = (data.items || []).filter((ticket) => activeHandoffStatuses.has(ticket.status))
  } catch (error) {
    statusText.value = error.message
  }
}

async function changeHandoffStatus(ticket, event) {
  if (busy.value) {
    event.target.value = ticket.status
    return
  }
  const status = event.target.value
  const previousStatus = ticket.status
  operationBusy.value = true
  try {
    const updated = await updateHandoffStatus(settings, ticket.ticket_id, status)
    handoffs.value = handoffs.value
      .map((item) => (item.ticket_id === updated.ticket_id ? updated : item))
      .filter((item) => activeHandoffStatuses.has(item.status))
    statusText.value = `工单 ${updated.ticket_id} 已更新为 ${updated.status}`
  } catch (error) {
    statusText.value = error.message
    event.target.value = previousStatus
    await loadHandoffs()
  } finally {
    operationBusy.value = false
  }
}

async function refreshSkills() {
  if (busy.value) return
  operationBusy.value = true
  try {
    const data = await reloadSkills(settings)
    skills.value = data.skills || []
    statusText.value = `已热更新 ${data.count || 0} 个 Skills`
  } catch (error) {
    statusText.value = error.message
  } finally {
    operationBusy.value = false
  }
}

async function searchKnowledge() {
  if (busy.value || !searchQuery.value.trim()) return
  operationBusy.value = true
  try {
    const data = await requestSearch(settings, searchQuery.value.trim(), 5)
    searchResults.value = data.results || []
    statusText.value = `检索完成，共 ${searchResults.value.length} 条结果`
  } catch (error) {
    searchResults.value = []
    statusText.value = error.message
  } finally {
    operationBusy.value = false
  }
}

async function submitKnowledge() {
  if (busy.value || !docTitle.value.trim() || !docContent.value.trim()) return
  operationBusy.value = true
  try {
    const data = await addKnowledge(settings, [
      { title: docTitle.value.trim(), content: docContent.value.trim() }
    ])
    await loadStats()
    const added = data.added_chunks ?? data.added ?? data.count
    statusText.value = added == null ? '知识导入完成' : `已导入 ${added} 条知识`
  } catch (error) {
    statusText.value = error.message
  } finally {
    operationBusy.value = false
  }
}

async function handleUpload(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || busy.value) return
  operationBusy.value = true
  try {
    const data = await uploadKnowledge(settings, file)
    await loadStats()
    const added = data.added_chunks ?? data.added ?? data.count
    statusText.value = added == null ? '文件导入完成' : `文件导入完成：${added} 条知识`
  } catch (error) {
    statusText.value = error.message
  } finally {
    operationBusy.value = false
  }
}

async function refreshWorkspace() {
  await Promise.all([
    checkHealth(),
    loadStats(),
    loadSkills(),
    loadHandoffs()
  ])
}

function applyDemo(scenario) {
  activeWorkspace.value = 'chat'
  draft.value = scenario.prompt
  statusText.value = `已填入演示场景：${scenario.title}`
}

function startNewConversation() {
  if (operationBusy.value) return
  chatGeneration += 1
  chatController?.abort()
  chatController = null
  chatBusy.value = false
  messages.value = []
  draft.value = ''
  settings.conversationId = ''
  persist()
  statusText.value = '已创建新会话'
}

function toMonitorEntries(stats) {
  return Object.entries(stats || {}).map(([name, value]) => ({ name, ...value }))
}

function formatPercent(value) {
  return `${Math.round(Number(value ?? 0) * 100)}%`
}

function formatMilliseconds(value) {
  return `${Math.round(Number(value ?? 0))} ms`
}

function formatScore(value) {
  return Number(value ?? 0).toFixed(2)
}
</script>
