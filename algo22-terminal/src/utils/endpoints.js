const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000/api";

const endpoints = {
  auth: {
    login: `${API_BASE}/auth/login`,
    signup: `${API_BASE}/auth/signup`,
    me: `${API_BASE}/auth/me`,
  },
  billing: {
    checkout: `${API_BASE}/billing/checkout`,
    // No `portal`. REMOVED BY BILL-HIST-02, finishing BILLING-PORTAL-02's clean-up.
    // It composed a URL for `POST /api/billing/portal`, which answers
    // `501 PROVIDER_HAS_NO_BILLING_PORTAL` to every caller: Razorpay is the only gateway
    // and publishes no hosted self-serve portal. Nothing in this app read the entry — the
    // two controls that reached that route went with BILLING-PORTAL-02, and they called
    // `api.billing.openPortal` rather than this map — so it was a dead URL that read as a
    // live capability to the next person opening this file.
    subscription: `${API_BASE}/billing/subscription`,
  },
  strategies: {
    list: `${API_BASE}/strategies`,
    create: `${API_BASE}/strategies`,
    validate: `${API_BASE}/strategies/validate`,
    backtest: `${API_BASE}/strategies/backtest`,
  },
  library: {
    list: `${API_BASE}/library`,
    publish: `${API_BASE}/library`,
  },
};

export default endpoints;
