import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    sourcemap: false,
    // Charting is ~90% of the bundle, so App.tsx loads it with React.lazy and
    // Rollup splits it out on its own. No manual chunking needed.
    chunkSizeWarningLimit: 600,
  },
});
