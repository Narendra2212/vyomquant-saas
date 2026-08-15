/**
 * Centralized configuration for Algo22 Terminal
 * Uses Vite's import.meta.env for environment variables with safe browser origin fallbacks
 */

const getBrowserApiBase = () => {
  if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL;
  if (typeof window !== 'undefined' && window.location.origin && window.location.origin !== 'null') {
    return window.location.origin;
  }
  return "http://localhost:8000";
};

const getBrowserWsBase = () => {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL;
  if (typeof window !== 'undefined' && window.location.host) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}`;
  }
  return "ws://localhost:8000";
};

export const CONFIG = {
  apiBaseUrl: getBrowserApiBase(),
  wsBaseUrl: getBrowserWsBase(),
  // 🔴 STEP 12: Request timeout - 30 seconds to prevent hanging requests
  REQUEST_TIMEOUT: 30000
};

export default CONFIG;
