import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import {seoPlugin} from './seo-plugin.js'

export default defineConfig(({mode})=>({
  appType: 'mpa',
  plugins: [vue(), tailwindcss(), seoPlugin(loadEnv(mode,process.cwd(),'VITE_').VITE_SITE_URL || '')],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true, ws: true } },
  },
}))
