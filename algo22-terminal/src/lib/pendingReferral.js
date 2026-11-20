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
import { post, publicPost } from '../apiClient';

export const PENDING_REFERRAL_KEY = 'vq_pending_referral';
// REF-LEN-01: the SAME rule the server enforces (routers/referral.py,
// REFERRAL_CODE_PATTERN): 5-20 letters/digits. Real codes are 8-10 characters. A looser
// rule here let a 3-4 or 21-32 character code through the form, only for the server to
// answer with a bare 422 instead of the form's own "check the code" message.
const CODE = /^[A-Za-z0-9]{5,20}$/;
export const REFERRAL_CODE_HINT = 'Referral codes are 5-20 letters and digits.';

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

/** REF-UI-01: normalise a code typed into the signup form. Returns null if malformed. */
export function normaliseReferralCode(raw) {
  const code = String(raw || '').trim().toUpperCase();
  return CODE.test(code) ? code : null;
}

/** REF-UI-01: remember a code the user TYPED (the form's field), as a link would. */
export function setPendingReferral(code) {
  const clean = normaliseReferralCode(code);
  if (!clean) return null;
  try {
    store()?.setItem(PENDING_REFERRAL_KEY, clean);
  } catch {
    return null;
  }
  return clean;
}

/** REF-UI-01: the user emptied the field -- they do not want a referral recorded. */
export function clearPendingReferral() {
  try { store()?.removeItem(PENDING_REFERRAL_KEY); } catch { /* storage unavailable */ }
}

/**
 * REF-UI-01: ask the server whether a code exists and is active, BEFORE the account is
 * created (there is no session yet, so this is the public endpoint). Resolves to
 * `{ valid, message }`; a network failure resolves to `{ valid: null }` -- unknown, so the
 * form does not block signup on an outage (the claim after first sign-in re-checks anyway).
 */
export async function validateReferralCode(code, send = (c) => publicPost('/api/referral/validate', { referral_code: c })) {
  const clean = normaliseReferralCode(code);
  if (!clean) return { valid: false, message: REFERRAL_CODE_HINT };
  try {
    const res = await send(clean);
    return { valid: Boolean(res?.valid), message: res?.message || '' };
  } catch (err) {
    const status = err?.response?.status ?? err?.status;
    if (status === 422) return { valid: false, message: REFERRAL_CODE_HINT };
    return { valid: null, message: '' };
  }
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
