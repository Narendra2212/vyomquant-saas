/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Feature: vyomquant-ui-redesign, Property 27: Translated error copy never
 * leaks internals
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Task 5.4. **Validates: Requirements 14.4**
 *
 * Requirement 14.4 forbids raw HTTP status codes, exception class names and stack traces as
 * user-facing content. `translateError` is the only place an error becomes words, so this is
 * the one function where that rule can be established for the whole application — but only
 * over the whole input space, because the way copy leaks is never the error anyone thought
 * about. The errors generated here therefore carry, on every field a translator might be
 * tempted to read, exactly the content Requirement 14.4 forbids: `message` holds V8 stack
 * frames and Python tracebacks, `statusText` names an exception class, `url` is an internal
 * `/api/` endpoint, and `detail.reasons[]` entries carry a Python exception's own text in
 * their free-form half.
 *
 * WHAT MAKES THIS PROPERTY MEANINGFUL
 * -----------------------------------
 * The tables in `errorCopy.js` are authored clean, so on today's code this property passes
 * **without the `FORBIDDEN` scrubber ever firing** — the copy is safe because no branch reads
 * a message off the error, not because a guard keeps catching one that does. That is the
 * intended result, and it is worth stating: a version of this property that only passed
 * because `enforceNoLeak` kept intercepting output would be describing a much worse module.
 * `DEV` is stubbed true for the whole suite, so if the scrubber *did* fire it would throw and
 * this file would go red rather than quietly recording the degraded copy.
 *
 * The forbidden patterns are declared locally rather than imported from `errorCopy.js`. A
 * property that asks the subject for its own definition of "forbidden" is satisfied by
 * weakening that definition; these are read off Requirement 14.4 instead.
 *
 * The shape assertion is not decoration. `ErrorState` and every catch site destructure
 * `{headline, detail, retryable, action, supportRef}` unconditionally, so a returned
 * `undefined` headline is a blank error panel — a different failure from a leak, and equally
 * worth ruling out across the space.
 *
 * NOT REPEATED HERE: `errorCopy.test.js` drives `enforceNoLeak` directly for its throw-in-dev
 * and degrade-in-production branches, and asserts the tables are clean. This file is about
 * `translateError` never producing forbidden output in the first place.
 *
 * ---------------------------------------------------------------------------------------
 * FINDING — now CLOSED in `errorCopy.js` (task 5.3)
 * ---------------------------------------------------------------------------------------
 * `fromCode` read `CODE_COPY[resolved]` and `paperStartDetail` read
 * `PAPER_START_REFUSAL_COPY[validation]` with plain bracket access, so a server-supplied code
 * or validation label that happened to be an `Object.prototype` key resolved through the
 * prototype chain. `translateError({status: 500, category: 'SERVER_ERROR',
 * data: {detail: {error: 'constructor'}}}, 'dashboard')` returned `headline: undefined` and a
 * `detail` that was an object rather than a string. Nothing forbidden reached the screen, so it
 * was never a Property 27 leak — it was a shape failure, and it was the same
 * untrusted-server-key gap `semantic.js` guards against with `hasOwnProperty`. Backend codes
 * are `SCREAMING_SNAKE_CASE`, so the generators below cannot produce it and this property was
 * green either way.
 *
 * `errorCopy.js` now routes every table lookup keyed by a wire string — `CODE_COPY`,
 * `PAPER_START_REFUSAL_COPY`, `ENTITLEMENT_REASON_TO_CODE`, `GUARD_CHECK_COPY` and
 * `CONTEXT_COPY` — through an own-property check, so the reproduction above returns the
 * `SERVER_ERROR` category copy with a complete shape. `errorCopy.test.js` covers the four
 * `Object.prototype` keys at each of those lookups by example; this file is unchanged apart
 * from this note.
 */

import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';
import fc from 'fast-check';

import { ApiError } from '../../../src/apiClient';
import {
  CATEGORY_COPY,
  CODE_COPY,
  CONTEXT_COPY,
  GUARD_CHECK_COPY,
  PAPER_START_REFUSAL_COPY,
  translateError,
} from '../../../src/design/errorCopy.js';

/** design.md: "minimum 100 iterations per property". */
const RUNS = { numRuns: 300 };

// ══════════════════════════════════════════════════════════════════════════════════════
// REQUIREMENT 14.4, AS PATTERNS — declared here, not imported
// ══════════════════════════════════════════════════════════════════════════════════════

const LEAKS = [
  ['a bare HTTP status code', /\b[1-5]\d{2}\b/],
  ['an identifier ending in Error or Exception', /\b\w*(Error|Exception)\b/],
  ['a V8 stack frame', /\bat\s+\S+\s*\([^)]*:\d+:\d+\)/],
  ['a stack frame in minimal form', /\n\s+at\s/],
  ['a Python traceback marker', /\b(Traceback|File\s+"[^"]+",\s+line)/],
  ['an internal API URL', /https?:\/\/\S+\/api\//],
];

/** Every headline any authored table can produce. Nothing else may ever be returned. */
const AUTHORED_HEADLINES = new Set(
  [CODE_COPY, CATEGORY_COPY, CONTEXT_COPY].flatMap((table) =>
    Object.values(table).map((entry) => entry.headline),
  ),
);

// ══════════════════════════════════════════════════════════════════════════════════════
// THE SERVER'S OWN VOCABULARIES
// ══════════════════════════════════════════════════════════════════════════════════════

const KNOWN_CODES = Object.keys(CODE_COPY);
const GUARD_CHECKS = Object.keys(GUARD_CHECK_COPY);
const PAPER_VALIDATIONS = Object.keys(PAPER_START_REFUSAL_COPY);
const CONTEXTS = Object.keys(CONTEXT_COPY);

/** `core/websocket_auth.py`'s four `CHANNEL_REFUSED_*` codes. */
const REFUSAL_CODES = [
  'CHANNEL_FORBIDDEN',
  'CHANNEL_UNKNOWN',
  'CHANNEL_UNAUTHENTICATED',
  'CHANNEL_OWNER_UNRESOLVED',
];

/** `entitlement_resolver.EntitlementReason`, as it arrives in `PAPER_START_REFUSED`. */
const ENTITLEMENT_REASONS = [
  'NOT_SUBSCRIBED',
  'EXPIRED',
  'LISTING_UNAVAILABLE',
  'SUBSCRIPTION_SUSPENDED',
];

// ══════════════════════════════════════════════════════════════════════════════════════
// GENERATORS — leaky content first, then the wire shapes that carry it
// ══════════════════════════════════════════════════════════════════════════════════════

const identifier = fc.stringMatching(/^[a-z][a-zA-Z0-9_]{2,12}$/);

/** `AxiosError`, `KeyError`, `ValueException` — the class names Requirement 14.4 names. */
const exceptionName = fc
  .tuple(fc.stringMatching(/^[A-Z][a-z]{2,10}$/), fc.constantFrom('Error', 'Exception'))
  .map(([stem, suffix]) => `${stem}${suffix}`);

/** Every HTTP status, plus the statuses `ApiError`'s own category derivation branches on. */
const status = fc.oneof(
  fc.integer({ min: 100, max: 599 }),
  fc.constantFrom(undefined, 0, 400, 401, 403, 404, 409, 422, 429, 500, 502, 503, 504),
);

/** Axios's own message, which is how a status reaches the screen today. */
const bareStatusMessage = fc
  .integer({ min: 100, max: 599 })
  .map((code) => `Request failed with status code ${code}`);

const v8Frame = fc
  .tuple(identifier, identifier, fc.integer({ min: 1, max: 9999 }), fc.integer({ min: 1, max: 200 }))
  .map(([fn, file, line, column]) => `    at ${fn} (/src/${file}.js:${line}:${column})`);

const pythonTraceback = fc
  .tuple(identifier, fc.integer({ min: 1, max: 9999 }), identifier, exceptionName, identifier)
  .map(
    ([file, line, fn, exception, detail]) =>
      `Traceback (most recent call last):\n  File "${file}.py", line ${line}, in ${fn}\n${exception}: ${detail}`,
  );

const internalUrl = fc
  .tuple(identifier, identifier)
  .map(([host, path]) => `https://${host}.vyomquant.io/api/${path}`);

/** Anything Requirement 14.4 forbids, in the shapes a real failure carries it. */
const leakyText = fc.oneof(
  bareStatusMessage,
  pythonTraceback,
  internalUrl,
  exceptionName.map((name) => `${name}: the read did not complete`),
  fc.tuple(bareStatusMessage, v8Frame).map(([message, frame]) => `${message}\n${frame}`),
  fc.tuple(exceptionName, v8Frame, v8Frame).map(([name, a, b]) => `${name}: boom\n${a}\n${b}`),
);

/**
 * A code the backend could send that no table holds.
 *
 * `SCREAMING_SNAKE_CASE` by construction, which is the shape every backend code actually
 * takes — and which cannot collide with an `Object.prototype` key (see the finding above).
 */
const untabledCode = fc
  .stringMatching(/^[A-Z][A-Z_]{3,20}$/)
  .filter((code) => !KNOWN_CODES.includes(code));

const anyCode = fc.oneof(fc.constantFrom(...KNOWN_CODES), untabledCode);

/** `apiClient` generates one per request; it is an opaque id, not a status or a path. */
const requestId = fc.oneof(
  fc.stringMatching(/^req-[0-9a-f]{8}$/),
  fc.constant(null),
  fc.constant(undefined),
);

/**
 * `execution_guard.py`'s `blocked_reasons` entry: `f"{check_name}: {result.message}"`.
 *
 * The message half is free-form and can carry a Python exception's own text, which is the
 * whole reason `errorCopy` reads only the name half.
 */
const blockedReason = fc
  .tuple(fc.oneof(fc.constantFrom(...GUARD_CHECKS), identifier), leakyText)
  .map(([check, message]) => `${check}: ${message}`);

/** `PAPER_START_REFUSED`'s `details`, including labels no table holds. */
const paperDetails = fc.oneof(
  fc.record({ validation: fc.constantFrom(...PAPER_VALIDATIONS) }),
  fc.record({
    validation: fc.constant('ENTITLEMENT'),
    reason: fc.constantFrom(...ENTITLEMENT_REASONS),
  }),
  fc.record({ validation: fc.constant('ENTITLEMENT') }),
  // An unrecognised label must fall to the generic sentence, never be echoed.
  fc.record({ validation: fc.oneof(untabledCode, exceptionName) }),
  fc.constant({}),
);

/** `backend_app/routers/*`: FastAPI's `HTTPException` path. */
const routerBody = fc.record({
  detail: fc.record({ error: anyCode, message: leakyText }),
});

/** The same, carrying the safety guard's reasons. The code is untabled so step 2 is reached. */
const reasonsBody = fc.record({
  detail: fc.record({
    error: untabledCode,
    message: leakyText,
    reasons: fc.array(blockedReason, { minLength: 1, maxLength: 5 }),
  }),
});

/** `marketplace/errors.py`'s single envelope. */
const envelopeBody = fc.record({
  error: fc.record({
    code: fc.oneof(anyCode, fc.constant('PAPER_START_REFUSED')),
    message: leakyText,
    details: paperDetails,
  }),
  request_id: fc.stringMatching(/^srv-[0-9a-f]{6}$/),
});

const anyBody = fc.oneof(
  fc.constant(null),
  routerBody,
  reasonsBody,
  envelopeBody,
  // `publicGet`'s shape, and FastAPI's plain-string `detail`.
  fc.record({ error: anyCode }),
  fc.record({ code: anyCode, message: leakyText }),
  fc.record({ detail: leakyText }),
);

/**
 * A real `ApiError`, not a look-alike, so the class's own `status → category` derivation is
 * what the translation is read against.
 *
 * `statusText`, `url` and `message` are all seeded with forbidden content on purpose: they are
 * the fields a future `detail: err.message` would reach for.
 */
const apiErrorArb = fc
  .record({ status, message: leakyText, data: anyBody, requestId })
  .map(
    ({ status: httpStatus, message, data, requestId: id }) =>
      new ApiError(message, {
        status: httpStatus,
        statusText: 'Internal Server Error',
        url: 'https://api.vyomquant.io/api/portfolio/summary',
        method: 'get',
        data,
        requestId: id,
      }),
  );

/** The request never reached a response: no status, so `ApiError` files it as NETWORK_ERROR. */
const networkFailureArb = fc
  .record({
    message: fc.constantFrom(
      'Network Error',
      'AxiosError: Network Error',
      'timeout of 30000ms exceeded',
      'ECONNREFUSED 127.0.0.1:8000',
    ),
    requestId,
  })
  .map(
    ({ message, requestId: id }) =>
      new ApiError(message, {
        status: undefined,
        url: 'https://api.vyomquant.io/api/dashboard/summary',
        method: 'get',
        data: null,
        requestId: id,
      }),
  );

/** A `subscription_refused` frame, across all four refusal codes and beyond them. */
const refusalFrameArb = fc.record({
  type: fc.constant('subscription_refused'),
  channel: fc.stringMatching(/^[a-z]{3,10}\.[a-z0-9]{3,10}$/),
  code: fc.oneof(fc.constantFrom(...REFUSAL_CODES), untabledCode),
  reason: leakyText,
});

/** `registryClient`'s `RegistryError` and the builder's `saveIssue` records: a bare `code`. */
const codeCarrierArb = fc.record({ code: anyCode, message: leakyText });

/** Whatever else a `catch` can receive. Step 5's input. */
const thrownArb = fc.oneof(
  leakyText.map((message) => new Error(message)),
  leakyText.map((message) => new TypeError(message)),
  leakyText,
  codeCarrierArb,
  fc.constantFrom(null, undefined, 0, false, {}),
);

const anyError = fc.oneof(apiErrorArb, networkFailureArb, refusalFrameArb, thrownArb);

const anyContext = fc.oneof(
  fc.constantFrom(...CONTEXTS),
  fc.constant(undefined),
  fc.constantFrom('not-a-page', ''),
);

// ══════════════════════════════════════════════════════════════════════════════════════
// PROPERTY 27
// ══════════════════════════════════════════════════════════════════════════════════════

describe('Feature: vyomquant-ui-redesign, Property 27: Translated error copy never leaks internals', () => {
  beforeAll(() => {
    // With `DEV` true, `enforceNoLeak` throws rather than degrading. The property is therefore
    // asserting the stronger claim: not "the output is clean", but "the output is clean and
    // the scrubber never had to make it so".
    vi.stubEnv('DEV', true);
  });

  afterAll(() => {
    vi.unstubAllEnvs();
  });

  it('generates errors that really do carry forbidden content', () => {
    // The negative control. A property asserting "no leak" is worth nothing if the generated
    // errors were harmless to begin with, and that failure mode is silent — the suite stays
    // green while proving less each year. So the input space is checked to be adversarial:
    // every message these errors carry trips at least one Requirement 14.4 pattern.
    fc.assert(
      fc.property(leakyText, (text) => {
        expect(
          LEAKS.some(([, pattern]) => pattern.test(text)),
          `generated a harmless message: ${JSON.stringify(text)}`,
        ).toBe(true);
      }),
      RUNS,
    );

    fc.assert(
      fc.property(blockedReason, (reason) => {
        expect(LEAKS.some(([, pattern]) => pattern.test(reason))).toBe(true);
      }),
      RUNS,
    );
  });

  it('produces no forbidden content on any user-facing surface, for any error and context', () => {
    fc.assert(
      fc.property(anyError, anyContext, (error, context) => {
        const out = translateError(error, context);

        const surfaces = [
          ['headline', out.headline],
          ['detail', out.detail],
          ['action.label', out.action?.label ?? null],
        ];

        for (const [where, text] of surfaces) {
          if (text === null || text === undefined) continue;
          expect(text, `${where} is not a string`).toBeTypeOf('string');
          for (const [what, pattern] of LEAKS) {
            expect(pattern.test(text), `${where} leaked ${what}: ${JSON.stringify(text)}`).toBe(
              false,
            );
          }
        }
      }),
      RUNS,
    );
  });

  it('always returns the complete shape a caller destructures', () => {
    fc.assert(
      fc.property(anyError, anyContext, (error, context) => {
        const out = translateError(error, context);

        expect(Object.keys(out).sort()).toEqual([
          'action',
          'detail',
          'headline',
          'retryable',
          'supportRef',
        ]);

        expect(out.headline).toBeTypeOf('string');
        expect(out.headline.length).toBeGreaterThan(0);
        expect(out.retryable).toBeTypeOf('boolean');

        if (out.detail !== null) expect(out.detail).toBeTypeOf('string');
        if (out.action !== null) {
          expect(out.action).toBeTypeOf('object');
          expect(out.action.label).toBeTypeOf('string');
        }
        if (out.supportRef !== null) expect(out.supportRef).toBeTypeOf('string');
      }),
      RUNS,
    );
  });

  it('returns a headline from an authored table, never one derived from the error', () => {
    // The structural reason no stack reaches the screen: there is no branch that reads a
    // message off the error, so every headline is one somebody wrote. A future
    // `headline: err.message` fails here even if the message happens to look harmless.
    fc.assert(
      fc.property(anyError, anyContext, (error, context) => {
        const { headline } = translateError(error, context);
        expect(AUTHORED_HEADLINES.has(headline), `unauthored headline: ${headline}`).toBe(true);
      }),
      RUNS,
    );
  });
});
