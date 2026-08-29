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
    // Raised from the 5000ms default because a handful of tests render the full
    // palette / strategy-builder trees, which is slow enough that they can exceed
    // 5s when the whole suite runs in parallel on a loaded machine (they pass
    // comfortably when their file runs alone). This absorbs that scheduling
    // jitter -- it is not masking a hang; no single test here should take
    // anywhere near 15s.
    testTimeout: 15000,
    // The default forks pool spawns roughly one jsdom worker per core. These
    // render-heavy suites are memory-hungry, and on a host with limited free RAM
    // the extra workers cannot even complete their startup handshake
    // ("[vitest-pool]: Failed to start forks worker: Timeout waiting for worker
    // to respond"), so whole test files silently never run. Running the files
    // sequentially in one reused process keeps a full-suite run complete and
    // deterministic. Per-file module isolation still applies. Drop these on hosts
    // with more memory headroom if wall-clock time matters more.
    //
    // Vitest 4 removed `test.poolOptions` and promoted its contents to top-level
    // options. This was previously expressed as `poolOptions.forks.singleFork`,
    // which Vitest 4 silently ignored while emitting a deprecation notice -- so
    // the suite was in fact running fully parallel, and the render-heavy files
    // were timing out under exactly the contention this setting exists to avoid.
    // `fileParallelism: false` runs one file at a time; `maxWorkers: 1` keeps
    // that to a single reused worker.
    pool: 'forks',
    fileParallelism: false,
    maxWorkers: 1,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
    },
  },
});
