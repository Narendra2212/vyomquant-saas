/**
 * src/legal/consent.js — LEGAL-01
 *
 * ANALYTICS CONSENT. Google Analytics and Microsoft Clarity load ONLY after the
 * visitor agrees.
 *
 * WHY THIS EXISTS
 * ===============
 * Two problems, one fix.
 *
 * 1. The Privacy Policy claimed "We do not use third-party tracking or
 *    advertising cookies." That was false: src/bootstrap.js loaded Google
 *    Analytics and Microsoft Clarity unconditionally on first paint, before any
 *    interaction, and index.html's CSP admits both. Clarity records pointer
 *    movement, clicks and scrolling — session replay, not page counting.
 *
 * 2. Signups are open globally and the Privacy Policy now carries GDPR/UK-GDPR
 *    rights. Under the ePrivacy Directive and GDPR, non-essential analytics and
 *    session-replay scripts need consent BEFORE they load. Disclosure alone is
 *    not consent, and a banner that loads the scripts while asking is not
 *    consent either.
 *
 * So the honest fix is both: say what we use, and do not load it until asked.
 *
 * WHAT IS AND IS NOT GATED
 * ========================
 * GATED (consent required):
 *   Google Analytics  — usage analytics
 *   Microsoft Clarity — session analytics and replay
 *
 * NOT GATED:
 *   Authentication/session cookies — strictly necessary; the product cannot
 *     work without them and no consent is sought for them.
 *   Sentry — error diagnostics, relied on under legitimate interest and
 *     configured not to capture credentials. Declared in the Privacy Policy
 *     under that basis rather than hidden among the analytics.
 *
 * DEFAULT IS DENIED, AND SILENCE IS NOT CONSENT
 * =============================================
 * ``hasAnalyticsConsent()`` is false until the visitor actively chooses. No
 * stored decision means no analytics — not "assume yes until they object",
 * which is the pattern regulators have repeatedly rejected. Dismissing the
 * banner without choosing leaves it denied.
 *
 * WITHDRAWAL IS REAL, WITH ONE HONEST LIMIT
 * =========================================
 * Withdrawing consent stops further collection and is reflected immediately in
 * ``window.gtag``/``window.clarity`` becoming inert stubs. It CANNOT unload a
 * script the browser has already executed — only a reload does that, and this
 * module says so rather than implying a clean revocation. The Privacy Policy
 * carries the same caveat: withdrawing stops future collection and does not
 * retrieve what was already collected.
 *
 * STORAGE
 * =======
 * localStorage, not a cookie, so the consent record itself is not transmitted
 * with every request. Keyed with a version so a future change to WHAT is being
 * consented to invalidates old decisions instead of silently inheriting them.
 */

/** Bump when the set of gated services changes, invalidating stored decisions. */
export const CONSENT_VERSION = '1';

const STORAGE_KEY = `vq.analyticsConsent.v${CONSENT_VERSION}`;

export const ConsentState = {
  /** No decision recorded. Treated as DENIED for loading purposes. */
  UNKNOWN: 'unknown',
  GRANTED: 'granted',
  DENIED: 'denied',
};

/** Subscribers notified when the decision changes. */
const listeners = new Set();

function readStored() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === ConsentState.GRANTED || raw === ConsentState.DENIED) return raw;
    return ConsentState.UNKNOWN;
  } catch {
    // Private browsing, disabled storage, or a sandboxed iframe. A visitor whose
    // decision cannot be remembered is treated as not having consented, which
    // is the safe direction: it under-collects rather than over-collects.
    return ConsentState.UNKNOWN;
  }
}

/** The current decision. `UNKNOWN` until the visitor chooses. */
export function getConsentState() {
  return readStored();
}

/**
 * Whether analytics may load. FALSE unless explicitly granted.
 *
 * Deliberately not `!== DENIED` — that would treat an absent decision as
 * permission, which is exactly the behaviour being removed.
 */
export function hasAnalyticsConsent() {
  return readStored() === ConsentState.GRANTED;
}

/** Whether to show the banner: only when no decision has been recorded. */
export function needsConsentDecision() {
  return readStored() === ConsentState.UNKNOWN;
}

function notify(state) {
  listeners.forEach((fn) => {
    try {
      fn(state);
    } catch {
      // A broken subscriber must not prevent the others from being told, nor
      // leave the decision half-applied.
    }
  });
}

/**
 * Record the visitor's decision and tell subscribers.
 *
 * @param {boolean} granted
 * @returns {string} the state actually stored
 */
export function setAnalyticsConsent(granted) {
  const state = granted ? ConsentState.GRANTED : ConsentState.DENIED;
  try {
    window.localStorage.setItem(STORAGE_KEY, state);
  } catch {
    // Unstorable. The in-memory notification below still applies for this page
    // view, so a granted decision takes effect now even if it cannot persist.
  }
  notify(state);
  return state;
}

/**
 * Subscribe to decision changes. Returns an unsubscribe function.
 *
 * bootstrap.js uses this to load the analytics scripts the moment consent is
 * granted, so agreeing takes effect without a page reload.
 */
export function onConsentChange(fn) {
  if (typeof fn !== 'function') return () => {};
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/**
 * Replace `window.gtag` and `window.clarity` with inert stubs.
 *
 * Called when consent is withdrawn. HONEST LIMIT, repeated here because it
 * matters: this stops our code sending anything further, but a script the
 * browser has already executed stays executed until the page is reloaded. The
 * caller should offer a reload, and the Privacy Policy states the same limit.
 */
export function neutraliseAnalytics() {
  const inert = function inertAnalyticsStub() {};
  try {
    window.gtag = inert;
    window.clarity = inert;
    // Signal GA's own consent mode as well, for the case where the tag is
    // already live and observing it.
    if (Array.isArray(window.dataLayer)) {
      window.dataLayer.push({
        event: 'consent_withdrawn',
        analytics_storage: 'denied',
      });
    }
  } catch {
    // Nothing further to do: the stubs are best effort on a page that has
    // already loaded the tags.
  }
}

export default {
  CONSENT_VERSION,
  ConsentState,
  getConsentState,
  hasAnalyticsConsent,
  needsConsentDecision,
  setAnalyticsConsent,
  onConsentChange,
  neutraliseAnalytics,
};
