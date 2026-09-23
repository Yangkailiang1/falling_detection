// 算法迭代平台 - Vite 构建配置
// 端口 5174，/api 代理到 Flask 后端 :5003
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
    port: 5174,
    // [WSL2 修复] /mnt/d (drvfs) 上 inotify 监听失效 → 改用轮询，文件修改立即生效
    watch: {
      usePolling: true,
      interval: 300,
    },
    proxy: {
      '/api': {
        target: 'http://localhost:5003',
        changeOrigin: true
      }
    }
  }
})
