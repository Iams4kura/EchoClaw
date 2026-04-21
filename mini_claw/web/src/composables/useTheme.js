import { watch } from 'vue'
import { appStore } from '../stores/app.js'

export function useTheme() {
  watch(() => appStore.theme, () => {
    appStore.applyTheme()
    appStore.save()
  })

  watch(() => appStore.accentColor, () => {
    appStore.applyTheme()
    appStore.save()
  })

  watch(() => appStore.fontSize, () => {
    appStore.applyTheme()
    appStore.save()
  })

  return appStore
}
