import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { visualizer } from "rollup-plugin-visualizer";
import { sentryVitePlugin } from "@sentry/vite-plugin";

const host = process.env.TAURI_DEV_HOST;
const sentryAuthToken = process.env.SENTRY_AUTH_TOKEN;

const deleteSourcemapsPlugin = () => ({
  name: 'delete-sourcemaps',
  closeBundle() {
    import('node:child_process').then(({ execSync }) => {
      try {
        if (process.platform === 'win32') {
          execSync('del /s /q dist\\*.map');
        } else {
          execSync('find dist -name "*.map" -type f -delete');
        }
      } catch(e) {}
    });
  }
});

// https://vite.dev/config/
export default defineConfig(async () => ({
  base: '/',
  plugins: [
    react(),
    visualizer({ open: false, filename: 'bundle-analysis.html' }),
    sentryAuthToken && sentryVitePlugin({
      org: process.env.SENTRY_ORG || "aerora",
      project: process.env.SENTRY_PROJECT || "algo22-terminal",
      authToken: sentryAuthToken,
      sourcemaps: {
        assets: "./dist/**",
      },
      disable: !sentryAuthToken,
    }),
    deleteSourcemapsPlugin()
  ],

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
  },
  build: {
    // Produce hidden source maps so they can be uploaded to Sentry but not publicly served
    sourcemap: 'hidden',
    outDir: 'dist',
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          if (id.includes('node_modules')) {
            if (id.includes('ag-grid-community') || id.includes('ag-grid-react')) {
              return 'vendor-ag-grid';
            }
            if (id.includes('reactflow') || id.includes('@reactflow')) {
              return 'vendor-reactflow';
            }
            if (id.includes('recharts')) {
              return 'vendor-recharts';
            }
            if (id.includes('lightweight-charts')) {
              return 'vendor-charts';
            }
          }
        },
      },
    },
  },
  test: {
    include: ['src/**/*.{test,spec}.{js,jsx,ts,tsx}', 'tests/**/*.{test,spec}.{js,jsx,ts,tsx}'],
    exclude: ['**/node_modules/**', '**/dist/**', '**/archive/**'],
  },
}));

