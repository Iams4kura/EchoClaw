import { reactive } from 'vue'
import { appStore } from './app.js'

const HISTORY_KEY = () => `claw_history_${appStore.userId}`

function loadHistory() {
  try {
    const data = localStorage.getItem(HISTORY_KEY())
    return data ? JSON.parse(data) : []
  } catch { return [] }
}

export const chatStore = reactive({
  messages: loadHistory(),
  thinking: null,
  pendingQuestion: null,
  sending: false,

  addMessage(role, text) {
    this.messages.push({ role, text, time: Date.now() })
    this.saveHistory()
  },

  saveHistory() {
    try {
      localStorage.setItem(HISTORY_KEY(), JSON.stringify(this.messages.slice(-200)))
    } catch { /* quota */ }
  },

  clearHistory() {
    this.messages = []
    localStorage.removeItem(HISTORY_KEY())
  },

  reloadHistory() {
    this.messages = loadHistory()
  }
})
