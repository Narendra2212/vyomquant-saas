/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/devAssert — how a `components/ds/` primitive enforces its own contract
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.1. design.md §5.1, §11.1, §11.2.
 *
 * Several requirements in this spec are of the form "a surface that shows X must
 * also show Y". Requirement 14.1 wants an empty state to say what is missing, why
 * it matters and what to do next. Requirement 12.2 wants a money-bearing panel to
 * name its trading environment. Requirement 15.3 wants a disabled control to name
 * the reason. Requirement 19.3 wants an unavailable panel to carry a human reason.
 *
 * Each of those can be honoured by discipline at every call site, and each of them
 * has already been broken that way at least once in this codebase. The alternative
 * this module exists for is to make the omission a *failure* at the one place the
 * pairing is known: the primitive. A panel that shows positions without saying
 * which environment they are in stops the developer who wrote it, in development,
 * with a message naming the panel — instead of reaching a trader who then has to
 * guess whether the P&L in front of them is real.
 *
 * WHY IT THROWS IN DEVELOPMENT AND LOGS IN PRODUCTION
 * --------------------------------------------------
 * Throwing is what makes the rule structural: it cannot be skimmed past, and the
 * test suite (which runs with `import.meta.env.DEV === true`) exercises it. But an
 * unhandled throw in a production trading terminal takes down the surrounding
 * React subtree, and a panel missing its environment badge is a worse panel, not a
 * reason to lose the page. So in production the same failure is logged with the
 * same message and the caller renders its safest fallback — which for every use in
 * this module means "show less, and never guess".
 *
 * `import.meta.env.DEV` is read at call time rather than captured at module load,
 * for the same two reasons `design/errorCopy.js` gives: a test can stub it with
 * `vi.stubEnv('DEV', false)`, and the production bundle still gets the
 * constant-folded `false` that Vite substitutes.
 *
 * NOT EXPORTED FROM THE BARREL. This is an internal enforcement helper, not one of
 * design.md §5's sixteen primitives; task 6.23's `ds/index.js` must not list it.
 *
 * @module components/ds/devAssert
 */

/** True in development and under the test runner; false in a production bundle. */
export function isDevelopment() {
  return import.meta.env.DEV === true;
}

/**
 * A contract a primitive will not render without.
 *
 * Throws in development, logs and returns in production. The message should name
 * the offending component, the prop that is missing, and what to pass instead —
 * it is read by whoever wrote the call site, not by a trader.
 *
 * @param {boolean} satisfied Result of the check. `true` means nothing happens.
 * @param {string} message What is wrong and what to do about it.
 * @returns {boolean} `satisfied`, so a caller can branch on it for its fallback.
 */
export function assertContract(satisfied, message) {
  if (satisfied === true) return true;
  const full = `[ds] ${message}`;
  if (isDevelopment()) throw new Error(full);
  console.error(full);
  return false;
}

/**
 * A contract that is *probably* broken, reported without stopping the render.
 *
 * Reserved for checks that a legitimate call site can fail. `Panel` uses exactly
 * one: a panel in `refreshing` with no children. §11.1 says `refreshing` is
 * reachable only from `ready`, so previous data should be on screen — but a page
 * that writes `{rows.length > 0 && <DataTable …/>}` can legitimately hand over
 * `false` for one render, and turning that into a throw would punish a correct
 * page for a rendering idiom. It is worth saying out loud and not worth failing.
 *
 * @param {boolean} satisfied
 * @param {string} message
 * @returns {boolean} `satisfied`.
 */
export function warnContract(satisfied, message) {
  if (satisfied === true) return true;
  console.error(`[ds] ${message}`);
  return false;
}

/** True when `value` is a string with visible content — the test every "required human reason" uses. */
export function hasText(value) {
  return typeof value === 'string' && value.trim() !== '';
}

/**
 * True when `value` can be rendered as a component.
 *
 * `typeof Icon === 'function'` is the obvious test and it is WRONG for the icon set
 * this app uses: every export of `lucide-react` at the pinned version is a
 * `React.forwardRef` result, which is a plain object carrying a `$$typeof` symbol, so
 * a `typeof === 'function'` gate silently drops every icon it is handed. That is how
 * `EmptyState`'s icon came to render nothing while every prop was correct.
 *
 * Checking for the `$$typeof` symbol covers `forwardRef` and `memo` without pulling in
 * `react-is` (which this project pins at a major version ahead of its React).
 *
 * @param {unknown} value
 * @returns {boolean}
 */
export function isComponentType(value) {
  if (typeof value === 'function') return true;
  if (typeof value !== 'object' || value === null) return false;
  return typeof value.$$typeof === 'symbol';
}

export default assertContract;
