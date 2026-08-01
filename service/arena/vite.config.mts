import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3100,
    hmr: {
      overlay: false,
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        timeout: 120000,
        proxyTimeout: 120000,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
        // Reconnect ws proxy when backend restarts
        configure: (proxy) => {
          proxy.on('error', () => {});
        },
      },
    },
  },
})
