import { test, expect } from '@playwright/test';

test.describe('Aerora Production E2E Suite', () => {

  test('Register', async ({ page }) => {
    // Navigate to registration and create a new tenant account
    await page.goto('/register');
    await page.fill('input[name="email"]', 'test_quant@aerora.io');
    await page.fill('input[name="password"]', 'SecurePass123!');
    await page.click('button[type="submit"]');
    await expect(page.locator('.dashboard-container')).toBeVisible();
  });

  test('Login', async ({ page }) => {
    // Authenticate using existing credentials
    await page.goto('/login');
    await page.fill('input[name="email"]', 'test_quant@aerora.io');
    await page.fill('input[name="password"]', 'SecurePass123!');
    await page.click('button[type="submit"]');
    await expect(page.locator('.dashboard-header')).toContainText('Dashboard');
  });

  test('Create Strategy', async ({ page }) => {
    await page.goto('/strategies/new');
    await page.fill('input[name="strategy_name"]', 'Alpha Momentum V1');
    await page.selectOption('select[name="symbol"]', 'BTC/USDT');
    await page.selectOption('select[name="timeframe"]', '1h');
    // Add RSI indicator logic
    await page.click('#add-indicator-btn');
    await page.selectOption('.indicator-select', 'RSI');
    await expect(page.locator('.strategy-builder-canvas')).toBeVisible();
  });

  test('Save Strategy', async ({ page }) => {
    await page.goto('/strategies/active');
    await page.click('#save-strategy-btn');
    await expect(page.locator('.toast-success')).toHaveText('Strategy saved successfully');
  });

  test('Clone Strategy', async ({ page }) => {
    await page.goto('/strategies');
    await page.click('.strategy-card:has-text("Alpha Momentum V1") .clone-btn');
    await expect(page.locator('.strategy-card:has-text("Alpha Momentum V1 (Copy)")')).toBeVisible();
  });

  test('Backtest', async ({ page }) => {
    await page.goto('/backtest');
    await page.selectOption('select[name="strategy"]', 'Alpha Momentum V1');
    await page.fill('input[name="start_date"]', '2025-01-01');
    await page.fill('input[name="end_date"]', '2025-06-01');
    await page.click('#run-backtest-btn');
    await expect(page.locator('.backtest-results-chart')).toBeVisible({ timeout: 30000 });
  });

  test('Optimization', async ({ page }) => {
    await page.goto('/optimize');
    await page.selectOption('select[name="strategy"]', 'Alpha Momentum V1');
    await page.click('#start-optimization-btn');
    // Wait for the 3D surface plot to render
    await expect(page.locator('.optimization-surface-plot')).toBeVisible({ timeout: 45000 });
  });

  test('Deploy Paper Bot', async ({ page }) => {
    await page.goto('/deploy');
    await page.selectOption('select[name="strategy"]', 'Alpha Momentum V1');
    await page.selectOption('select[name="mode"]', 'paper');
    await page.click('#deploy-bot-btn');
    await expect(page.locator('.status-badge-running')).toBeVisible();
  });

  test('Pause Bot', async ({ page }) => {
    await page.goto('/bots/active');
    await page.click('.bot-card:has-text("Alpha Momentum V1") .pause-btn');
    await expect(page.locator('.status-badge-paused')).toBeVisible();
  });

  test('Resume Bot', async ({ page }) => {
    await page.goto('/bots/active');
    await page.click('.bot-card:has-text("Alpha Momentum V1") .resume-btn');
    await expect(page.locator('.status-badge-running')).toBeVisible();
  });

  test('Stop Bot', async ({ page }) => {
    await page.goto('/bots/active');
    await page.click('.bot-card:has-text("Alpha Momentum V1") .stop-btn');
    // Handle confirmation modal
    await page.click('.modal-confirm-btn');
    await expect(page.locator('.status-badge-stopped')).toBeVisible();
  });

});
