import path from 'path'
import { fileURLToPath } from 'url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3004,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  },
  // 29 console.log calls were shipping to production. Dropped as pure calls so
  // they vanish from the bundle, while console.error/warn survive - those are
  // the ones worth having in a user's devtools when something actually breaks.
  esbuild: {
    pure: ['console.log', 'console.debug', 'console.info'],
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'motion': ['framer-motion'],
          'icons': ['react-icons'],
          'utils': ['axios', 'zustand', 'jszip']
        }
      }
    },
    chunkSizeWarningLimit: 600,
    sourcemap: false
  }
})
