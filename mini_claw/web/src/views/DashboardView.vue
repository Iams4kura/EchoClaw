<template>
  <div class="page dashboard">
    <h1>仪表盘</h1>
    <div class="cards">
      <div class="card">
        <div class="card-label">活跃会话</div>
        <div class="card-value">{{ status?.active_sessions ?? '-' }}</div>
      </div>
      <div class="card">
        <div class="card-label">运行时间</div>
        <div class="card-value">{{ uptimeText }}</div>
      </div>
      <div class="card">
        <div class="card-label">今日消息</div>
        <div class="card-value">{{ todayMsgCount }}</div>
      </div>
      <div class="card">
        <div class="card-label">系统状态</div>
        <div class="card-value" :class="healthy ? 'ok' : 'err'">{{ healthy ? 'Healthy' : 'Error' }}</div>
      </div>
    </div>
    <div class="section">
      <h2>系统信息</h2>
      <div class="info-row"><span>首次启动</span><span>{{ status?.first_boot ? 'Yes' : 'No' }}</span></div>
      <div class="info-row"><span>运行秒数</span><span>{{ status?.uptime_seconds?.toFixed(0) ?? '-' }}</span></div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '../api.js'
import { chatStore } from '../stores/chat.js'

const status = ref(null)
const healthy = ref(true)
let timer

const uptimeText = computed(() => {
  if (!status.value) return '-'
  const s = Math.round(status.value.uptime_seconds)
  if (s < 60) return s + 's'
  if (s < 3600) return Math.floor(s / 60) + 'm'
  return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm'
})

const todayMsgCount = computed(() => {
  const start = new Date(); start.setHours(0, 0, 0, 0)
  return chatStore.messages.filter(m => m.time >= start.getTime()).length
})

async function refresh() {
  try {
    status.value = await api.get('/status')
    await api.get('/health')
    healthy.value = true
  } catch {
    healthy.value = false
  }
}

onMounted(() => { refresh(); timer = setInterval(refresh, 10000) })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.page { padding: 24px; overflow-y: auto; height: 100%; }
h1 { font-size: 20px; margin-bottom: 20px; color: var(--accent); }
h2 { font-size: 16px; margin-bottom: 12px; color: var(--text-primary); }

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 32px;
}

.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 20px;
}

.card-label { font-size: 12px; color: var(--text-muted); margin-bottom: 8px; }
.card-value { font-size: 24px; font-weight: 700; }
.card-value.ok { color: var(--success); }
.card-value.err { color: var(--error); }

.section {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 20px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}

.info-row:last-child { border-bottom: none; }
</style>
