import { reactive } from 'vue'

const saved = localStorage.getItem('claw_settings')
const defaults = { userId: 'web_user', theme: 'system', accentColor: '#e94560', fontSize: 14, sidebarPosition: 'left' }
const settings = saved ? { ...defaults, ...JSON.parse(saved) } : defaults

export const appStore = reactive({
  userId: settings.userId,
  theme: settings.theme,
  accentColor: settings.accentColor,
  fontSize: settings.fontSize,
  sidebarPosition: settings.sidebarPosition,

  save() {
    localStorage.setItem('claw_settings', JSON.stringify({
      userId: this.userId,
      theme: this.theme,
      accentColor: this.accentColor,
      fontSize: this.fontSize,
      sidebarPosition: this.sidebarPosition
    }))
  },

  applyTheme() {
    document.documentElement.setAttribute('data-theme', this.theme === 'system'
      ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : this.theme
    )
    document.documentElement.style.setProperty('--accent', this.accentColor)
    document.documentElement.style.setProperty('--font-size', this.fontSize + 'px')
  }
})

appStore.applyTheme()

// 跟随系统主题变化
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (appStore.theme === 'system') appStore.applyTheme()
})
