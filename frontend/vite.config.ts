import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  return {
    plugins: [vue()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      port: 5173,
      proxy: {
        '/qq/webhook': {
          target: env.VITE_QQ_PROXY_TARGET || 'http://localhost:8203',
          changeOrigin: true,
        },
        '/api': {
          target: env.VITE_PROXY_TARGET || 'http://localhost:8200',
          changeOrigin: true,
        },
      },
    },
    build: {
      chunkSizeWarningLimit: 1_600,
      rollupOptions: {
        output: {
          manualChunks: {
            'ant-design-vue': ['ant-design-vue', '@ant-design/icons-vue'],
            'vue-vendor': ['vue', 'vue-router', 'pinia', 'axios'],
          },
        },
      },
    },
  }
})
