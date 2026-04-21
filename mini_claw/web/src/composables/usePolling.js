import { ref, onMounted, onUnmounted } from 'vue'

export function usePolling(fetchFn, intervalMs = 5000) {
  const data = ref(null)
  const error = ref(null)
  let timer = null
  let active = true

  async function poll() {
    if (!active) return
    try {
      data.value = await fetchFn()
      error.value = null
    } catch (e) {
      error.value = e
    }
  }

  function start() {
    active = true
    poll()
    timer = setInterval(poll, intervalMs)
  }

  function stop() {
    active = false
    clearInterval(timer)
  }

  onMounted(start)
  onUnmounted(stop)

  return { data, error, refresh: poll, stop, start }
}
