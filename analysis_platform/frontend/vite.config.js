// [开发文档 二.V1.0 - Vite构建工具配置]
// 功能: Vite构建配置，设置Vue插件、开发服务器代理
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src')
    }
  },
  server: {
    port: 5173,
    // [WSL2 修复] /mnt/d (drvfs) 上 inotify 监听失效 → 改用轮询，文件修改立即生效
    watch: {
      usePolling: true,
      interval: 300,
    },
    // [开发文档 三.3.1 - 前后端通信代理]
    // 开发环境下将 /api 请求代理到Flask后端
    proxy: {
      '/api': {
        target: 'http://localhost:5001',
        changeOrigin: true
      }
    }
  }
})
