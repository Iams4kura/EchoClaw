import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

const proxyPaths = [
  '/message',
  '/reset',
  '/health',
  '/status',
  '/skills',
  '/pending_question',
  '/answer',
  '/thinking',
  '/notifications',
  '/api'
]

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget =
    process.env.VITE_API_TARGET || env.VITE_API_TARGET || 'http://localhost:8080'

  return {
    plugins: [vue()],
    base: '/',
    build: {
      outDir: 'dist',
      emptyOutDir: true
    },
    server: {
      port: 5173,
      proxy: Object.fromEntries(proxyPaths.map(path => [path, apiTarget]))
    }
  }
})
