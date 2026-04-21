<template>
  <div class="chat-page">
    <div class="chat-header">
      <div class="status-info">
        <span>会话: {{ status?.active_sessions ?? '-' }}</span>
        <span>运行: {{ uptimeText }}</span>
      </div>
    </div>
    <div class="chat-messages" ref="chatEl">
      <template v-for="(msg, i) in chatStore.messages" :key="i">
        <div v-if="showTimeSep(i)" class="time-sep">{{ formatTimeSep(msg.time) }}</div>
        <div class="msg" :class="msg.role">
          <div class="msg-content" v-if="msg.role === 'bot'" v-html="renderMarkdown(msg.text)"></div>
          <div class="msg-content" v-else>{{ msg.text }}</div>
          <div class="meta">
            {{ formatTime(msg.time) }}
            <span v-if="msg.duration" class="dur">{{ (msg.duration / 1000).toFixed(1) }}s</span>
          </div>
        </div>
      </template>
      <!-- Thinking panel -->
      <div v-if="chatStore.thinking" class="thinking-panel">
        <div class="tp-header"><span class="spinner"></span>思考中...</div>
        <div class="tp-steps">
          <div v-for="(step, i) in thinkingSteps" :key="i" class="tp-step">
            <span class="dot" :class="step.status"></span>
            <span>{{ step.detail }}</span>
          </div>
        </div>
      </div>
      <!-- AskUser card -->
      <div v-if="chatStore.pendingQuestion" class="ask-card">
        <div class="ask-q">{{ chatStore.pendingQuestion.question }}</div>
        <div v-if="chatStore.pendingQuestion.options?.length" class="ask-opts">
          <button v-for="(opt, i) in chatStore.pendingQuestion.options" :key="i"
            class="ask-opt" @click="submitAnswer(opt)">
            {{ i + 1 }}. {{ opt }}
          </button>
        </div>
        <div class="ask-input-row">
          <input class="ask-input" v-model="askInput" placeholder="输入回答..."
            @keydown.enter.prevent="submitAnswer(askInput)">
          <button class="ask-submit" @click="submitAnswer(askInput)">发送</button>
        </div>
      </div>
      <!-- Queued hint -->
      <div v-if="queued" class="queued-hint">已排队，等待当前任务完成...</div>
    </div>
    <div class="input-area">
      <div class="input-wrap">
        <div v-if="showCmdPanel" class="cmd-panel">
          <div v-for="(cmd, i) in filteredCmds" :key="cmd.name"
            class="cmd-item" :class="{ active: i === cmdIdx }"
            @mouseenter="cmdIdx = i" @click="pickCmd()">
            <span class="cmd-name">{{ cmd.name }}</span>
            <span class="cmd-desc">{{ cmd.desc }}</span>
          </div>
        </div>
        <textarea ref="inputEl" v-model="inputText" rows="1"
          placeholder="输入消息，/ 查看命令..."
          @keydown="handleKeydown" @input="handleInput"></textarea>
      </div>
      <button class="send-btn" @click="send" :disabled="sending">发送</button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { api } from '../api.js'
import { appStore } from '../stores/app.js'
import { chatStore } from '../stores/chat.js'

const chatEl = ref(null)
const inputEl = ref(null)
const inputText = ref('')
const askInput = ref('')
const sending = ref(false)
const queued = ref(false)
const status = ref(null)
const thinkingSteps = ref([])
const showCmdPanel = ref(false)
const cmdIdx = ref(0)
const commands = ref([
  { name: '/help', desc: '显示可用命令' },
  { name: '/clear', desc: '清空聊天显示' },
  { name: '/reset', desc: '重置当前会话' },
  { name: '/status', desc: '查看服务状态' },
])

const filteredCmds = computed(() => {
  const q = inputText.value.toLowerCase()
  return commands.value.filter(c => c.name.startsWith(q))
})

const uptimeText = computed(() => {
  if (!status.value) return '-'
  return Math.round(status.value.uptime_seconds) + 's'
})

// Load skills
onMounted(async () => {
  try {
    const data = await api.get('/skills')
    // 新版分组格式: { mclaw_builtin: [...], mclaude: [...], mclaw_custom: [...] }
    const groups = Array.isArray(data) ? { mclaw_builtin: data } : data
    for (const list of Object.values(groups)) {
      if (Array.isArray(list)) {
        list.forEach(s => {
          commands.value.push({ name: '/' + s.name, desc: s.desc || 'skill', isSkill: true })
        })
      }
    }
  } catch {}

  refreshStatus()
  statusTimer = setInterval(refreshStatus, 5000)
  questionTimer = setInterval(pollPendingQuestion, 2000)
  notifTimer = setInterval(pollNotifications, 5000)

  // First boot check — 后端已通过 startup greeting 处理，前端不再重复触发
  // 只需等待后端推送的通知即可

  scrollToBottom()
})

let statusTimer, questionTimer, notifTimer, thinkingTimer

onUnmounted(() => {
  clearInterval(statusTimer)
  clearInterval(questionTimer)
  clearInterval(notifTimer)
  clearInterval(thinkingTimer)
})

async function refreshStatus() {
  try { status.value = await api.get('/status') } catch {}
}

async function pollPendingQuestion() {
  try {
    const j = await api.get(`/pending_question/${appStore.userId}`)
    chatStore.pendingQuestion = j.pending ? j : null
  } catch {}
}

async function pollNotifications() {
  try {
    const j = await api.get(`/notifications/${appStore.userId}`)
    if (j.notifications?.length) {
      j.notifications.forEach(n => {
        chatStore.addMessage('bot', n.text)
      })
      scrollToBottom()
    }
  } catch {}
}

function startThinkingPoll() {
  chatStore.thinking = true
  thinkingTimer = setInterval(async () => {
    try {
      const j = await api.get(`/thinking/${appStore.userId}`)
      if (j.thinking) thinkingSteps.value = j.steps || []
    } catch {}
  }, 1500)
}

function stopThinkingPoll() {
  chatStore.thinking = false
  thinkingSteps.value = []
  clearInterval(thinkingTimer)
  thinkingTimer = null
}

async function send() {
  const text = inputText.value.trim()
  if (!text) return
  inputText.value = ''
  await sendMessage(text)
}

async function sendMessage(text) {
  chatStore.addMessage('user', text)
  scrollToBottom()

  if (sending.value && !text.startsWith('/btw ')) {
    queued.value = true
    try {
      const j = await api.post('/message', { user_id: appStore.userId, content: text })
      chatStore.addMessage('bot', j.text)
      chatStore.messages[chatStore.messages.length - 1].duration = j.duration_ms
    } catch (e) {
      chatStore.addMessage('bot', '请求失败: ' + e.message)
    }
    queued.value = false
    scrollToBottom()
    refreshStatus()
    return
  }

  sending.value = true
  startThinkingPoll()
  scrollToBottom()

  try {
    const j = await api.post('/message', { user_id: appStore.userId, content: text })
    stopThinkingPoll()
    if (j.text !== '[interrupted]') {
      chatStore.addMessage('bot', j.text)
      chatStore.messages[chatStore.messages.length - 1].duration = j.duration_ms
    }
  } catch (e) {
    stopThinkingPoll()
    chatStore.addMessage('bot', '请求失败: ' + e.message)
  }
  sending.value = false
  scrollToBottom()
  refreshStatus()
}

async function submitAnswer(answer) {
  if (!answer?.trim()) return
  askInput.value = ''
  chatStore.pendingQuestion = null
  chatStore.addMessage('user', answer)
  try {
    await api.post(`/answer/${appStore.userId}`, { user_id: appStore.userId, content: answer })
  } catch (e) {
    chatStore.addMessage('bot', '提交回答失败: ' + e.message)
  }
  scrollToBottom()
}

function handleKeydown(e) {
  if (showCmdPanel.value) {
    if (e.key === 'ArrowDown') { e.preventDefault(); cmdIdx.value = Math.min(cmdIdx.value + 1, filteredCmds.value.length - 1) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); cmdIdx.value = Math.max(cmdIdx.value - 1, 0) }
    else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pickCmd() }
    else if (e.key === 'Escape') { e.preventDefault(); showCmdPanel.value = false }
  } else {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }
}

function handleInput() {
  // Auto resize
  const el = inputEl.value
  if (el) { el.style.height = '42px'; el.style.height = el.scrollHeight + 'px' }
  // Command panel
  const v = inputText.value
  if (v.startsWith('/') && !v.includes(' ')) {
    showCmdPanel.value = filteredCmds.value.length > 0
    cmdIdx.value = 0
  } else {
    showCmdPanel.value = false
  }
}

function pickCmd() {
  if (cmdIdx.value < 0 || !filteredCmds.value.length) return
  const cmd = filteredCmds.value[cmdIdx.value]
  inputText.value = ''
  showCmdPanel.value = false
  execCmd(cmd)
}

async function execCmd(cmd) {
  if (cmd.name === '/help') {
    chatStore.addMessage('bot', '可用命令:\n' + commands.value.map(c => c.name + ' — ' + c.desc).join('\n'))
  } else if (cmd.name === '/clear') {
    chatStore.clearHistory()
  } else if (cmd.name === '/reset') {
    if (!confirm('重置会话？')) return
    await api.post(`/reset/${appStore.userId}`, {})
    chatStore.clearHistory()
    chatStore.addMessage('bot', '会话已重置')
  } else if (cmd.name === '/status') {
    await refreshStatus()
    chatStore.addMessage('bot', `活跃会话: ${status.value?.active_sessions}  运行时间: ${uptimeText.value}`)
  } else if (cmd.isSkill) {
    inputText.value = cmd.name
    await send()
  }
  scrollToBottom()
}

function scrollToBottom() {
  nextTick(() => { if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight })
}

function showTimeSep(i) {
  if (i === 0) return false
  const prev = chatStore.messages[i - 1]
  const curr = chatStore.messages[i]
  return curr.time - prev.time >= 3600000
}

function formatTime(ts) {
  const d = new Date(ts)
  return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0')
}

function formatTimeSep(ts) {
  const d = new Date(ts), now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today - 86400000)
  const msgDay = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const hm = formatTime(ts)
  if (msgDay.getTime() === today.getTime()) return hm
  if (msgDay.getTime() === yesterday.getTime()) return '昨天 ' + hm
  return (d.getMonth() + 1) + '月' + d.getDate() + '日 ' + hm
}

function renderMarkdown(text) {
  let s = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  s = s.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>')
  return s
}
</script>

<style scoped>
.chat-page {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.chat-header {
  padding: 8px 20px;
  background: var(--bg-input);
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  color: var(--text-muted);
  display: flex;
  gap: 20px;
}

.status-info {
  display: flex;
  gap: 20px;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.msg {
  max-width: 80%;
  padding: 10px 14px;
  border-radius: 12px;
  line-height: 1.5;
  word-wrap: break-word;
  white-space: pre-wrap;
}

.msg.user {
  align-self: flex-end;
  background: var(--bg-tertiary);
  border-bottom-right-radius: 4px;
}

.msg.bot {
  align-self: flex-start;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-bottom-left-radius: 4px;
}

.msg.bot :deep(code) {
  background: var(--bg-input);
  padding: 2px 5px;
  border-radius: 3px;
  font-size: 13px;
}

.msg.bot :deep(pre) {
  background: var(--bg-input);
  padding: 10px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 6px 0;
}

.msg.bot :deep(pre code) {
  background: none;
  padding: 0;
}

.msg .meta {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
}

.msg .meta .dur {
  margin-left: 8px;
  color: var(--accent);
}

.time-sep {
  text-align: center;
  font-size: 11px;
  color: var(--text-muted);
  margin: 8px 0;
  user-select: none;
}

.thinking-panel {
  align-self: flex-start;
  max-width: 80%;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 12px;
  color: var(--text-secondary);
}

.tp-header {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--accent);
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 6px;
}

.spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid var(--accent);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin .8s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg) } }

.tp-step {
  padding: 2px 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot.running { background: var(--accent); box-shadow: 0 0 4px var(--accent); }
.dot.done { background: var(--success); }
.dot.cancelled { background: var(--text-muted); }

.ask-card {
  align-self: flex-start;
  max-width: 80%;
  background: var(--bg-card);
  border: 1px solid var(--accent);
  border-radius: 12px;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ask-q { font-size: 14px; line-height: 1.5; }
.ask-opts { display: flex; flex-wrap: wrap; gap: 8px; }

.ask-opt {
  padding: 6px 14px;
  background: var(--bg-tertiary);
  border: 1px solid var(--accent);
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}

.ask-opt:hover { background: var(--accent); color: #fff; }

.ask-input-row { display: flex; gap: 8px; }

.ask-input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-input);
  outline: none;
}

.ask-input:focus { border-color: var(--accent); }

.ask-submit {
  padding: 8px 16px;
  background: var(--accent);
  color: #fff;
  border-radius: 6px;
  cursor: pointer;
}

.queued-hint {
  align-self: flex-end;
  color: var(--accent);
  font-size: 12px;
  font-style: italic;
  padding: 4px 10px;
  border: 1px dashed var(--accent);
  border-radius: 8px;
}

.input-area {
  padding: 12px 20px;
  background: var(--bg-secondary);
  border-top: 1px solid var(--border);
  display: flex;
  gap: 10px;
}

.input-wrap {
  flex: 1;
  position: relative;
}

.input-wrap textarea {
  width: 100%;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-input);
  resize: none;
  min-height: 42px;
  max-height: 120px;
  outline: none;
}

.input-wrap textarea:focus { border-color: var(--accent); }

.send-btn {
  padding: 10px 24px;
  background: var(--accent);
  color: #fff;
  border-radius: 8px;
  font-weight: 600;
  white-space: nowrap;
}

.send-btn:hover { background: var(--accent-hover, #c73650); }
.send-btn:disabled { background: #555; cursor: not-allowed; }

.cmd-panel {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  margin-bottom: 6px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 -4px 16px rgba(0, 0, 0, .4);
}

.cmd-item {
  padding: 8px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  font-size: 13px;
}

.cmd-item:hover, .cmd-item.active { background: var(--bg-hover); }
.cmd-name { color: var(--accent); font-weight: 600; min-width: 70px; }
.cmd-desc { color: var(--text-secondary); }
</style>
