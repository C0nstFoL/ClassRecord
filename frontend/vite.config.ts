import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // 构建产物直接输出到后端的静态资源目录，由 FastAPI 同源托管
    outDir: '../backend/static',
    emptyOutDir: true,
  },
  server: {
    // 开发模式下将 /api 与 /auth 转发给本地后端，避免跨域 cookie 问题
    proxy: {
      '/api': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
    },
  },
})
