<template>
  <div class="page">
    <h1>外观设置</h1>

    <div class="section">
      <h2>主题</h2>
      <div class="option-group">
        <button v-for="t in themes" :key="t.value" class="opt-btn"
          :class="{ active: settings.theme === t.value }"
          @click="setTheme(t.value)">{{ t.label }}</button>
      </div>
    </div>

    <div class="section">
      <h2>强调色</h2>
      <div class="color-group">
        <button v-for="c in colors" :key="c" class="color-btn"
          :style="{ background: c }"
          :class="{ active: settings.accentColor === c }"
          @click="setAccent(c)"></button>
      </div>
    </div>

    <div class="section">
      <h2>字体大小</h2>
      <div class="slider-row">
        <input type="range" min="12" max="18" v-model.number="settings.fontSize"
          @input="applyFont">
        <span>{{ settings.fontSize }}px</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { appStore } from '../stores/app.js'
import { useTheme } from '../composables/useTheme.js'

const settings = useTheme()

const themes = [
  { value: 'dark', label: '暗色' },
  { value: 'light', label: '亮色' },
  { value: 'system', label: '跟随系统' },
]

const colors = ['#e94560', '#6c63ff', '#00b894', '#fdcb6e', '#e17055', '#74b9ff']

function setTheme(t) { settings.theme = t }
function setAccent(c) { settings.accentColor = c }
function applyFont() { settings.applyTheme(); settings.save() }
</script>

<style scoped>
.page { padding: 24px; overflow-y: auto; height: 100%; }
h1 { font-size: 20px; margin-bottom: 24px; color: var(--accent); }
h2 { font-size: 14px; margin-bottom: 12px; color: var(--text-secondary); }

.section {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 20px;
  margin-bottom: 16px;
}

.option-group { display: flex; gap: 10px; flex-wrap: wrap; }

.opt-btn {
  padding: 8px 20px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-input);
  cursor: pointer;
  transition: all var(--transition);
}

.opt-btn:hover { border-color: var(--accent); }
.opt-btn.active { border-color: var(--accent); background: var(--accent); color: #fff; }

.color-group { display: flex; gap: 10px; flex-wrap: wrap; }

.color-btn {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 3px solid transparent;
  cursor: pointer;
  transition: all var(--transition);
}

.color-btn:hover { transform: scale(1.1); }
.color-btn.active { border-color: var(--text-primary); transform: scale(1.15); }

.slider-row {
  display: flex;
  align-items: center;
  gap: 16px;
}

.slider-row input[type="range"] {
  flex: 1;
  max-width: 300px;
  accent-color: var(--accent);
}

.slider-row span { font-size: 14px; font-weight: 600; min-width: 40px; }
</style>
