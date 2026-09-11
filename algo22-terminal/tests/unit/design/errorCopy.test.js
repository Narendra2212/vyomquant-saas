/**
 * `src/design/errorCopy.js` — the scrubber, and the resolution order it guards.
 *
 * Task 5.3. Requirements 14.3 and 14.4.
 *
 * The property test for Requirement 14.4 (Property 27) is task 5.4's and is deliberately not
 * here. What is here is the pair of facts the scrubber's whole value rests on:
 *
 *   1. It FIRES. Every table in `errorCopy.js` is authored clean, so no input to
 *      `translateError` reaches the forbidden branch today — which means an untested branch
 *      would silently rot until the day a `detail: err.message` needed catching. So the branch
 *      is driven directly through `enforceNoLeak`, the same function `translateError` calls,
 *      with a candidate carrying a stack frame and a status number.
 *   2. It fires the RIGHT WAY: throwing in development, degrading to category copy in
 *      production.
 *
 * Plus the cheap standing check that the tables themselves are clean, so a future entry that
 * quotes a status or names an exception class is caught here rather than in a browser.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  CATEGORY_COPY,
  CODE_COPY,
  CONTEXT_COPY,
  GUARD_CHECK_COPY,
  PAPER_START_REFUSAL_COPY,
  containsForbidden,
  enforceNoLeak,
  resolveCategory,
  translateError,
} from '../../../src/design/errorCopy.js';

/** An `ApiError`-shaped object: the fields `translateError` is allowed to read, and no others. */
const apiError = ({ status, data = null, requestId = null, category }) => ({
  status,
  data,
  requestId,
  category,
  isRetryable: () => category === 'NETWORK_ERROR' || category === 'SERVER_ERROR' || status === 429,
});

/** The body shape every `backend_app/routers/*` raise produces. */
const routerBody = (code, message = 'x', extra = {}) => ({
  detail: { error: code, message, ...extra },
});

/** The body shape the marketplace/paper exception handler serialises. */
const envelopeBody = (code, details = {}, requestId = 'req-abc') => ({
  error: { code, message: 'x', details },
  request_id: requestId,
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('the FORBIDDEN scrubber', () => {
  const STACK_AND_STATUS =
    'Request failed with status code 500\n    at handleError (/src/apiClient.js:731:23)';

  it('recognises each thing Requirement 14.4 forbids', () => {
    expect(containsForbidden('Request failed with status code 500')).toBe(true);
    expect(containsForbidden('AxiosError: timeout of 30ms exceeded')).toBe(true);
    expect(containsForbidden('KeyError')).toBe(true);
    expect(containsForbidden('at readBalance (/src/apiClient.js:12:9)')).toBe(true);
    expect(containsForbidden('boom\n    at foo')).toBe(true);
    expect(containsForbidden('Traceback (most recent call last):')).toBe(true);
    expect(containsForbidden('File "paper_repository.py", line 88, in read')).toBe(true);
    expect(containsForbidden('https://api.vyomquant.io/api/paper/account')).toBe(true);
  });

  it('passes authored copy', () => {
    expect(containsForbidden('Could not load your portfolio')).toBe(false);
    expect(containsForbidden('Live balance and position data is temporarily unreadable.')).toBe(false);
    expect(containsForbidden('')).toBe(false);
    expect(containsForbidden(null)).toBe(false);
  });

  it('throws in development when a candidate carries a stack frame and a status', () => {
    vi.stubEnv('DEV', true);
    const candidate = {
      headline: 'Could not read your paper account',
      detail: STACK_AND_STATUS,
      retryable: true,
      action: null,
      supportRef: 'req-1',
    };

    expect(() => enforceNoLeak(candidate, apiError({ status: 500, category: 'SERVER_ERROR' }), 'paper-trading')).toThrow(
      /forbidden content for paper-trading/,
    );
  });

  it('degrades to the category copy in production, keeping the support reference', () => {
    vi.stubEnv('DEV', false);
    const candidate = {
      headline: 'Could not read your paper account',
      detail: STACK_AND_STATUS,
      retryable: true,
      action: null,
      supportRef: 'req-1',
    };

    const out = enforceNoLeak(candidate, apiError({ status: 500, category: 'SERVER_ERROR' }), 'paper-trading');

    expect(out).toEqual({ ...CATEGORY_COPY.SERVER_ERROR, action: null, supportRef: 'req-1' });
    expect(containsForbidden(`${out.headline} ${out.detail}`)).toBe(false);
  });

  it('degrades to SERVER_ERROR copy when the error carries no category at all', () => {
    vi.stubEnv('DEV', false);
    const out = enforceNoLeak(
      { headline: 'boom', detail: 'TypeError: undefined is not a function', retryable: false, action: null, supportRef: null },
      new Error('boom'),
      'builder',
    );
    expect(out.headline).toBe(CATEGORY_COPY.SERVER_ERROR.headline);
    expect(out.supportRef).toBeNull();
  });

  it('scrubs the action label too, not just headline and detail', () => {
    vi.stubEnv('DEV', true);
    expect(() =>
      enforceNoLeak(
        { headline: 'Clean', detail: 'Clean.', retryable: false, action: { label: 'Retry after 503', to: null }, supportRef: null },
        null,
        'dashboard',
      ),
    ).toThrow(/forbidden content/);
  });

  it('leaves a clean candidate exactly as it was', () => {
    vi.stubEnv('DEV', true);
    const candidate = { headline: 'Signal not found', detail: 'It is not yours.', retryable: false, action: null, supportRef: null };
    expect(enforceNoLeak(candidate, null, 'signal-trace')).toBe(candidate);
  });
});

describe('every authored table is already clean', () => {
  const surfacesOf = (table) =>
    Object.entries(table).flatMap(([key, entry]) => [
      [`${key}.headline`, entry.headline],
      [`${key}.detail`, entry.detail],
      [`${key}.action.label`, entry.action?.label ?? null],
    ]);

  it.each([
    ['CODE_COPY', surfacesOf(CODE_COPY)],
    ['CATEGORY_COPY', surfacesOf(CATEGORY_COPY)],
    ['CONTEXT_COPY', surfacesOf(CONTEXT_COPY)],
  ])('%s carries no forbidden content', (_name, surfaces) => {
    const offenders = surfaces.filter(([, text]) => containsForbidden(text)).map(([where]) => where);
    expect(offenders).toEqual([]);
  });

  it.each([
    ['PAPER_START_REFUSAL_COPY', PAPER_START_REFUSAL_COPY],
    ['GUARD_CHECK_COPY', GUARD_CHECK_COPY],
  ])('%s carries no forbidden content', (_name, table) => {
    const offenders = Object.entries(table)
      .filter(([, text]) => containsForbidden(text))
      .map(([key]) => key);
    expect(offenders).toEqual([]);
  });

  it('gives every CODE_COPY entry a headline and a boolean retryable', () => {
    for (const [code, entry] of Object.entries(CODE_COPY)) {
      expect(typeof entry.headline, code).toBe('string');
      expect(entry.headline.length, code).toBeGreaterThan(0);
      expect(typeof entry.retryable, code).toBe('boolean');
    }
  });
});

describe('resolution order', () => {
  it('1. prefers the backend error code over everything else', () => {
    const out = translateError(
      apiError({ status: 503, data: routerBody('EQUITY_CURVE_FETCH_FAILED'), category: 'SERVER_ERROR' }),
      'portfolio',
    );
    expect(out.headline).toBe('Could not load the equity curve');
    expect(out.retryable).toBe(true);
  });

  it('1. reads the code from the marketplace envelope as well as the router detail', () => {
    const out = translateError(
      apiError({ status: 409, data: envelopeBody('MARKETPLACE_STRATEGY_UNAVAILABLE'), category: 'CLIENT_ERROR' }),
      'marketplace',
    );
    expect(out.headline).toBe('This strategy cannot run');
    expect(out.retryable).toBe(false);
  });

  it('1. fills PAPER_START_REFUSED detail from the validation label, never from the message', () => {
    const out = translateError(
      apiError({
        status: 409,
        data: envelopeBody('PAPER_START_REFUSED', { validation: 'STRATEGY_NOT_EXECUTABLE' }),
        category: 'CLIENT_ERROR',
      }),
      'paper-trading',
    );
    expect(out.headline).toBe('This session cannot start');
    expect(out.detail).toBe(PAPER_START_REFUSAL_COPY.STRATEGY_NOT_EXECUTABLE);
  });

  it('1. resolves an ENTITLEMENT refusal through the marketplace reason it names', () => {
    const out = translateError(
      apiError({
        status: 403,
        data: envelopeBody('PAPER_START_REFUSED', { validation: 'ENTITLEMENT', reason: 'EXPIRED' }),
        category: 'AUTH_ERROR',
      }),
      'paper-trading',
    );
    expect(out.detail).toBe(CODE_COPY.MARKETPLACE_SUBSCRIPTION_EXPIRED.detail);
  });

  it('2. names the safety checks from detail.reasons when the code is not one we table', () => {
    const out = translateError(
      apiError({
        status: 403,
        data: routerBody('SOME_UNTABLED_GUARD', 'blocked', {
          reasons: ['daily_drawdown: limit of 5% reached', 'exposure_limits: 3 positions over cap'],
        }),
        category: 'AUTH_ERROR',
      }),
      'live-trading',
    );
    expect(out.headline).toBe('Blocked by a safety check');
    expect(out.detail).toContain(GUARD_CHECK_COPY.daily_drawdown);
    expect(out.detail).toContain(GUARD_CHECK_COPY.exposure_limits);
    expect(out.retryable).toBe(false);
  });

  it('2. drops the free-form half of a reason, which can carry a Python exception', () => {
    const out = translateError(
      apiError({
        status: 500,
        data: routerBody('SOME_UNTABLED_GUARD', 'blocked', {
          reasons: ['exception: Exception during validation: KeyError(\'symbol\')'],
        }),
        category: 'SERVER_ERROR',
      }),
      'live-trading',
    );
    expect(out.detail).toBe(
      `Nothing was sent to the exchange. The checks that blocked it looked at ${GUARD_CHECK_COPY.exception}.`,
    );
    expect(containsForbidden(`${out.headline} ${out.detail}`)).toBe(false);
  });

  it('3. routes a WebSocket subscription_refused frame through CODE_COPY', () => {
    const frame = { type: 'subscription_refused', channel: 'deployment.abc', code: 'CHANNEL_FORBIDDEN', reason: 'not yours' };
    const out = translateError(frame, 'signal-trace');
    expect(out.headline).toBe(CODE_COPY.CHANNEL_FORBIDDEN.headline);
    expect(out.detail).toBe(CODE_COPY.CHANNEL_FORBIDDEN.detail);
  });

  it('3. offers sign-in on an unauthenticated refusal', () => {
    const out = translateError(
      { type: 'subscription_refused', channel: 'paper.1', code: 'CHANNEL_UNAUTHENTICATED', reason: 'no identity' },
      'paper-trading',
    );
    expect(out.action).toEqual({ label: 'Sign in', to: '/signin' });
  });

  it('4. falls to the HTTP category when the code is unknown', () => {
    const out = translateError(
      apiError({ status: 500, data: routerBody('SOMETHING_WE_DO_NOT_TABLE'), category: 'SERVER_ERROR' }),
      'dashboard',
    );
    expect(out.headline).toBe(CATEGORY_COPY.SERVER_ERROR.headline);
  });

  it('4. treats a 429 as RATE_LIMIT even though ApiError files it under CLIENT_ERROR', () => {
    expect(resolveCategory(apiError({ status: 429, category: 'CLIENT_ERROR' }))).toBe('RATE_LIMIT');
    const out = translateError(apiError({ status: 429, category: 'CLIENT_ERROR' }), 'dashboard');
    expect(out.headline).toBe(CATEGORY_COPY.RATE_LIMIT.headline);
    expect(out.retryable).toBe(true);
  });

  it("4. reuses ApiError's own isRetryable rather than recomputing it", () => {
    const network = apiError({ status: undefined, category: 'NETWORK_ERROR' });
    expect(translateError(network, 'portfolio').retryable).toBe(true);
    const auth = apiError({ status: 401, category: 'AUTH_ERROR' });
    expect(translateError(auth, 'portfolio').retryable).toBe(false);
    expect(translateError(auth, 'portfolio').action).toEqual({ label: 'Sign in', to: '/signin' });
  });

  it("4. maps ApiError's UNKNOWN_ERROR category onto server-side copy", () => {
    expect(resolveCategory({ status: 399, category: 'UNKNOWN_ERROR' })).toBe('SERVER_ERROR');
  });

  it('5. falls to the context default for a thrown Error, never reading its message', () => {
    const thrown = new Error('Request failed with status code 500\n    at load (/src/pages/Dashboard.jsx:12:3)');
    const out = translateError(thrown, 'dashboard');
    expect(out.headline).toBe(CONTEXT_COPY.dashboard.headline);
    expect(out.detail).toBe(CONTEXT_COPY.dashboard.detail);
    expect(out.detail).not.toContain('500');
  });

  it('5. is total over an unrecognised context, a string and null', () => {
    for (const input of [undefined, null, 'boom', 42, {}]) {
      const out = translateError(input, 'not-a-page');
      expect(out.headline).toBe(CONTEXT_COPY.default.headline);
      expect(out).toHaveProperty('supportRef');
    }
  });
});

describe('server-supplied keys never resolve through Object.prototype', () => {
  /*
   * Found by the Property 27 suite (task 5.4) and fixed here. Every table in this module is a
   * plain object literal, so a wire string that happens to be an `Object.prototype` key used to
   * resolve *through the prototype chain*: `CODE_COPY['constructor']` is truthy, step 1 read it
   * as a match, and the caller got `headline: undefined` with a function or an object as the
   * `detail`. Nothing forbidden reaches the screen, so this is not a Requirement 14.4 leak — it
   * is a shape failure, and a blank error panel, because `ErrorState` and every catch site
   * destructure `{headline, detail, …}` unconditionally.
   *
   * `Object.freeze` does not close it; only an own-property check does. One case per guarded
   * lookup, asserting the shape is complete and that the value falls through to the step it
   * should have fallen through to all along.
   */
  const PROTOTYPE_KEYS = ['constructor', '__proto__', 'toString', 'hasOwnProperty'];

  /** The contract `ErrorState` relies on: a headline you can render, a boolean you can branch on. */
  const expectCompleteShape = (out) => {
    expect(typeof out.headline).toBe('string');
    expect(out.headline.length).toBeGreaterThan(0);
    expect(typeof out.retryable).toBe('boolean');
    if (out.detail !== null) expect(typeof out.detail).toBe('string');
    if (out.action !== null) expect(typeof out.action.label).toBe('string');
  };

  it.each(PROTOTYPE_KEYS)('a backend code of "%s" falls to the HTTP category', (key) => {
    const out = translateError(
      apiError({ status: 500, data: routerBody(key), category: 'SERVER_ERROR' }),
      'dashboard',
    );
    expectCompleteShape(out);
    expect(out.headline).toBe(CATEGORY_COPY.SERVER_ERROR.headline);
    expect(out.detail).toBe(CATEGORY_COPY.SERVER_ERROR.detail);
  });

  it.each(PROTOTYPE_KEYS)('a bare `code` of "%s" with no category falls to the context default', (key) => {
    // `registryClient`'s `RegistryError` shape: a code and nothing `fromCategory` can read.
    const out = translateError({ code: key, message: 'x' }, 'builder');
    expectCompleteShape(out);
    expect(out.headline).toBe(CONTEXT_COPY.builder.headline);
  });

  it.each(PROTOTYPE_KEYS)('a refusal frame code of "%s" falls to the context default', (key) => {
    const out = translateError(
      { type: 'subscription_refused', channel: 'deployment.abc', code: key, reason: 'x' },
      'signal-trace',
    );
    expectCompleteShape(out);
    expect(out.headline).toBe(CONTEXT_COPY['signal-trace'].headline);
  });

  it.each(PROTOTYPE_KEYS)('a validation label of "%s" keeps the refusal copy and the generic sentence', (key) => {
    const out = translateError(
      apiError({
        status: 409,
        data: envelopeBody('PAPER_START_REFUSED', { validation: key }),
        category: 'CLIENT_ERROR',
      }),
      'paper-trading',
    );
    expectCompleteShape(out);
    expect(out.headline).toBe(CODE_COPY.PAPER_START_REFUSED.headline);
    expect(out.detail).toBe('A start check refused it, and nothing was created.');
    expect(Object.values(PAPER_START_REFUSAL_COPY)).not.toContain(out.detail);
  });

  it.each(PROTOTYPE_KEYS)('an ENTITLEMENT reason of "%s" falls back to the entitlement sentence', (key) => {
    const out = translateError(
      apiError({
        status: 403,
        data: envelopeBody('PAPER_START_REFUSED', { validation: 'ENTITLEMENT', reason: key }),
        category: 'AUTH_ERROR',
      }),
      'paper-trading',
    );
    expectCompleteShape(out);
    expect(out.detail).toBe(PAPER_START_REFUSAL_COPY.ENTITLEMENT);
  });

  it.each(PROTOTYPE_KEYS)('a context of "%s" falls to the default copy', (key) => {
    const out = translateError(new Error('x'), key);
    expectCompleteShape(out);
    expect(out.headline).toBe(CONTEXT_COPY.default.headline);
  });

  it('a guard check named "constructor" is counted as unrecognised, not rendered', () => {
    const out = translateError(
      apiError({
        status: 403,
        data: routerBody('SOME_UNTABLED_GUARD', 'blocked', { reasons: ['constructor: boom'] }),
        category: 'AUTH_ERROR',
      }),
      'live-trading',
    );
    expectCompleteShape(out);
    expect(out.headline).toBe('Blocked by a safety check');
    expect(out.detail).toBe(
      'Nothing was sent to the exchange. The safety checks that blocked it are recorded against the reference below.',
    );
  });
});

describe('supportRef', () => {
  it('comes from ApiError.requestId', () => {
    const out = translateError(
      apiError({ status: 503, data: routerBody('PORTFOLIO_FETCH_FAILED'), requestId: 'req-8f2c', category: 'SERVER_ERROR' }),
      'portfolio',
    );
    expect(out.supportRef).toBe('req-8f2c');
  });

  it("falls back to the server's own request_id in the envelope", () => {
    const out = translateError(
      apiError({ status: 503, data: envelopeBody('PAPER_READ_FAILED', {}, 'srv-77aa'), category: 'SERVER_ERROR' }),
      'paper-trading',
    );
    expect(out.supportRef).toBe('srv-77aa');
  });

  it('is null when neither is present', () => {
    expect(translateError(new Error('x'), 'dashboard').supportRef).toBeNull();
  });
});
