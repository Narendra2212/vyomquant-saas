import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  reporter: [['html', { open: 'always' }]],
  use: { 
    ...devices['Desktop Chrome'],
    headless: false,
  },
});
