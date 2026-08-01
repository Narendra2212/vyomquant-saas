/**
 * Centralized configuration for Algo22 Terminal
 * Uses Vite's import.meta.env for environment variables
 */

export const CONFIG = {
  apiBaseUrl: import.meta.env.VITE_API_URL || "http://localhost:8000",
  wsBaseUrl: import.meta.env.VITE_WS_URL || "ws://localhost:8000",
  // 🔴 STEP 12: Request timeout - 30 seconds to prevent hanging requests
  REQUEST_TIMEOUT: 30000
};

export default CONFIG;
