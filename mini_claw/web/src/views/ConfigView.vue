<template>
  <div class="page">
    <h1>配置</h1>
    <div v-if="loading" class="hint">加载中...</div>
    <div v-else-if="!config" class="hint">无法加载配置（后端 /api/config 端点可能未就绪）</div>
    <div v-else class="config-sections">
      <div class="section" v-for="(section, key) in config" :key="key">
        <h2>{{ key }}</h2>
        <div class="config-row" v-for="(val, field) in section" :key="field">
          <span class="field-name">{{ field }}</span>
          <span class="field-value">{{ displayValue(val) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const config = ref(null)
const loading = ref(true)

onMounted(async () => {
  try {
    const data = await api.get('/api/config')
    if (data.error) {
      config.value = null
    } else {
      config.value = data
    }
  } catch {}
  loading.value = false
})

function displayValue(val) {
  if (val === null || val === undefined) return '-'
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}
</script>

<style scoped>
.page { padding: 24px; overflow-y: auto; height: 100%; }
h1 { font-size: 20px; margin-bottom: 20px; color: var(--accent); }
h2 { font-size: 15px; margin-bottom: 10px; color: var(--text-primary); text-transform: capitalize; }
.hint { color: var(--text-muted); font-size: 13px; }

.config-sections { display: flex; flex-direction: column; gap: 20px; }

.section {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 16px 20px;
}

.config-row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}

.config-row:last-child { border-bottom: none; }
.field-name { color: var(--text-secondary); }
.field-value { color: var(--text-primary); font-family: monospace; font-size: 12px; }
</style>
