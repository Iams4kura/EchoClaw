import { createRouter, createWebHashHistory } from 'vue-router'
import ChatView from './views/ChatView.vue'

const routes = [
  { path: '/', name: 'chat', component: ChatView },
  { path: '/dashboard', name: 'dashboard', component: () => import('./views/DashboardView.vue') },
  { path: '/scheduled', name: 'scheduled', component: () => import('./views/ScheduledView.vue') },
  { path: '/skills', name: 'skills', component: () => import('./views/SkillsView.vue') },
  { path: '/config', name: 'config', component: () => import('./views/ConfigView.vue') },
  { path: '/appearance', name: 'appearance', component: () => import('./views/AppearanceView.vue') },
]

export default createRouter({
  history: createWebHashHistory(),
  routes
})
