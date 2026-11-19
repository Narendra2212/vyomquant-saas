/**
 * REF-ATTR-01: carry a `?ref=CODE` from the signup page to the first signed-in session.
 *
 * The referral link is `/signup?ref=CODE`, but signup goes through `supabase.auth.signUp`
 * and the account usually has no session until its email is confirmed -- possibly in another
 * tab, minutes later. So the code is kept in localStorage at signup and claimed by
 * `POST /api/referral/claim` the first time a session exists (AuthGuard). The server decides
 * everything that matters (valid code, self-referral, once per account, claim window); this
 * module only remembers the code and stops asking once the server has answered.
 */
import { post } from '../apiClient';

export const PENDING_REFERRAL_KEY = 'vq_pending_referral';
const CODE = /^[A-Za-z0-9]{3,32}$/;

const store = () => {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
};

/** Remember `ref` from a signup URL's query string. Returns the code kept, or null. */
export function capturePendingReferral(search) {
  let code;
  try {
    code = new URLSearchParams(search || '').get('ref');
  } catch {
    return null;
  }
  if (!code || !CODE.test(code)) return null;
  try {
    store()?.setItem(PENDING_REFERRAL_KEY, code.toUpperCase());
  } catch {
    return null;
  }
  return code.toUpperCase();
}

export function readPendingReferral() {
  try {
    return store()?.getItem(PENDING_REFERRAL_KEY) || null;
  } catch {
    return null;
  }
}

let inFlight = null;

/**
 * Claim a remembered code for the signed-in account, once. The code is cleared after any
 * definitive answer (attributed or refused); a network or 5xx failure keeps it for the next
 * session, so a transient outage does not lose the referral.
 */
export function flushPendingReferral(send = (code) => post('/api/referral/claim', { referral_code: code })) {
  const code = readPendingReferral();
  if (!code) return Promise.resolve(null);
  if (inFlight) return inFlight;
  inFlight = Promise.resolve()
    .then(() => send(code))
    .then((res) => {
      try { store()?.removeItem(PENDING_REFERRAL_KEY); } catch { /* storage unavailable */ }
      return res;
    })
    .catch((err) => {
      const status = err?.response?.status ?? err?.status;
      if (status && status >= 400 && status < 500 && status !== 401 && status !== 429) {
        try { store()?.removeItem(PENDING_REFERRAL_KEY); } catch { /* storage unavailable */ }
      }
      return null;
    })
    .finally(() => { inFlight = null; });
  return inFlight;
}
