/**
 * libraryApi.test.js — the contract of `src/api/modules/library.js`.
 *
 * Three things are asserted, and the first is the one that keeps the other two honest:
 *
 *  1. **Every** method on `libraryApi` has a case here. The expectation table below is compared
 *     against a reflective walk of the exported object, so a method added to the module without
 *     a case fails this suite instead of shipping uncovered.
 *  2. Each method calls the shared `get` / `post` / `patch` / `publicGet` from `../../apiClient`
 *     with the exact path, and with nothing else — no second client call, no stray verb.
 *  3. The module's **source text** builds no transport of its own (Requirement 20.2).
 *
 * `../../apiClient` is mocked here and nowhere near a production path: the module under test
 * imports the real shared client, and only this test replaces it.
 *
 * Requirements: 20.2, 29.2
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import {
  REPO_ROOT,
  assertTravelsOnlyTheSharedClient,
  collectMethodPaths,
  methodAt,
} from './apiSourceContract';

// The shared client, replaced wholesale. `library.js` resolves `'../../apiClient'` to the same
// module id this path resolves to from `__tests__/`, so the module under test receives these.
vi.mock('../../../apiClient', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  publicGet: vi.fn(),
  getMetrics: vi.fn(),
  logout: vi.fn(),
  clearApiCache: vi.fn(),
  isAuthenticated: vi.fn(),
  getToken: vi.fn(),
  testConnection: vi.fn(),
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
  },
}));

import * as client from '../../../apiClient';
import { libraryApi, libraryEndpoints } from '../library';

/** The six shared verbs. Every case names one; the other five must stay untouched. */
const VERBS = ['get', 'post', 'put', 'del', 'patch', 'publicGet'];

/**
 * An identifier that has to be percent-encoded to survive a path segment. Using it everywhere
 * an id is taken means each case also proves the method encodes rather than interpolates raw.
 */
const ID = 'lib/1 a';
const ENC = 'lib%2F1%20a';

const SUBMISSION_BODY = { strategy_id: 's-1', backtest_ids: ['b1', 'b2', 'b3'] };
const RATING_BODY = { rating: 5, review_text: 'solid' };
const SETTINGS_BODY = { source_cloning_enabled: true };
const BROWSE_PARAMS = { page: 2, limit: 20, sort: 'clones' };

/**
 * One row per method: the arguments to call it with, the shared verb it must reach, and the
 * exact argument list that verb must receive.
 *
 * @type {Record<string, {args: any[], verb: string, call: any[]}>}
 */
const EXPECTED = {
  browse: { args: [BROWSE_PARAMS], verb: 'get', call: ['/api/library', { params: BROWSE_PARAMS }] },
  browsePublic: { args: [BROWSE_PARAMS], verb: 'publicGet', call: ['/api/library', BROWSE_PARAMS] },

  featured: { args: [], verb: 'get', call: ['/api/library/featured?limit=3'] },
  featuredPublic: { args: [7], verb: 'publicGet', call: ['/api/library/featured?limit=7'] },
  trending: { args: [], verb: 'get', call: ['/api/library/trending?limit=10'] },
  trendingPublic: { args: [4], verb: 'publicGet', call: ['/api/library/trending?limit=4'] },

  categories: { args: [], verb: 'get', call: ['/api/library/categories'] },
  categoriesPublic: { args: [], verb: 'publicGet', call: ['/api/library/categories'] },

  detail: { args: [ID], verb: 'get', call: [`/api/library/${ENC}`] },
  detailPublic: { args: [ID], verb: 'publicGet', call: [`/api/library/${ENC}`] },

  myStrategies: { args: [], verb: 'get', call: ['/api/library/my-strategies'] },

  'submissions.create': {
    args: [SUBMISSION_BODY],
    verb: 'post',
    call: ['/api/library/submissions', SUBMISSION_BODY],
  },
  'submissions.get': {
    args: [ID],
    verb: 'get',
    call: [`/api/library/submissions/${ENC}`],
  },
  'submissions.priceRange': {
    args: [ID, 'USD'],
    verb: 'post',
    call: [`/api/library/submissions/${ENC}/price-range`, { currency: 'USD' }],
  },
  // Money crosses the wire as an integer number of Minor_Units: 1998 is $19.98, and no
  // major-unit float travels this path.
  'submissions.setPrice': {
    args: [ID, 1998, 'USD'],
    verb: 'post',
    call: [`/api/library/submissions/${ENC}/price`, { price_minor: 1998, currency: 'USD' }],
  },

  // `state` is repeated (`?state=A&state=B`), which is what FastAPI's
  // `Optional[List[str]] = Query(None)` parses; axios' `state[]=A` default would not.
  'admin.listSubmissions': {
    args: [{ state: ['SUBMITTED', 'UNDER_REVIEW'], page: 2, pageSize: 50 }],
    verb: 'get',
    call: [
      '/api/library/admin/submissions?state=SUBMITTED&state=UNDER_REVIEW&page=2&page_size=50',
    ],
  },
  'admin.getSubmission': {
    args: [ID],
    verb: 'get',
    call: [`/api/library/admin/submissions/${ENC}`],
  },
  'admin.approve': {
    args: [ID],
    verb: 'post',
    call: [`/api/library/admin/submissions/${ENC}/approve`],
  },
  'admin.reject': {
    args: [ID, 'thin evidence'],
    verb: 'post',
    call: [`/api/library/admin/submissions/${ENC}/reject`, { reason: 'thin evidence' }],
  },
  'admin.publish': {
    args: [ID],
    verb: 'post',
    call: [`/api/library/admin/submissions/${ENC}/publish`],
  },
  'admin.suspend': {
    args: [ID],
    verb: 'post',
    call: [`/api/library/admin/submissions/${ENC}/suspend`],
  },
  'admin.unpublish': {
    args: [ID],
    verb: 'post',
    call: [`/api/library/admin/submissions/${ENC}/unpublish`],
  },

  checkout: {
    args: [ID],
    verb: 'post',
    call: [`/api/library/${ENC}/checkout`, { currency: 'USD' }],
  },
  subscriptionStatus: { args: [ID], verb: 'get', call: [`/api/library/${ENC}/subscribe`] },

  // Both subscription operations take no body — the handlers declare none.
  cancelSubscription: {
    args: [ID],
    verb: 'post',
    call: [`/api/library/subscriptions/${ENC}/cancel`],
  },
  renewSubscription: {
    args: [ID],
    verb: 'post',
    call: [`/api/library/subscriptions/${ENC}/renew`],
  },

  clone: { args: [ID], verb: 'post', call: [`/api/library/${ENC}/clone`] },
  rate: { args: [ID, RATING_BODY], verb: 'post', call: [`/api/library/${ENC}/rate`, RATING_BODY] },

  // A PATCH, not a PUT: the route is `@literal_router.patch("/{library_id}/settings")`.
  settings: {
    args: [ID, SETTINGS_BODY],
    verb: 'patch',
    call: [`/api/library/${ENC}/settings`, SETTINGS_BODY],
  },

  creatorAnalytics: { args: [], verb: 'get', call: ['/api/library/creator/analytics'] },
  subscriberAnalytics: { args: [], verb: 'get', call: ['/api/library/subscriber/analytics'] },
};

beforeEach(() => {
  for (const verb of VERBS) client[verb].mockReset();
});

describe('libraryApi — every method is covered', () => {
  it('exposes exactly the methods this file has expectations for', () => {
    // The guard that makes the rest of this suite meaningful: add a method to library.js
    // without adding a row to EXPECTED and this fails, naming the uncovered method.
    expect(collectMethodPaths(libraryApi)).toEqual(Object.keys(EXPECTED).sort());
  });

  it('re-exports the same object as libraryEndpoints for the legacy callers', () => {
    expect(libraryEndpoints).toBe(libraryApi);
  });
});

describe('libraryApi — each method calls the shared client with the expected path', () => {
  for (const [name, { args, verb, call }] of Object.entries(EXPECTED)) {
    it(`${name} → ${verb}(${JSON.stringify(call[0])})`, () => {
      methodAt(libraryApi, name)(...args);

      expect(client[verb]).toHaveBeenCalledTimes(1);
      expect(client[verb]).toHaveBeenCalledWith(...call);

      // Nothing else was touched — no second request, no fallback verb.
      const total = VERBS.reduce((sum, v) => sum + client[v].mock.calls.length, 0);
      expect(total, `${name} must make exactly one shared-client call`).toBe(1);
    });
  }
});

describe('libraryApi — default arguments', () => {
  it('defaults featured to limit=3 and trending to limit=10, and checkout to USD', () => {
    libraryApi.featured();
    libraryApi.trending();
    expect(client.get).toHaveBeenNthCalledWith(1, '/api/library/featured?limit=3');
    expect(client.get).toHaveBeenNthCalledWith(2, '/api/library/trending?limit=10');

    libraryApi.checkout(ID, 'INR');
    expect(client.post).toHaveBeenCalledWith(`/api/library/${ENC}/checkout`, {
      currency: 'INR',
    });
  });

  it('omits an empty admin submission query rather than sending a blank one', () => {
    libraryApi.admin.listSubmissions();
    expect(client.get).toHaveBeenCalledWith('/api/library/admin/submissions');

    client.get.mockReset();
    libraryApi.admin.listSubmissions({ state: 'SUBMITTED' });
    expect(client.get).toHaveBeenCalledWith(
      '/api/library/admin/submissions?state=SUBMITTED',
    );
  });
});

describe('libraryApi — submissions.list and submissions.withdraw are absent', () => {
  // Both are absent BECAUSE THE BACKEND ROUTE IS ABSENT. `backend_app/routers/library.py`
  // declares exactly one owner-scoped submission read, `GET /submissions/{submission_id}`,
  // and no withdrawal verb at all — `SubmissionAction` covers approve/reject/publish/
  // suspend/unpublish, every one of them admin-only. A client method pointing at a path the
  // server does not serve is a call that fails at runtime, so neither is written.
  //
  // The assertion is a biconditional against the router source, not a flat "is undefined":
  // the day `GET /api/library/submissions` or a withdrawal route lands, this test is the
  // thing that says the client method is still missing.
  const ROUTER = path.join(REPO_ROOT, 'backend_app', 'routers', 'library.py');

  it('has no client method for either, and gains one only when the route does', () => {
    expect(fs.existsSync(ROUTER), `expected the router at ${ROUTER}`).toBe(true);
    const router = fs.readFileSync(ROUTER, 'utf8');

    // `GET /submissions` exactly — `/submissions/{submission_id}` is a different route and is
    // deliberately not matched here.
    const hasListRoute = /@(?:literal_)?router\.get\(\s*["']\/submissions["']/.test(router);
    const hasWithdrawRoute = /@(?:literal_)?router\.(?:post|delete|patch|put)\(\s*["']\/submissions\/\{submission_id\}\/withdraw["']/.test(
      router,
    );

    expect(
      typeof libraryApi.submissions.list === 'function',
      hasListRoute
        ? 'GET /api/library/submissions now exists — add libraryApi.submissions.list'
        : 'libraryApi.submissions.list must stay absent while the backend has no GET /api/library/submissions',
    ).toBe(hasListRoute);

    expect(
      typeof libraryApi.submissions.withdraw === 'function',
      hasWithdrawRoute
        ? 'a submission withdrawal route now exists — add libraryApi.submissions.withdraw'
        : 'libraryApi.submissions.withdraw must stay absent while the backend has no withdrawal route',
    ).toBe(hasWithdrawRoute);
  });
});

describe('libraryApi — no transport of its own', () => {
  it('builds no client, base URL or host of its own', () => {
    assertTravelsOnlyTheSharedClient('library.js');
  });
});

describe('api.library is reachable from src/api/index.js', () => {
  it('registers the same libraryApi object, alongside api.paper.sessions', async () => {
    const { api } = await import('../../index');
    expect(api.library).toBe(libraryApi);
    expect(api.paper.sessions).toBeTypeOf('object');
  });
});
