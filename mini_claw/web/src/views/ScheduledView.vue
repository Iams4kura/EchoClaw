<template>
  <div class="page">
    <h1>定时任务</h1>
    <p class="hint" v-if="!routines.length && !loading">暂无定时任务，任务定义在 HEARTBEAT.md 中。</p>
    <div v-if="loading" class="hint">加载中...</div>
    <div class="routine-list">
      <div v-for="r in routines" :key="r.id" class="routine-card">
        <div class="routine-header">
          <span class="routine-name">{{ r.name }}</span>
          <span class="routine-freq">{{ r.frequency }}</span>
        </div>
        <div class="routine-desc">{{ r.content }}</div>
        <div class="routine-footer">
          <span v-if="r.condition" class="routine-cond">条件: {{ r.condition }}</span>
          <button class="trigger-btn" @click="trigger(r.id)"
            :disabled="!!triggering[r.id]">
            {{ typeof triggering[r.id] === 'string' ? triggering[r.id] : (triggering[r.id] ? '执行中...' : '手动触发') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
import { appStore } from '../stores/app.js'

const routines = ref([])
const loading = ref(true)

onMounted(async () => {
  try {
    routines.value = await api.get('/api/routines')
  } catch { /* backend may not have this endpoint yet */ }
  loading.value = false
})

const triggering = ref({})

async function trigger(id) {
  if (triggering.value[id]) return
  triggering.value[id] = true
  try {
    const j = await api.post(`/api/routines/${id}/trigger`, { user_id: appStore.userId, content: '' })
    if (!j.ok) {
      triggering.value[id] = '失败: ' + (j.error || '未知错误')
    } else {
      triggering.value[id] = '已触发'
    }
  } catch (e) {
    triggering.value[id] = '失败: ' + e.message
  }
  setTimeout(() => { triggering.value[id] = false }, 3000)
}
</script>

<style scoped>
.page { padding: 24px; overflow-y: auto; height: 100%; }
h1 { font-size: 20px; margin-bottom: 20px; color: var(--accent); }
.hint { color: var(--text-muted); font-size: 13px; }

.routine-list { display: flex; flex-direction: column; gap: 12px; }

.routine-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.routine-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.routine-name { font-weight: 600; font-size: 15px; }
.routine-freq { font-size: 12px; color: var(--accent); background: var(--bg-tertiary); padding: 2px 8px; border-radius: 4px; }
.routine-desc { font-size: 13px; color: var(--text-secondary); margin-bottom: 10px; line-height: 1.5; }

.routine-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 12px;
}

.routine-cond { font-size: 11px; color: var(--text-muted); margin-right: auto; }

.trigger-btn {
  padding: 4px 12px;
  background: var(--bg-tertiary);
  border: 1px solid var(--accent);
  border-radius: 4px;
  color: var(--accent);
  font-size: 12px;
  cursor: pointer;
}

.trigger-btn:hover { background: var(--accent); color: #fff; }
</style>
