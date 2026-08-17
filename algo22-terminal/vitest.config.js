import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  root: './',
  plugins: [react()],
  test: {
    root: './',
    include: ['tests/unit/**/*.{test,spec}.{js,jsx,ts,tsx}'],
    exclude: ['**/archive/**', '**/node_modules/**', '**/dist/**', '../**'],
    environment: 'jsdom',
    globals: true,
    setupFiles: [],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
    },
  },
});
