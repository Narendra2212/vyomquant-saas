import { defineConfig, devices } from '@playwright/test';

/**
 * Read environment variables from file.
 * https://github.com/motdotla/dotenv
 */
// import dotenv from 'dotenv';
// import path from 'path';
// dotenv.config({ path: path.resolve(__dirname, '.env') });

/**
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  testDir: './tests/e2e',
  /* Run tests in files in parallel */
  fullyParallel: true,
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Opt out of parallel tests on CI. */
  workers: process.env.CI ? 1 : undefined,
  /* Reporter to use. See https://playwright.dev/docs/test-reporters */
  /*
   * Local behaviour is unchanged. On CI the `html` reporter alone puts the failure in a
   * zipped artifact and the log says only "To open last HTML report run…", so a red job
   * cannot be diagnosed from the log it prints. `list` restores that; `html` is kept
   * beside it, with `open: 'never'` because there is no browser to open it in.
   */
  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never' }]]
    : 'html',
  /* Shared settings for all the projects below. See https://playwright.dev/docs/api/class-testoptions. */
  use: {
    /* Base URL to use in actions like `await page.goto('/')`. */
    baseURL: process.env.TEST_BASE_URL || 'http://localhost:1420',

    /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
    trace: 'on-first-retry',
  },

  /* Configure projects for major browsers */
  projects: [
    /*
     * T03's layout sweep, and the only project CI runs today (`.github/workflows/11-layout.yml`).
     *
     * SEPARATE, AND CHROMIUM-ONLY, ON PURPOSE. The specs it owns assert box geometry
     * against a container — `boundingBox()` arithmetic and the document's scrollHeight —
     * which is layout engine behaviour that CSS-grid-correct browsers agree on. Running it
     * three times would triple the browser download for three identical answers, and the
     * cost of a cold `playwright install` is the main thing that makes a browser job
     * expensive. Each spec sets its own viewport, so the sizes come from the spec rather
     * than from a project per size.
     *
     * The three browser projects below `grepInvert` the same tag, so the sweep has exactly
     * one owner and `npx playwright test` does not quietly run it once per engine.
     */
    {
      name: 'layout',
      grep: /@layout/,
      use: { ...devices['Desktop Chrome'] },
    },

    {
      name: 'chromium',
      grepInvert: /@layout/,
      use: { ...devices['Desktop Chrome'] },
    },

    {
      name: 'firefox',
      grepInvert: /@layout/,
      use: { ...devices['Desktop Firefox'] },
    },

    {
      name: 'webkit',
      grepInvert: /@layout/,
      use: { ...devices['Desktop Safari'] },
    },

    /* Test against mobile viewports. */
    // {
    //   name: 'Mobile Chrome',
    //   use: { ...devices['Pixel 5'] },
    // },
    // {
    //   name: 'Mobile Safari',
    //   use: { ...devices['iPhone 12'] },
    // },

    /* Test against branded browsers. */
    // {
    //   name: 'Microsoft Edge',
    //   use: { ...devices['Desktop Edge'], channel: 'msedge' },
    // },
    // {
    //   name: 'Google Chrome',
    //   use: { ...devices['Desktop Chrome'], channel: 'chrome' },
    // },
  ],

  /* Run your local dev server before starting the tests */
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:1420',
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
  },
});
