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
        // jszip is deliberately NOT listed here. axios and zustand are
        // imported from the app entry, so 'utils' lands in index.html's
        // modulepreload set and is fetched on every cold load - and naming
        // jszip alongside them dragged ~28 KB gz of ZIP machinery into that
        // eager path for a feature only the export button uses. Left
        // unnamed, Rollup splits it into the lazy chunk that actually
        // imports it (StoryPlayer's download path), where it belongs.
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'motion': ['framer-motion'],
          'icons': ['react-icons'],
          'utils': ['axios', 'zustand']
        }
      }
    },
    chunkSizeWarningLimit: 600,
    sourcemap: false
  }
})
