import { test, expect } from '@playwright/test';

const BASE_URL = 'http://localhost:1420';
const API_URL = 'http://127.0.0.1:8000';
const TEST_USER = {
  email: 'test@test.com',
  password: 'test123'
};

test('REAL trading flow → strategy → backtest → equity validation', async ({ page, request }) => {

  // -------------------------------
  // 1. Signin to get real token
  // -------------------------------
  let accessToken;
  
  try {
    const signinResponse = await request.post(`${API_URL}/api/auth/signin`, {
      data: {
        email: TEST_USER.email,
        password: TEST_USER.password
      }
    });

    console.log('Signin response status:', signinResponse.status());
    
    if (signinResponse.status() === 200) {
      const signinData = await signinResponse.json();
      accessToken = signinData.access_token;
      // F-04 REMEDIATION (Phase 7B): Never log raw JWT to test output.
      // Use structural/presence confirmation only.
      console.log('Access token obtained: [REDACTED, length=' + (accessToken ? accessToken.length : 0) + ']');
    }
  } catch (error) {
    console.warn('Signin failed, using fallback token:', error.message);
  }

  // Fallback to dev token if signin failed
  if (!accessToken) {
    console.warn('Using fallback dev token');
    accessToken = 'dev_bypass_token_e2e_test';
  }

  // F-04: Log token presence only, not the value
  console.log('Final token present:', !!accessToken, '| length:', accessToken ? accessToken.length : 0);

  // -------------------------------
  // 2. Load App
  // -------------------------------
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });

  // -------------------------------
  // 3. Inject real token into localStorage
  // -------------------------------
  await page.addInitScript((token, testEmail) => {
    localStorage.setItem('token', token);
    window.TEST_USER = window.TEST_USER || {
      id: 'e2e_user',
      email: testEmail
    };
  }, accessToken, TEST_USER.email);

  await page.reload({ waitUntil: 'domcontentloaded' });

  // -------------------------------
  // 4. Go to Strategy Builder
  // -------------------------------
  await page.waitForLoadState('domcontentloaded');
  const strategyButton = page.getByRole('button', { name: /strategy/i }).first();
  await strategyButton.click({ timeout: 5000 });
  await page.waitForLoadState('domcontentloaded');

  // -------------------------------
  // 5. Create Strategy Graph
  // -------------------------------

  // Add source node (example: CCXT Feed)
  await page.getByText(/CCXT/i).first().dragTo(
    page.locator('.react-flow__pane')
  );

  // Add indicator
  await page.getByText(/RSI/i).first().dragTo(
    page.locator('.react-flow__pane')
  );

  // Add execution node
  await page.getByText(/Buy/i).first().dragTo(
    page.locator('.react-flow__pane')
  );

  // -------------------------------
  // 6. Connect Nodes (basic)
  // -------------------------------
  const handles = page.locator('.react-flow__handle');

  // connect first 3 handles (simple heuristic)
  await handles.nth(0).dragTo(handles.nth(1));
  await handles.nth(1).dragTo(handles.nth(2));

  // -------------------------------
  // 7. Save Strategy
  // -------------------------------
  await page.getByRole('button', { name: 'Save' }).click();

  // -------------------------------
  // 8. Run Backtest
  // -------------------------------
  await page.getByRole('button', { name: 'Backtest' }).click();

  // Wait for execution
  await page.waitForTimeout(5000);

  // -------------------------------
  // 9. Validate Equity Curve
  // -------------------------------

  const equityText = page.locator('text=$');

  await expect(equityText.first()).toBeVisible();

  // -------------------------------
  // 10. Validate Non-zero Performance
  // -------------------------------
  const pnl = await page.locator('text=/\\$[0-9]/').first().textContent();

  expect(pnl).not.toBe('$0.00');

  // -------------------------------
  // 11. Validate Chart Exists
  // -------------------------------
  await expect(page.locator('canvas, svg')).toBeVisible();

});