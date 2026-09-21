/**
 * `src/design/errorLine.js` — the one-line form of `translateError`'s copy.
 *
 * Task 10.8. Requirements 14.3 and 14.4.
 *
 * `errorLine` is the drop-in that replaced `extractErrorMessage(err, fallback)` at the fourteen
 * call sites that hold a string rather than render a panel: `PaperTrading`'s toasts and its
 * `PanelBody` `message`, and the three engine contexts' `message` fields. The two things worth
 * pinning are the two things a caller depends on:
 *
 *   1. It is TOTAL. Same inputs `translateError` is total over — an `ApiError`, a bare `Error`,
 *      a string, `null`, `undefined` — all produce a non-empty sentence, so no call site can
 *      render a blank toast or an empty notice.
 *   2. It NEVER carries the error's own text. That is the whole point of deleting
 *      `extractErrorMessage`: a message, a `detail` dump and a stack must not survive the trip.
 *
 * The joining rule itself is checked against the authored tables rather than against literals,
 * so re-wording an entry in `errorCopy.js` cannot leave this test asserting stale copy.
 */

import { describe, it, expect } from 'vitest';

import { CATEGORY_COPY, CODE_COPY, CONTEXT_COPY, containsForbidden } from '../../../src/design/errorCopy.js';
import { errorLine } from '../../../src/design/errorLine.js';

/** An `ApiError`-shaped object: the fields `translateError` reads, and no others. */
const apiError = ({ status = 500, data = null, category = 'SERVER_ERROR' }) => ({
  status,
  data,
  requestId: 'req-abc',
  category,
  isRetryable: () => category === 'SERVER_ERROR',
});

describe('errorLine: the joining rule', () => {
  it('is the headline, a full stop, then the detail', () => {
    const entry = CODE_COPY.PAPER_READ_FAILED;
    const line = errorLine(apiError({ data: { detail: { error: 'PAPER_READ_FAILED' } } }), 'paper-trading');

    expect(line).toBe(`${entry.headline}. ${entry.detail}`);
  });

  it('joins without a dangling full stop, on every entry the tables can resolve to', () => {
    // The `detail`-less arm of the join is unreachable on today's copy and is stated as such
    // rather than faked: `PAPER_START_REFUSED` is the only entry authored with `detail: null`,
    // and `fromCode` fills it from `details.validation` before returning — so every resolution
    // carries both fields. Asserting that is the useful check, because it is what would break
    // if a future entry were added with no detail and the `?` were dropped.
    const resolvable = [
      ...Object.keys(CODE_COPY).map((code) => apiError({ data: { detail: { error: code } } })),
      ...Object.keys(CONTEXT_COPY).map(() => new Error('boom')),
    ];

    for (const error of resolvable) {
      const line = errorLine(error, 'paper-trading');
      // No doubled stop from a headline that already ended in one, and no stop left standing on
      // its own from a detail that resolved to an empty string rather than `null`.
      expect(line, line).not.toContain('..');
      expect(line, line).not.toMatch(/\.\s+\./);
      expect(line, line).not.toMatch(/\.\s+$/);
    }
  });

  it('takes the context the caller named, not a generic one', () => {
    // Step 5 is what a bare `throw new Error(...)` from the engine contexts lands on, and the
    // context is the only thing that makes it specific.
    expect(errorLine(new Error('boom'), 'builder')).toBe(
      `${CONTEXT_COPY.builder.headline}. ${CONTEXT_COPY.builder.detail}`,
    );
    expect(errorLine(new Error('boom'), 'paper-trading')).toBe(
      `${CONTEXT_COPY['paper-trading'].headline}. ${CONTEXT_COPY['paper-trading'].detail}`,
    );
    expect(errorLine(new Error('boom'), 'strategies')).toBe(
      `${CONTEXT_COPY.strategies.headline}. ${CONTEXT_COPY.strategies.detail}`,
    );
  });

  it('falls to the HTTP category when the server named no code', () => {
    expect(errorLine(apiError({ status: 429, category: 'CLIENT_ERROR' }), 'paper-trading')).toBe(
      `${CATEGORY_COPY.RATE_LIMIT.headline}. ${CATEGORY_COPY.RATE_LIMIT.detail}`,
    );
  });
});

describe('errorLine: totality', () => {
  it('produces a non-empty sentence for everything a catch can hold', () => {
    const caught = [
      undefined,
      null,
      '',
      'a bare string',
      new Error('boom'),
      new TypeError('bad'),
      { message: 'plain object' },
      apiError({ status: 503, category: 'SERVER_ERROR' }),
      apiError({ status: 401, category: 'AUTH_ERROR' }),
      { type: 'subscription_refused', channel: 'paper.1', code: 'CHANNEL_FORBIDDEN' },
    ];

    for (const error of caught) {
      const line = errorLine(error, 'paper-trading');
      expect(typeof line, `${String(error)} did not produce a string`).toBe('string');
      expect(line.trim().length, `${String(error)} produced an empty line`).toBeGreaterThan(0);
    }
  });
});

describe('errorLine: nothing off the error reaches the string', () => {
  it('drops the axios message, the FastAPI dump and the traceback', () => {
    const leaky = apiError({
      status: 500,
      category: 'SERVER_ERROR',
      data: {
        detail: [{ loc: ['body', 'qty'], msg: 'value is not a valid float', type: 'type_error' }],
        message: 'Request failed with status code 500',
        error: 'Traceback (most recent call last):\n  File "paper.py", line 8, in read',
      },
    });
    leaky.message = 'AxiosError: timeout of 30000ms exceeded\n    at settle (/src/xhr.js:12:9)';

    const line = errorLine(leaky, 'paper-trading');

    expect(line).toBe(`${CATEGORY_COPY.SERVER_ERROR.headline}. ${CATEGORY_COPY.SERVER_ERROR.detail}`);
    expect(line).not.toContain('AxiosError');
    expect(line).not.toContain('500');
    expect(line).not.toContain('Traceback');
    expect(line).not.toContain('value is not a valid float');
    expect(containsForbidden(line)).toBe(false);
  });

  it('never returns a caller-supplied fallback, because there is no longer one to supply', () => {
    // `extractErrorMessage(err, 'Your strategy list could not be read.')` took its wording from
    // the call site. Nothing about `errorLine`'s signature admits that, which is what makes the
    // fourteen migrated sites unable to drift apart.
    expect(errorLine.length).toBe(2);
    expect(errorLine(new Error('x'), 'strategies')).not.toContain('x');
  });
});
