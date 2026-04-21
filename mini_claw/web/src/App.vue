<template>
  <div class="app-layout" :class="{ collapsed: sidebarCollapsed }">
    <Sidebar :collapsed="sidebarCollapsed" @toggle="sidebarCollapsed = !sidebarCollapsed" />
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import Sidebar from './components/Sidebar.vue'

const sidebarCollapsed = ref(false)
</script>

<style scoped>
.app-layout {
  display: flex;
  height: 100%;
  width: 100%;
}

.main-content {
  flex: 1;
  margin-left: var(--sidebar-width);
  height: 100%;
  overflow: hidden;
  transition: margin-left var(--transition);
}

.app-layout.collapsed .main-content {
  margin-left: var(--sidebar-collapsed);
}
</style>
