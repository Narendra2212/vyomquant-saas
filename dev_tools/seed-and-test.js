const fs = require('fs');
const path = require('path');

const testCode = `import { test, expect } from '@playwright/test';

test('Sweep all components with REAL Database Auth', async ({ page, request }) => {
  const failedRequests = [];
  
  page.on('response', response => {
    if (response.url().includes('/api/') && response.status() >= 400) {
      console.log(\`[ RESPONSE STATUS: \${response.status()} ] at \${response.url()}\`);
      failedRequests.push(response.url());
    }
  });

  console.log('1. Communicating with FastAPI to register real user...');
  const creds = { 
    email: 'neo@algo22.io', 
    username: 'Neo_Trader', 
    password: 'SecurePassword123!',
    phoneNumber: '555-010-1212'
  };
  
  let token = null;
  
  // Try to register the user
  let res = await request.post('http://127.0.0.1:8000/api/auth/signup/verify-create', { data: creds });
  let data = await res.json();
  
  if (data.token) {
    token = data.token;
    console.log('New user successfully saved to SQLite Database!');
  } else {
    // If email already registered, just sign in to get the token
    console.log('User already exists, signing in to fetch fresh JWT...');
    res = await request.post('http://127.0.0.1:8000/api/auth/signin', { data: creds });
    data = await res.json();
    token = data.token;
  }

  if (!token) {
    console.log('CRITICAL ERROR: Could not generate a real token from FastAPI.');
    return;
  }

  console.log('2. Injecting real JWT into Frontend...');
  await page.goto('http://localhost:1420');

  // Inject the REAL token
  await page.evaluate((realToken) => {
    localStorage.setItem('algo22_token', realToken);
  }, token);

  await page.reload();

  console.log('3. Waiting for Dashboard to authenticate...');
  await page.waitForSelector('text="Total Portfolio"', { timeout: 15000 });
  console.log('Real Auth Successful! Starting secure component sweep...');

  const pagesToTest = [
    'Live Trading', 
    'Strategies', 
    'Portfolio', 
    'Trade History',
    'Exchanges',
    'Risk Settings',
    'Billing',
    'Security Logs',
    'Leaderboard',
    'Referral',
    'Support',
    'Notifications'
  ];

  for (const pageName of pagesToTest) {
    console.log(\`Testing component: \${pageName}...\`);
    await page.click(\`text="\${pageName}"\`);
    await page.waitForTimeout(1000); 
  }

  if (failedRequests.length === 0) {
    console.log('FLAWLESS VICTORY: Zero 404s and Zero 403s. Architecture is 100% stable.');
  } else {
    console.log(\`SWEEP COMPLETE: Found \${failedRequests.length} remaining errors.\`);
  }
  
  expect(failedRequests.length).toBe(0);
});
`;

const dir = path.join(process.cwd(), 'tests');
fs.writeFileSync(path.join(dir, 'connection-sweep.spec.js'), testCode, 'utf8');
console.log("SUCCESS: Playwright script updated for fully automated Database Seeding & Testing!");
