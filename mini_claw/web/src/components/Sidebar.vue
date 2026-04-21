<template>
  <aside class="sidebar" :class="{ collapsed }">
    <div class="sidebar-header">
      <span class="logo" v-if="!collapsed">Mini Claw</span>
      <span class="logo-icon" v-else>MC</span>
      <button class="toggle-btn" @click="$emit('toggle')">
        <span>{{ collapsed ? '>' : '<' }}</span>
      </button>
    </div>
    <nav class="sidebar-nav">
      <router-link
        v-for="item in navItems"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: $route.path === item.path }"
      >
        <span class="nav-icon">{{ item.icon }}</span>
        <span class="nav-label" v-if="!collapsed">{{ item.label }}</span>
      </router-link>
    </nav>
    <div class="sidebar-footer" v-if="!collapsed">
      <div class="status-dot" :class="online ? 'online' : 'offline'"></div>
      <span class="status-text">{{ online ? 'Online' : 'Offline' }}</span>
    </div>
  </aside>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '../api.js'

defineProps({ collapsed: Boolean })
defineEmits(['toggle'])

const online = ref(false)

const navItems = [
  { path: '/', icon: '\u{1F4AC}', label: '\u804A\u5929' },
  { path: '/dashboard', icon: '\u{1F4CA}', label: '\u4EEA\u8868\u76D8' },
  { path: '/scheduled', icon: '\u23F0', label: '\u5B9A\u65F6\u4EFB\u52A1' },
  { path: '/skills', icon: '\u{1F527}', label: '\u6280\u80FD' },
  { path: '/config', icon: '\u2699\uFE0F', label: '\u914D\u7F6E' },
  { path: '/appearance', icon: '\u{1F3A8}', label: '\u5916\u89C2' },
]

let healthInterval = null

async function checkHealth() {
  try {
    await api.get('/health')
    online.value = true
  } catch {
    online.value = false
  }
}

onMounted(() => {
  checkHealth()
  healthInterval = setInterval(checkHealth, 10000)
})

onUnmounted(() => {
  clearInterval(healthInterval)
})
</script>

<style scoped>
.sidebar {
  position: fixed;
  left: 0;
  top: 0;
  bottom: 0;
  width: var(--sidebar-width);
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  transition: width var(--transition);
  z-index: 100;
}

.sidebar.collapsed {
  width: var(--sidebar-collapsed);
}

.sidebar-header {
  height: var(--header-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  border-bottom: 1px solid var(--border);
}

.logo {
  font-weight: 700;
  font-size: 16px;
  color: var(--accent);
}

.logo-icon {
  font-weight: 700;
  font-size: 14px;
  color: var(--accent);
}

.toggle-btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  color: var(--text-secondary);
  font-size: 12px;
}

.toggle-btn:hover {
  background: var(--bg-hover);
}

.sidebar-nav {
  flex: 1;
  padding: 8px;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: var(--radius);
  color: var(--text-secondary);
  text-decoration: none;
  transition: all var(--transition);
  margin-bottom: 2px;
}

.nav-item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.nav-item.active {
  background: var(--bg-tertiary);
  color: var(--accent);
}

.nav-icon {
  font-size: 18px;
  width: 24px;
  text-align: center;
  flex-shrink: 0;
}

.nav-label {
  white-space: nowrap;
  overflow: hidden;
}

.sidebar-footer {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.status-dot.online { background: var(--success); }
.status-dot.offline { background: var(--error); }

.status-text {
  font-size: 12px;
  color: var(--text-muted);
}
</style>
