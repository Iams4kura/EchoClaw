import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true
  },
  server: {
    port: 5173,
    proxy: {
      '/message': 'http://localhost:8000',
      '/reset': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/status': 'http://localhost:8000',
      '/skills': 'http://localhost:8000',
      '/pending_question': 'http://localhost:8000',
      '/answer': 'http://localhost:8000',
      '/thinking': 'http://localhost:8000',
      '/notifications': 'http://localhost:8000',
      '/api': 'http://localhost:8000'
    }
  }
})
