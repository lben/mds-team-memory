import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      // Without this the notification socket hits Vite itself, is rejected,
      // and reconnects forever behind the polling fallback.
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
