const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000/api";

const endpoints = {
  auth: {
    login: `${API_BASE}/auth/login`,
    signup: `${API_BASE}/auth/signup`,
    me: `${API_BASE}/auth/me`,
  },
  billing: {
    checkout: `${API_BASE}/billing/checkout`,
    portal: `${API_BASE}/billing/portal`,
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
