# Billing System Frontend Audit
**Phase 3: Frontend Audit - Inspect All Billing UI**

Generated: 2025-01-08
Project: VyomQuant SaaS
Scope: Billing & Subscription System Frontend Components

---

## Executive Summary

The frontend has multiple hardcoded plan definitions across different components with inconsistent naming conventions. There is no centralized plan configuration, no feature flag system, and no dynamic plan fetching from the backend. The billing UI is functional but lacks proper integration with the backend plan system.

---

## 1. Frontend Components with Billing Logic

### 1.1 Billing Page
**File:** `algo22-terminal/src/pages/Billing.jsx`
**Purpose:** Main billing management page

**Plan Definitions (Lines 17-21):**
```javascript
const plans = [
  { id: "free", name: "Free Tier", usd: 0, inr: 0, features: ["1 Deployed Bot", "Algorithm Builder", "3 Backtests/mo", "No ML Training"], description: "Start building and testing core ideas." },
  { id: "pro", name: "Pro Tier", usd: 12, inr: 999, features: ["5 Deployed Algos", "Unlimited Backtesting", "Telegram+Email Alerts", "Algorithm Indicators"], description: "Built for active algo developers." },
  { id: "enterprise", name: "Enterprise Tier", usd: 24, inr: 1999, features: ["8 Deployed Algos", "2 ML/DL Models Training", "Priority Support", "Full API Access"], description: "Institutional-grade execution and controls." },
];
```

**ML Addon Price (Line 22):**
```javascript
const EXTRA_ML_MODEL_PRICE = { INR: 199, USD: 2.5 };
```

**Plan Mapping Logic (Lines 62-63):**
```javascript
if (planId === "pro" || planId === "pro_999") tier = "pro_999";
else if (planId === "enterprise" || planId === "elite_1999") tier = "elite_1999";
```

**Issues:**
- Plan IDs inconsistent with backend (`pro` vs `pro_999`, `enterprise` vs `elite_1999`)
- Prices hardcoded (should fetch from backend)
- Features hardcoded (should fetch from backend)
- No validation that selected plan exists
- No error handling for invalid plan IDs
- Currency toggle manual (no auto-detection)

**State Management:**
- `currentPlan` - Fetched from backend via `endpoints.billing.getPlan()`
- `billingHistory` - Fetched from backend via `endpoints.billing.getInvoices()`
- `savedMethods` - Fetched from backend via `endpoints.billing.getPaymentMethods()`
- `currency` - Local state (USD/INR toggle)
- `isUpgradeModalOpen` - Modal state

**API Calls:**
- `endpoints.billing.getPlan()` - Fetch current plan
- `endpoints.billing.getInvoices()` - Fetch invoice history
- `endpoints.billing.getPaymentMethods()` - Fetch payment methods
- `endpoints.billing.createCheckout()` - Create checkout session

### 1.2 Landing Page Pricing
**File:** `algo22-terminal/src/components/landing/Pricing.jsx`
**Purpose:** Public pricing display for marketing

**Plan Definitions (Lines 5-32):**
```javascript
const tiers = [
  {
    name: "Free",
    price: { monthly: 0, annual: 0 },
    features: ["1 Deployed Bot", "Algorithm Builder", "3 Backtests/mo", "No ML Training"]
  },
  {
    name: "Pro",
    price: { monthly: 12, annual: 115 },
    features: ["5 Deployed Algos", "Unlimited Backtesting", "Telegram+Email Alerts", "Algorithm Indicators"]
  },
  {
    name: "Elite",
    price: { monthly: 24, annual: 230 },
    features: ["8 Deployed Algos", "2 ML/DL Models Training", "Priority Support", "Full API Access"]
  }
];
```

**Issues:**
- Different plan names than Billing.jsx (`Elite` vs `Enterprise`)
- Annual pricing hardcoded (20% discount calculation)
- No API integration - purely presentational
- No currency localization (USD only)
- No backend validation of displayed prices
- "Save 20%" badge hardcoded

**State Management:**
- `isAnnual` - Monthly/annual toggle (local state only)

**API Calls:**
- None (purely presentational)

### 1.3 Onboarding Wizard
**File:** `algo22-terminal/src/pages/Wizard.jsx`
**Purpose:** New user onboarding with plan selection

**Plan Definitions (Lines 47-51):**
```javascript
const plans = [
  { id: "free", n: "Free", tier: "free", inr: 0, usd: 0, f: ["1 Deployed Bot", "Algorithm Builder", "3 Backtests/mo", "No ML Training"] },
  { id: "pro", n: "Pro Tier", tier: "pro_999", inr: 999, usd: 12, f: ["5 Deployed Algos", "Unlimited Backtesting", "Telegram+Email Alerts", "Algorithm Indicators"], best: true },
  { id: "elite", n: "Enterprise Tier", tier: "elite_1999", inr: 1999, usd: 24, f: ["8 Deployed Algos", "2 ML/DL Models Training", "Priority Support", "Full API Access"] },
];
```

**Checkout Logic (Lines 53-72):**
```javascript
const handleSelectPlan = async (p) => {
  if (p.tier === "free") {
    navigate("/app/dashboard");
    return;
  }
  setIsCheckoutLoading(p.tier);
  try {
    const data = await endpoints.billing.createCheckout({ tier: p.tier, currency: "INR" });
    if (data && data.checkoutUrl) {
      window.location.href = data.checkoutUrl;
    } else {
      navigate("/app/dashboard");
    }
  } catch (err) {
    console.error("Wizard checkout error:", err);
    navigate("/app/dashboard");
  } finally {
    setIsCheckoutLoading("");
  }
};
```

**Issues:**
- Hardcoded to INR currency (no USD option)
- No validation that selected plan exists
- Error handling just navigates to dashboard (no user feedback)
- Plan IDs inconsistent with other components
- No plan comparison features

**State Management:**
- `step` - Wizard step (0-3)
- `isCheckoutLoading` - Loading state for checkout

**API Calls:**
- `endpoints.billing.createCheckout()` - Create checkout session

### 1.4 Sidebar
**File:** `algo22-terminal/src/components/Sidebar.jsx`
**Purpose:** Main navigation sidebar

**Hardcoded Tier Display (Line 96):**
```javascript
<div style={{ color: C.t3 }} className="text-[8px] font-mono">PRO TIER</div>
```

**Issues:**
- Tier label hardcoded to "PRO TIER"
- No dynamic fetching of user's actual tier
- No visual indication of tier changes
- No upgrade prompt when on free tier

**State Management:**
- None (static component)

**API Calls:**
- None

### 1.5 Profile Page
**File:** `algo22-terminal/src/pages/Profile.jsx`
**Purpose:** User profile and settings

**Billing Display (Lines 436-456):**
```javascript
{/* Subscription */}
<Card cls="p-6 mb-4">
  <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
    <CreditCard size={20} style={{ color: C.accent }} />
    <h3 style={{ fontSize: 14, fontWeight: 600, color: C.t1 }}>Subscription</h3>
  </div>
  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: 16 }}>
    <div>
      <div style={{ color: C.t2, fontSize: 10, marginBottom: 4 }}>Current Plan</div>
      <div style={{ fontSize: 16, fontWeight: 600, color: C.t1 }}>{billing?.name || "Free Tier"}</div>
    </div>
    <div>
      <div style={{ color: C.t2, fontSize: 10, marginBottom: 4 }}>Status</div>
      <div style={{ fontSize: 14, color: C.t1 }}>{billing?.autoRenew ? "Active" : "Inactive"}</div>
    </div>
    <div>
      <div style={{ color: C.t2, fontSize: 10, marginBottom: 4 }}>Price</div>
      <div style={{ fontSize: 14, color: C.t1 }}>
        ${billing?.priceUSD || 0}/mo
      </div>
    </div>
  </div>
</Card>
```

**Billing Data Fetch (Lines 31-34):**
```javascript
const [profileData, billingData, referralData, statsData, securityData, notifData] = await Promise.allSettle([
  api.user.getProfile(),
  api.user.getBillingPlan(),
  api.referral.getStats(),
  api.user.getStats(),
  api.user.getSecurityLogs(20),
]);
```

**WebSocket Subscriptions (Lines 82-95):**
```javascript
// Subscribe to profile updates via WebSocket
wsSubscriptionRef.current = wsClient.subscribe('profile_update', (message) => {
  console.log('Profile update received:', message);
  if (message.data) {
    setProfile(prev => ({ ...prev, ...message.data }));
  }
});

// Subscribe to billing updates
const billingSub = wsClient.subscribe('billing_update', (message) => {
  console.log('Billing update received:', message);
  if (message.data) {
    setBilling(prev => ({ ...prev, ...message.data }));
  }
});
```

**Issues:**
- No upgrade button in subscription section
- No usage display (bots used, ML models used)
- No downgrade option
- No cancel subscription option
- WebSocket subscriptions for billing updates (backend may not send these events)

**State Management:**
- `billing` - Billing plan data from backend
- WebSocket subscriptions for real-time updates

**API Calls:**
- `api.user.getBillingPlan()` - Fetch billing plan

### 1.6 FAQ Component
**File:** `algo22-terminal/src/components/landing/FAQ.jsx`
**Purpose:** Frequently asked questions

**Billing FAQ (Lines 11-16):**
```javascript
const billingFaqs = [
  { question: 'Is my exchange API key information secure?', answer: 'AES-256 encryption at rest. Keys never traverse the frontend. Row-level security isolation ensures your credentials are logically separated from all other users.' },
  { question: 'Can I cancel or change my subscription tier at any time?', answer: 'Yes. Modify or cancel your subscription at any time from the account panel. Changes take effect at the next billing cycle.' },
  { question: 'What happens to my strategies if I downgrade to Free?', answer: 'Your strategies remain in read-only state. Live bots are paused. You retain access to paper mode and community features.' },
  { question: 'Is there a free trial for Pro or Elite features?', answer: 'The Free tier provides full platform access with capacity limits. Upgrade to Pro or Elite when you require additional bots, backtests, or ML slots.' },
];
```

**Issues:**
- FAQ answers claim features that may not be implemented (downgrade, cancel)
- No link to actual billing panel
- Mentions "Elite" plan (inconsistent with "Enterprise")
- No mention of ML addons

**State Management:**
- None (static content)

**API Calls:**
- None

---

## 2. Plan Naming Convention Inconsistencies

### 2.1 Plan ID Mapping

| Component | Free | Pro | Elite/Enterprise |
|-----------|------|-----|------------------|
| Billing.jsx | `free` | `pro` | `enterprise` |
| Pricing.jsx | `Free` | `Pro` | `Elite` |
| Wizard.jsx | `free` | `pro` | `elite` |
| Backend Billing | `free` | `pro_999` | `elite_1999` |
| Backend User | `free` | `pro` | `enterprise` |
| Backend Tenant | `FREE` | `BASIC` | `PROFESSIONAL` |

### 2.2 Price Inconsistencies

| Plan | Billing.jsx | Pricing.jsx | Wizard.jsx | Backend |
|------|-------------|-------------|------------|---------|
| Free | $0 | $0 | $0 | $0 |
| Pro | $12 | $12 | $12 | $12 |
| Elite/Enterprise | $24 | $24 | $24 | $24 |
| Annual Pro | N/A | $115 | N/A | N/A |
| Annual Elite | N/A | $230 | N/A | N/A |

**Issues:**
- Annual pricing only in Pricing.jsx
- No annual pricing in backend
- No annual pricing in checkout flow
- Discount calculation manual in Pricing.jsx

---

## 3. Missing Frontend Features

### 3.1 Feature Flags
**Status:** Not implemented
**Problem:** No system to conditionally show/hide features based on plan

**Missing:**
- Feature flag component/library
- Feature flag API endpoint
- Feature flag configuration
- Feature flag management UI

### 3.2 Usage Display
**Status:** Partially implemented
**Problem:** No display of current usage vs limits

**Missing:**
- Bot usage display (X of Y bots deployed)
- ML model usage display (X of Y models trained)
- Backtest usage display (X of Y backtests this month)
- API call usage display
- Storage usage display

### 3.3 Currency Detection
**Status:** Not implemented
**Problem:** No automatic currency selection based on user location

**Missing:**
- IP-based country detection
- Browser language detection
- Automatic currency selection
- Regional pricing display

### 3.4 Plan Comparison
**Status:** Not implemented
**Problem:** No side-by-side plan comparison

**Missing:**
- Comparison table
- Feature differences highlighted
- Upgrade recommendations
- Downgrade warnings

### 3.5 Upgrade Prompts
**Status:** Not implemented
**Problem:** No proactive upgrade prompts when hitting limits

**Missing:**
- In-app upgrade prompts
- Limit warning banners
- Upgrade recommendation engine
- Upgrade incentive display

### 3.6 Subscription Management
**Status:** Partially implemented
**Problem:** Limited subscription management options

**Missing:**
- Cancel subscription button
- Downgrade plan option
- Pause subscription option
- Renewal toggle
- Payment method update

---

## 4. API Client Analysis

### 4.1 Billing API Module
**File:** `algo22-terminal/src/api/modules/billing.js`

**Methods:**
```javascript
- getPlan() - Fetch current billing plan
- getInvoices() - Fetch invoice history
- getPaymentMethods() - Fetch saved payment methods
- createCheckout(request) - Create checkout session
```

**Issues:**
- No method to fetch available plans
- No method to update plan
- No method to cancel subscription
- No method to fetch usage stats
- No method to fetch billing history beyond invoices

### 4.2 User API Module
**File:** `algo22-terminal/src/api/modules/user.js`

**Billing-Related Methods:**
```javascript
- getBillingPlan() - Fetch billing plan (same as billing.getPlan)
```

**Issues:**
- Duplicate method (exists in both user.js and billing.js)
- No other billing-related methods

---

## 5. UI/UX Issues

### 5.1 Billing Page
**Issues:**
- No loading state for plan data
- No error state for failed API calls
- No empty state for no payment methods
- No empty state for no billing history
- Upgrade modal not responsive on mobile

### 5.2 Pricing Page
**Issues:**
- No loading state
- No error state
- No currency toggle (USD only)
- Annual pricing toggle not clear
- No plan comparison feature

### 5.3 Wizard
**Issues:**
- No validation of exchange connection before proceeding
- No validation of security setup before proceeding
- Plan selection forced (must select plan to continue)
- No skip option for plan selection
- Hardcoded to INR currency

### 5.4 Profile Page
**Issues:**
- Billing section not prominent
- No upgrade button
- No usage display
- No billing history link
- No payment method management

### 5.5 Sidebar
**Issues:**
- Hardcoded tier label
- No tier indicator color coding
- No upgrade prompt
- No usage indicator

---

## 6. Hardcoded Values Summary

### 6.1 Plan Names
- `free`, `pro`, `enterprise` (Billing.jsx)
- `Free`, `Pro`, `Elite` (Pricing.jsx)
- `Free`, `Pro Tier`, `Enterprise Tier` (Wizard.jsx)
- `PRO TIER` (Sidebar.jsx)

### 6.2 Plan Prices
- Free: $0 (all components)
- Pro: $12/mo, ₹999/mo (all components)
- Elite/Enterprise: $24/mo, ₹1999/mo (all components)
- Pro Annual: $115/yr (Pricing.jsx only)
- Elite Annual: $230/yr (Pricing.jsx only)

### 6.3 Plan Features
- Free: 1 bot, builder, 3 backtests/mo, no ML
- Pro: 5 bots, unlimited backtests, alerts, indicators
- Elite/Enterprise: 8 bots, 2 ML models, priority support, full API

### 6.4 ML Addon Price
- INR: 199 (Billing.jsx)
- USD: 2.5 (Billing.jsx)

### 6.5 Currency
- Manual toggle (Billing.jsx)
- Hardcoded to INR (Wizard.jsx)
- USD only (Pricing.jsx)

---

## 7. Recommendations

### 7.1 Immediate (P0)
1. Standardize plan naming convention across all components
2. Fetch plan definitions from backend API
3. Remove hardcoded plan definitions
4. Implement centralized plan configuration
5. Add plan validation on checkout

### 7.2 Short-term (P1)
1. Implement feature flag system
2. Add usage display components
3. Implement currency detection
4. Add plan comparison feature
5. Add upgrade prompts

### 7.3 Long-term (P2)
1. Implement subscription management UI
2. Add billing analytics dashboard
3. Implement regional pricing
4. Add plan recommendation engine
5. Create billing admin panel

---

## 8. Next Steps

**Phase 3: Frontend Audit - Remove Placeholder/Dead UI**
- Identify placeholder UI elements
- Find dead code paths
- Remove unused components
- Clean up hardcoded values

**Phase 4: Backend Audit**
- Inspect all billing services
- Find race conditions
- Identify security vulnerabilities
- Review webhook handlers

---

**End of Phase 3 Frontend Audit**
