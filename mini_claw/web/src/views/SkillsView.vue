<template>
  <div class="page">
    <h1>技能</h1>
    <input class="search" v-model="search" placeholder="搜索技能...">
    <template v-for="group in visibleGroups" :key="group.key">
      <h2>{{ group.label }} <span class="count">{{ group.items.length }}</span></h2>
      <div class="skill-grid">
        <div v-for="skill in group.items" :key="skill.name" class="skill-card">
          <div class="skill-name">/{{ skill.name }}</div>
          <div class="skill-desc">{{ skill.desc || '无描述' }}</div>
        </div>
      </div>
    </template>
    <p v-if="!totalCount && !loading" class="hint">{{ search ? '无匹配结果' : '暂无技能' }}</p>
    <div v-if="loading" class="hint">加载中...</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api.js'

const skillGroups = ref({})
const search = ref('')
const loading = ref(true)

const groupMeta = [
  { key: 'mclaw_builtin', label: 'MClaw 内置技能' },
  { key: 'mclaude', label: 'MClaude 引擎技能' },
  { key: 'mclaw_custom', label: 'MClaw 自定义技能' },
]

const visibleGroups = computed(() => {
  const q = search.value.toLowerCase()
  return groupMeta
    .map(g => ({
      ...g,
      items: (skillGroups.value[g.key] || []).filter(s =>
        s.name.includes(q) || (s.desc || '').includes(q)
      ),
    }))
    .filter(g => g.items.length > 0)
})

const totalCount = computed(() => visibleGroups.value.reduce((n, g) => n + g.items.length, 0))

onMounted(async () => {
  try {
    const data = await api.get('/skills')
    // 兼容旧版扁平数组格式
    if (Array.isArray(data)) {
      skillGroups.value = { mclaw_builtin: data }
    } else {
      skillGroups.value = data
    }
  } catch {}
  loading.value = false
})
</script>

<style scoped>
.page { padding: 24px; overflow-y: auto; height: 100%; }
h1 { font-size: 20px; margin-bottom: 16px; color: var(--accent); }
h2 { font-size: 14px; color: var(--text-secondary); margin: 20px 0 10px; display: flex; align-items: center; gap: 8px; }
.count { font-size: 12px; color: var(--text-muted); font-weight: 400; }
.hint { color: var(--text-muted); font-size: 13px; margin-top: 12px; }

.search {
  width: 100%;
  max-width: 400px;
  padding: 8px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-input);
  outline: none;
  margin-bottom: 10px;
}

.search:focus { border-color: var(--accent); }

.skill-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
}

.skill-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 16px;
}

.skill-name { font-weight: 600; color: var(--accent); margin-bottom: 6px; }
.skill-desc { font-size: 13px; color: var(--text-secondary); line-height: 1.4; }
</style>
