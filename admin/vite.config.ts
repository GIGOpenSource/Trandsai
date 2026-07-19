import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const API_BASE = process.env.API_BASE_URL || 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': {
        target: API_BASE,
        changeOrigin: true,
      },
      '/data': {
        target: API_BASE,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return

          if (id.includes('react') || id.includes('scheduler')) return 'vendor-react'
          if (id.includes('@ant-design/pro-provider')) return 'vendor-ant-pro-provider'
          if (id.includes('@ant-design/pro-layout')) return 'vendor-ant-pro-layout'
          if (id.includes('@ant-design/pro-table')) return 'vendor-ant-pro-table'
          if (id.includes('@ant-design/pro-form')) return 'vendor-ant-pro-form'
          if (id.includes('@ant-design/pro-card')) return 'vendor-ant-pro-card'
          if (id.includes('@ant-design/pro-utils')) return 'vendor-ant-pro-utils'
          if (id.includes('@ant-design/pro-field/es/components/')) return 'vendor-ant-pro-field-components'
          if (id.includes('@ant-design/pro-field/es/utils/')) return 'vendor-ant-pro-field-utils'
          if (id.includes('@ant-design/pro-field/es/')) return 'vendor-ant-pro-field-core'
          if (id.includes('@ant-design/pro-field')) return 'vendor-ant-pro-field'
          if (id.includes('@ant-design/pro-descriptions')) return 'vendor-ant-pro-desc'
          if (id.includes('@ant-design/pro-list')) return 'vendor-ant-pro-list'
          if (id.includes('@ant-design/pro-provider')) return 'vendor-ant-pro-provider'
          if (id.includes('@ant-design/pro-components/es/components/')) return 'vendor-ant-pro-components'
          if (id.includes('@ant-design/pro-components/es/layouts/')) return 'vendor-ant-pro-layouts'
          if (id.includes('@ant-design/pro-components/es/')) return 'vendor-ant-pro-core'
          if (id.includes('@ant-design/pro-components') || id.includes('@ant-design/pro-')) return 'vendor-ant-pro'
          if (id.includes('@ant-design/icons') || id.includes('@ant-design/icons-svg')) return 'vendor-ant-icons'
          if (id.includes('antd') || id.includes('@ant-design')) return 'vendor-antd'
          if (id.includes('@antv')) return 'vendor-antv'
          if (id.includes('dayjs')) return 'vendor-dayjs'
          if (id.includes('lodash') || id.includes('lodash-es')) return 'vendor-lodash'
          if (id.includes('rc-')) return 'vendor-rc'
          if (id.includes('i18next') || id.includes('react-i18next')) return 'vendor-i18n'
          if (id.includes('react-router')) return 'vendor-router'

          return 'vendor'
        },
      },
    },
  },
})
