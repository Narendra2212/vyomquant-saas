/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/design/reported.js — `Reported<T>`, the load-bearing type
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.4. design.md §18 (Data Models).
 * Requirements 14.5, 19.3. Property P5.
 *
 * The union, exactly as design.md §18 declares it:
 *
 *     Reported<T> = { available: true, value: T } | { available: false, reason: string }
 *
 * WHY A UNION AND NOT A NULLABLE FIELD
 * ------------------------------------
 * Requirement 14.5 forbids substituting a fabricated value when the read that should
 * have produced it failed, and Requirement 19.3 asks for an explicit "not available"
 * state wherever the backend has no answer. Both are satisfiable with discipline —
 * `{pnl: null}` plus a `pnl == null ?` at every call site — and both have already been
 * broken that way in this codebase: `pages/Portfolio.jsx` fell back to a hardcoded
 * `100000/0` summary, and a `positions: []` default rendered as the honest-looking
 * claim "you have no open positions".
 *
 * A union makes the check unskippable instead of remembered. There is no way to read
 * a `Reported<T>` without first branching on `available`, because the `value` key does
 * not exist on the other arm: a page that forgets renders `undefined`, which is
 * visible in the first test that touches it, rather than a plausible `0` that nobody
 * questions until it is quoted at a trader as a balance.
 *
 * The second half of the mechanism is that the unavailable arm carries a **reason**
 * rather than a flag. "Not available" alone tells a trader the same nothing a blank
 * cell does; "the exchange connector does not report unrealised P&L" tells them the
 * figure is not coming and why, which is a different fact from "the read failed, try
 * again". `ds/ErrorState` covers the second. This covers the first.
 *
 * WHERE IT IS CONSUMED
 * -------------------
 * `ds/Metric` accepts either a raw value or a `Reported<T>` and renders the
 * unavailable arm's reason on the not-available marker, so a page can adopt the union
 * one field at a time. `readReported` is the accessor both halves of that go through:
 * it is total over every input, so no consumer has to guard before destructuring, and
 * every "this cannot be rendered" decision in the app is made in one function.
 *
 * NO REACT, NO COLOUR, NO COPY BEYOND ONE FALLBACK REASON. This module is imported by
 * property tests, by page view-model builders and by `ds/Metric`; keeping it free of
 * all three is what lets the tests read it directly.
 */

/**
 * The reason used when a value is absent and nobody said why.
 *
 * Deliberately about the *report* and not about the trader's account: "the server did
 * not report this value" is true whenever this string is reached, whereas anything
 * more specific ("you have no positions", "the connector is down") would be a guess
 * dressed as an explanation. A call site that knows better passes its own reason, and
 * the ones that matter — balances, P&L, exposure — are expected to.
 */
export const UNREPORTED_REASON = 'The server did not report this value.';

/** True for a string with visible content. The same test `ds/devAssert.hasText` applies. */
function hasText(value) {
  return typeof value === 'string' && value.trim() !== '';
}

/**
 * Whether a raw value is renderable as a reading.
 *
 * Exported because it is the definition of "unsupplied" that Property 5 asserts
 * against, and a second definition living in the test would be a second definition.
 * Four things are not readings:
 *
 *   * `null` / `undefined` — nothing was reported.
 *   * A blank string — `''` renders as an empty cell, which is indistinguishable from
 *     a layout bug and is exactly what P5 rules out ("never an empty string").
 *   * A non-finite number — `NaN` and `±Infinity` come out of arithmetic on missing
 *     operands (`fees / trades` with no trades), and formatting one produces the
 *     string "NaN" beside a currency symbol. An unreadable figure is not a reading.
 *   * A value of a type no figure can be — an object, an array, a function. Rendering
 *     one is a React crash at best and `[object Object]` at worst.
 *
 * `0` and `false` ARE readings and are never touched by this function. That is the
 * whole point of Requirement 14.5: a flat P&L is a fact, and the failure mode this
 * module exists to prevent is a *fabricated* zero, not a measured one.
 *
 * @param {unknown} value
 * @returns {boolean}
 */
export function isReadableValue(value) {
  if (value === null || value === undefined) return false;
  switch (typeof value) {
    case 'number':
      return Number.isFinite(value);
    case 'string':
      return value.trim() !== '';
    case 'boolean':
    case 'bigint':
      return true;
    default:
      // Symbols, functions, objects and arrays. A figure is a scalar.
      return false;
  }
}

/**
 * The available arm. `available(0)` is a reported zero and stays one.
 *
 * @template T
 * @param {T} value
 * @returns {{available: true, value: T}}
 */
export function available(value) {
  return Object.freeze({ available: true, value });
}

/**
 * The unavailable arm, with the human reason Requirement 19.3 asks for.
 *
 * A blank or missing reason falls back to {@link UNREPORTED_REASON} rather than
 * throwing: this module is called from view-model builders that must not be able to
 * take a page down, and an unavailable field with a generic reason is still honest.
 * The reason is trimmed so a call site's incidental whitespace does not reach a
 * tooltip.
 *
 * @param {string} [reason]
 * @returns {{available: false, reason: string}}
 */
export function unavailable(reason) {
  return Object.freeze({
    available: false,
    reason: hasText(reason) ? reason.trim() : UNREPORTED_REASON,
  });
}

/**
 * The type guard.
 *
 * Membership is decided by `typeof candidate.available === 'boolean'` and nothing
 * else, on purpose. A stricter test — "the available arm must own a `value` key" —
 * would classify the malformed `{available: true}` as a *raw value*, and a raw object
 * reaching `ds/Metric` is a React render crash. Under this test it is a `Reported`
 * whose available arm carries nothing, which {@link readReported} resolves to
 * not-available. Every malformed shape therefore fails in the safe direction.
 *
 * @param {unknown} candidate
 * @returns {boolean}
 */
export function isReported(candidate) {
  return (
    typeof candidate === 'object'
    && candidate !== null
    && !Array.isArray(candidate)
    && typeof candidate.available === 'boolean'
  );
}

/**
 * The safe accessor. **Total over every input**, including inputs that are not
 * `Reported` at all.
 *
 * Returns one normalised shape so a consumer branches once and never has to ask which
 * kind of thing it was handed:
 *
 *   `{available: true,  value: T,    reason: null}`
 *   `{available: false, value: null, reason: string}`   ← `reason` is never blank
 *
 * `available` is `true` only when there is something readable to render, which means
 * the available arm of a `Reported` is *not* taken at its word: `{available: true,
 * value: null}` resolves to not-available. A producer claiming a value it does not
 * have is the failure this whole module is aimed at, and honouring the claim would
 * put `undefined` on screen beside a currency symbol.
 *
 * A raw value is accepted so a page can adopt the union field by field:
 * `readReported(12843.55)` is available and `readReported(null)` is not.
 *
 * @template T
 * @param {*} candidate A `Reported<T>`, or a raw `T`.
 * @param {string} [fallbackReason] Used when the absence carries no reason of its own.
 * @returns {{available: boolean, value: T|null, reason: string|null}}
 */
export function readReported(candidate, fallbackReason) {
  const fallback = hasText(fallbackReason) ? fallbackReason.trim() : UNREPORTED_REASON;

  if (isReported(candidate)) {
    // The unavailable arm's own reason wins over the caller's fallback: it was written
    // by whoever knew why the field is missing.
    if (candidate.available === false) {
      return Object.freeze({
        available: false,
        value: null,
        reason: hasText(candidate.reason) ? candidate.reason.trim() : fallback,
      });
    }
    return isReadableValue(candidate.value)
      ? Object.freeze({ available: true, value: candidate.value, reason: null })
      : Object.freeze({ available: false, value: null, reason: fallback });
  }

  return isReadableValue(candidate)
    ? Object.freeze({ available: true, value: candidate, reason: null })
    : Object.freeze({ available: false, value: null, reason: fallback });
}

/**
 * A nullable server field → a `Reported<T>`, with the reason stated at the point where
 * it is known.
 *
 * This is the form a page's view-model builder uses, and the reason is required in
 * spirit even though it is optional in signature: `fromNullable(body.total_pnl)` gives
 * a trader "not available" with no explanation, which is the state Requirement 19.3
 * asks pages to improve on. Passing the sentence costs one line and is the difference
 * between a blank figure and a fact.
 *
 * @template T
 * @param {T|null|undefined} value
 * @param {string} [reason] Why this field can be absent, in a trader's terms.
 * @returns {{available: true, value: T}|{available: false, reason: string}}
 */
export function fromNullable(value, reason) {
  return isReadableValue(value) ? available(value) : unavailable(reason);
}
