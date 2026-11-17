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
    // PERF-01 EXPERIMENT: manualChunks removed. See the measurement before
    // keeping this. Forcing a vendor into a NAMED chunk that several lazily
    // loaded chunks share makes Rollup hoist that chunk into the entry's static
    // graph (so it is not duplicated), and Vite then emits a modulepreload for
    // it in index.html. Both of these packages are correctly lazy in source --
    // ds/index.js documents at length why `Chart` is deliberately NOT in the
    // barrel, and all six importers of ds/Chart are dynamic -- so the config was
    // undoing the architecture the code had set up.
  },
  test: {
    include: ['src/**/*.{test,spec}.{js,jsx,ts,tsx}', 'tests/**/*.{test,spec}.{js,jsx,ts,tsx}'],
    exclude: ['**/node_modules/**', '**/dist/**', '**/archive/**'],
  },
}));

