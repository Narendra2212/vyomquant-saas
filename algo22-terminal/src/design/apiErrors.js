/**
 * Reading a rejected API call — ONE place, for every page.
 *
 * WHY THIS EXISTS (launch audit, 2026-11)
 * ─────────────────────────────────────────────────────────────────────────────
 * Every call through `apiClient` rejects with an `ApiError` (`src/apiClient.js`), which carries
 * the HTTP status and body as OWN properties: `err.status` and `err.data`. It has no
 * `.response`. Pages written against the axios-native shape (`err.response.status`,
 * `err.response.data.detail`) therefore read `undefined` on every real failure and fell back to
 * a generic sentence — the server's actual reason ("your plan allows 1 strategy", "two-factor
 * authentication is required", "this graph has 3 problems") never reached the trader. Found on
 * Exchange Manager (EXCH-SAVE-01), Strategy Builder save, the Dashboard kill switch and Support.
 *
 * These helpers read BOTH shapes — `ApiError` first, axios-native second — so a page that uses
 * them is correct whichever client produced the error, and no page has to know which.
 *
 * `ApiError` itself is deliberately NOT given a `.response`: `apiClient`'s retry and
 * circuit-breaker predicates branch on `error.response`, and adding it would switch both on for
 * every rejection — a behaviour change to the network layer that does not belong in a fix to
 * how pages read errors.
 *
 * Regression: tests/unit/design/apiErrors.test.js
 */

/** The HTTP status of a rejected call, or `null` for a network failure / non-HTTP error. */
export function apiErrorStatus(err) {
  const status = err?.status ?? err?.response?.status;
  return Number.isFinite(Number(status)) && status !== null ? Number(status) : null;
}

/** The response body of a rejected call, or `null`. */
export function apiErrorBody(err) {
  return err?.data ?? err?.response?.data ?? null;
}

/** The body's `detail` (FastAPI's refusal envelope) — a string, an object, or `null`. */
export function apiErrorDetail(err) {
  const body = apiErrorBody(err);
  if (body && typeof body === 'object' && 'detail' in body) return body.detail ?? null;
  return null;
}

// Text that must never be shown to a trader even when the server sent it: a stack trace, SQL, or a
// raw Python exception means a handler leaked an internal, not that it wrote copy for a person.
const INTERNAL_TEXT = /Traceback|SELECT |INSERT |UPDATE |TypeError|AttributeError|KeyError|500 Internal|Cannot read propert|is not a function|undefined is not/;

const usable = (value) =>
  typeof value === 'string' && value.trim() !== '' && !INTERNAL_TEXT.test(value) ? value.trim() : null;

/**
 * The best human sentence a rejected call carries, or `fallback`.
 *
 * Order: a structured `detail.message` → a string `detail` → the body's top-level `message` →
 * `ApiError`'s own message (which `apiClient` already derived from the body) → `fallback`.
 * Anything that looks like a leaked internal is skipped, never displayed.
 */
export function apiErrorMessage(err, fallback = 'Something went wrong. Please try again.') {
  const detail = apiErrorDetail(err);
  const body = apiErrorBody(err);
  const candidates = [
    detail && typeof detail === 'object' ? detail.message : null,
    typeof detail === 'string' ? detail : null,
    body && typeof body === 'object' ? body.message : null,
    // Any error's own message, as the pages' previous `|| err?.message` fallback did — still
    // filtered by `usable`, so a leaked internal is never shown.
    err?.message,
  ];
  for (const candidate of candidates) {
    const text = usable(candidate);
    if (text && text !== 'Request failed') return text;
  }
  return fallback;
}
