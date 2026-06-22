import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    // Демо: nginx проксирует внешний Host; разрешаем домены туннеля
    allowedHosts: true,
    proxy: { '/api': { target: 'http://backend:8000', changeOrigin: true } }
  }
})