import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: '../static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          'hls': ['hls.js'],
          'leaflet': ['leaflet'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/v1': 'http://localhost:8082',
      '/v1.1': 'http://localhost:8082',
      '/v2': 'http://localhost:8082',
      '/connectdata': 'http://localhost:8082',
      '/ws': {
        target: 'http://localhost:8082',
        ws: true,
      },
    },
  },
})
