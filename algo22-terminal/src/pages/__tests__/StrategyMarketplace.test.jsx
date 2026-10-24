/**
 * StrategyMarketplace.test.jsx — the Marketplace catalogue and Listing detail surface.
 *
 * Spec: marketplace-subscriptions-paper-trading task 32.9 (the page it covers is task 32.6).
 * Requirements 6.6, 20.2, 20.9, 20.10, 22.8, 28.1, 28.5, 29.2.
 *
 * WHAT THIS SUITE IS FOR
 * ----------------------
 * Four properties of the page, each of which was a real defect before task 32.6:
 *
 *  1. **No browser dialog, ever.** `window.alert` is spied on and asserted to have received
 *     **zero** calls on every outcome path — clone success, clone failure, subscribe success
 *     (both the redirect and the no-redirect shapes) and subscribe failure — and
 *     `window.showToast`, the mechanism `AppShell` installs, is asserted to be what received
 *     the outcome instead (Requirement 20.10).
 *  2. **Every backend call travels `api.library`.** `../../api` is mocked wholesale and the
 *     *whole* mock is walked after each render: the set of methods that were actually called
 *     must be a subset of `library.*`. A call through any other namespace, through `fetch`, or
 *     through `XMLHttpRequest` fails the assertion rather than passing unnoticed
 *     (Requirement 20.2). The authenticated / anonymous split is the token check choosing
 *     between the paired `*` and `*Public` methods, so both sides are covered.
 *  3. **Owner-supplied text is text.** `<img src=x onerror=alert(1)>` is fed through `name`,
 *     `description`, `tags` and a review's `review_text`, and `container.querySelector('img')`
 *     is `null` — the payload is displayed, not executed (Requirement 22.8).
 *  4. **The three environment sections are separated, and an empty one is absent.**
 *     `[data-environment="BACKTEST"|"PAPER"|"LIVE"]` are three disjoint sibling elements; a
 *     section whose figures the Listing does not carry renders **nothing at all** rather than a
 *     grid of zeros; and exactly one of them — `BACKTEST` — carries
 *     `[data-testid="historical-results-statement"]` (Requirement 6.6).
 *
 * HOW EACH ASSERTION IS PROVED CAPABLE OF FAILING
 * ----------------------------------------------
 * An assertion that cannot fail is worse than no assertion, so each of the four carries a
 * paired control in this file:
 *
 *  * the `alert` spy is shown to record a real `window.alert(…)` call (`describe('controls')`),
 *    and the source-text rule that forbids `alert(` in the page is shown to flag a fixture that
 *    contains one;
 *  * the XSS query is shown to find an `<img>` when the same payload is injected through
 *    `dangerouslySetInnerHTML` into an identical container — so `querySelector('img') === null`
 *    is a fact about the page, not about the query;
 *  * the section-omission assertion is paired: the *same* selector is asserted present when the
 *    Listing carries that environment's figures and absent when it does not, in the same suite,
 *    with the section count going 1 → 3;
 *  * the statement assertion counts the statement across the whole document (exactly 1), so
 *    both a missing statement and a statement leaking into `PAPER` / `LIVE` fail.
 *
 * The mock of `../../api` lives only here. The page under test imports the real module in every
 * other context.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// The same comment-stripper the API module contract tests use, rather than a second copy of it
// here that could drift away from theirs.
import { stripComments } from '../../api/modules/__tests__/apiSourceContract';

// ─────────────────────────────────────────────────────────────────────────────────────────
// The mocked API surface
//
// Every namespace `src/api/index.js` exports is present, and every method is a bare `vi.fn()`.
// The point of mocking the *whole* surface rather than only `library` is assertion (2): after a
// render, `calledPaths(mockApi)` is the exact set of API methods the page reached, so a call
// through `api.paper`, `api.strategies` or anything else shows up as a path that is not
// `library.*` instead of quietly working.
// ─────────────────────────────────────────────────────────────────────────────────────────

const { mockApi } = vi.hoisted(() => {
  /** An object of `vi.fn()`s, one per name. */
  const fns = (names) => Object.fromEntries(names.map((n) => [n, vi.fn()]));

  const library = {
    ...fns([
      'browse', 'browsePublic',
      'featured', 'featuredPublic',
      'trending', 'trendingPublic',
      'categories', 'categoriesPublic',
      'detail', 'detailPublic',
      'myStrategies',
      'checkout', 'subscriptionStatus',
      'cancelSubscription', 'renewSubscription',
      'clone', 'deploy', 'rate', 'settings',
      'creatorAnalytics', 'subscriberAnalytics',
    ]),
    submissions: fns(['create', 'get', 'priceRange', 'setPrice']),
    admin: fns([
      'listSubmissions', 'getSubmission', 'approve', 'reject',
      'publish', 'suspend', 'unpublish',
    ]),
  };

  const api = {
    library,
    // The seventeen other namespaces. None of them may be touched by this page.
    assets: fns(['list', 'get']),
    auth: fns(['login', 'logout', 'me']),
    dataQuality: fns(['get']),
    exchange: fns(['list', 'listConnections']),
    market: fns(['candles', 'ticker']),
    orders: fns(['list', 'place']),
    strategies: fns(['list', 'get', 'deploy']),
    portfolio: fns(['summary']),
    paper: { ...fns(['getAccount', 'getSummary']), sessions: fns(['create', 'list', 'equity']) },
    risk: fns(['get']),
    billing: fns(['plans']),
    user: fns(['me']),
    referral: fns(['stats']),
    dashboard: fns(['summary']),
    health: fns(['check']),
    support: fns(['tickets']),
    notifications: fns(['list']),
  };

  return { mockApi: api };
});

// `isAuthenticated` IS PART OF THIS MOCK'S OBLIGATION, and leaving it out did not fail these
// tests -- it failed the RUN. All 155 test files passed and the step still exited non-zero.
//
// `StrategyMarketplace` renders entitlement-gated controls through `useEntitlements`, which
// does `import { api, isAuthenticated } from '../api'` and opens its `load()` with
// `if (!isAuthenticated())` (`src/hooks/useEntitlements.js:99`). A `vi.mock` factory replaces
// the WHOLE module, so an export it omits does not fall through to the real one -- touching it
// throws `[vitest] No "isAuthenticated" export is defined on the "../../api" mock`. That
// happens inside the hook's async `load`, so it surfaced as an UNHANDLED ERROR rather than a
// failed assertion, and vitest exits non-zero on those even when every test passes.
//
// Answered from `sessionStorage.token` rather than hardcoded, because that is the store
// `signIn()` / `signOut()` below actually drive, and section 1 asserts the authenticated and
// anonymous halves of each paired `library.*` / `library.*Public` method. A flat `() => false`
// would make the hook permanently anonymous while the transport tests still signed in, which
// is a mock disagreeing with its own fixture. This is the same shape
// `tests/unit/lib/singleSocket.test.jsx` uses, for the same reason.
vi.mock('../../api', () => ({
  default: mockApi,
  api: mockApi,
  endpoints: mockApi,
  isAuthenticated: () => !!sessionStorage.getItem('token'),
}));

import StrategyMarketplace from '../StrategyMarketplace';

// ─────────────────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────────────────

/**
 * Every mocked API method that was actually called, as dotted paths.
 *
 * This is what makes "every backend call travels `api.library`" an assertion about the page
 * rather than about the three methods a test happened to stub.
 */
const calledPaths = (target, prefix = '') => {
  const found = [];
  for (const [key, value] of Object.entries(target)) {
    const dotted = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'function') {
      if (value.mock && value.mock.calls.length > 0) found.push(dotted);
    } else if (value && typeof value === 'object') {
      found.push(...calledPaths(value, dotted));
    }
  }
  return found.sort();
};

/** Reset every mocked method's call record and implementation. */
const resetApiMock = (target) => {
  for (const value of Object.values(target)) {
    if (typeof value === 'function') value.mockReset();
    else if (value && typeof value === 'object') resetApiMock(value);
  }
};

const PAGE_SOURCE_PATH = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  'StrategyMarketplace.jsx',
);

/** Constructs that would mean the page reaches the network, or the DOM, on its own terms. */
const FORBIDDEN_IN_PAGE = [
  ['a browser dialog (alert/confirm/prompt)', /\b(?:window\.)?(?:alert|confirm|prompt)\s*\(/],
  ['import.meta.env', /import\.meta\.env/],
  ['fetch(', /\bfetch\s*\(/],
  ['XMLHttpRequest', /\bXMLHttpRequest\b/],
  ['axios', /\baxios\b/],
  ['an absolute http:// or https:// URL', /https?:\/\//],
  ['dangerouslySetInnerHTML', /dangerouslySetInnerHTML/],
];

/**
 * The forbidden constructs a piece of source text contains.
 *
 * Returned rather than asserted so the rule itself can be exercised against a fixture that
 * violates it — see `describe('controls')`. A checker nobody has ever seen fail is a checker
 * nobody has any reason to trust.
 */
const forbiddenConstructsIn = (source) => {
  const code = stripComments(source);
  return FORBIDDEN_IN_PAGE.filter(([, pattern]) => pattern.test(code)).map(([label]) => label);
};

const LISTING_ID = 'lst-1';

/** The publish-time `backtest_*` aggregate, as `listing_projection` emits it. */
const BACKTEST_FIGURES = {
  performance_summary: {
    total_return_pct: 12.5,
    win_rate_pct: 58.25,
    profit_factor: 1.7,
    total_trades: 240,
  },
  risk_metrics: { sharpe_ratio: 1.85, max_drawdown_pct: 8.4 },
};

const catalogueCard = (overrides = {}) => ({
  listing_id: LISTING_ID,
  name: 'Momentum Alpha',
  category: 'trend',
  price_minor: 1998,
  price_display: '$19.98',
  currency: 'USD',
  subscriber_count: 12,
  avg_rating: 4.5,
  ...BACKTEST_FIGURES,
  ...overrides,
});

const listingDetail = (overrides = {}) => ({
  ...catalogueCard(),
  description: 'A trend-following listing.',
  creator_alias: 'quant_one',
  tags: ['trend', 'momentum'],
  published_at: '2024-03-01T00:00:00+00:00',
  rating_count: 8,
  ...overrides,
});

/** The default catalogue reads, so a mount never leaves a rejected promise behind. */
const stubCatalogue = (card = catalogueCard()) => {
  const items = card === null ? [] : [card];
  for (const method of ['browse', 'browsePublic']) {
    mockApi.library[method].mockResolvedValue({ items, total: items.length, pages: 1 });
  }
  for (const method of ['featured', 'featuredPublic', 'trending', 'trendingPublic']) {
    mockApi.library[method].mockResolvedValue({ items: [] });
  }
  for (const method of ['categories', 'categoriesPublic']) {
    mockApi.library[method].mockResolvedValue({ categories: [] });
  }
};

const signIn = () => sessionStorage.setItem('token', 'a-token');
const signOut = () => sessionStorage.removeItem('token');

const renderPage = (initialEntries = ['/']) => render(
  <MemoryRouter initialEntries={initialEntries}>
    <StrategyMarketplace />
  </MemoryRouter>,
);

/**
 * Mount, wait for the catalogue read, click the card, wait for the detail.
 *
 * @param {Object} detail - the `api.library.detail` / `detailPublic` body.
 * @param {Object} [options]
 * @param {Object|null} [options.subscription] - the `subscriptionStatus` body, or `null`.
 */
const openDetail = async (detail, { subscription = null } = {}) => {
  const card = catalogueCard({ name: detail.name, price_minor: detail.price_minor });
  stubCatalogue(card);
  mockApi.library.detail.mockResolvedValue(detail);
  mockApi.library.detailPublic.mockResolvedValue(detail);
  if (subscription) mockApi.library.subscriptionStatus.mockResolvedValue(subscription);
  else mockApi.library.subscriptionStatus.mockResolvedValue({});

  const user = userEvent.setup();
  const view = renderPage();
  const cardHeading = await screen.findByRole('heading', { level: 3, name: detail.name });
  await user.click(cardHeading);
  // `level: 2`, not `level: 1`. retail-ui-simplification task 5.2 (commit b9bee71) moved this
  // page's chrome onto `ds/PageHeader`, which owns the page's only `<h1>` — *Strategy
  // Marketplace* — for the whole of its lifetime, browse view and detail view alike. The
  // listing's name is therefore the heading of a region INSIDE that page, at `h2`, carrying
  // `--text-page` so it is still the largest title on screen. Asserting `level: 1` here would
  // be asserting two `<h1>`s on one document, which is the thing that change removed.
  // The name match is unchanged and still exact.
  await screen.findByRole('heading', { level: 2, name: detail.name });
  return { ...view, user };
};

// ─────────────────────────────────────────────────────────────────────────────────────────

let alertSpy;
let fetchSpy;
let xhrOpenSpy;

beforeEach(() => {
  resetApiMock(mockApi);
  signOut();
  // jsdom's `window.alert` is a not-implemented stub; spying on it records calls instead of
  // raising, which is exactly what "zero calls" needs to be observable.
  alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
  window.showToast = vi.fn();
  // Any transport other than `api.library` would have to go through one of these.
  fetchSpy = vi.fn(() => Promise.reject(new Error('the page must not fetch')));
  globalThis.fetch = fetchSpy;
  window.fetch = fetchSpy;
  xhrOpenSpy = vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.showToast;
  signOut();
  window.location.hash = '';
});

/** The two assertions every test wants: no dialog, and nothing but `api.library`. */
const expectNoDialogAndOnlyLibrary = () => {
  expect(alertSpy).not.toHaveBeenCalled();
  expect(fetchSpy).not.toHaveBeenCalled();
  expect(xhrOpenSpy).not.toHaveBeenCalled();
  const paths = calledPaths(mockApi);
  expect(paths.length).toBeGreaterThan(0);
  expect(paths.filter((p) => !p.startsWith('library.'))).toEqual([]);
};

// ═════════════════════════════════════════════════════════════════════════════════════════
// 1. Transport: `api.library`, and the token check choosing between the paired methods
// ═════════════════════════════════════════════════════════════════════════════════════════

describe('StrategyMarketplace transport (Requirements 20.2, 20.10)', () => {
  it('reads the catalogue through the authenticated api.library methods when a token is held', async () => {
    signIn();
    stubCatalogue();

    renderPage();
    await waitFor(() => expect(mockApi.library.browse).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockApi.library.categories).toHaveBeenCalledTimes(1));

    expect(mockApi.library.browse).toHaveBeenCalledWith({ page: 1, limit: 20, sort: 'clones' });
    expect(mockApi.library.featured).toHaveBeenCalledWith(3);
    expect(mockApi.library.trending).toHaveBeenCalledWith(5);
    // The anonymous half of each pair is untouched: the token decided, not a second client.
    expect(mockApi.library.browsePublic).not.toHaveBeenCalled();
    expect(mockApi.library.featuredPublic).not.toHaveBeenCalled();
    expect(mockApi.library.trendingPublic).not.toHaveBeenCalled();
    expect(mockApi.library.categoriesPublic).not.toHaveBeenCalled();
    expectNoDialogAndOnlyLibrary();
  });

  it('reads the catalogue through the *Public methods when no token is held', async () => {
    stubCatalogue();

    renderPage();
    await waitFor(() => expect(mockApi.library.browsePublic).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockApi.library.categoriesPublic).toHaveBeenCalledTimes(1));

    expect(mockApi.library.browsePublic).toHaveBeenCalledWith({ page: 1, limit: 20, sort: 'clones' });
    expect(mockApi.library.featuredPublic).toHaveBeenCalledWith(3);
    expect(mockApi.library.trendingPublic).toHaveBeenCalledWith(5);
    expect(mockApi.library.browse).not.toHaveBeenCalled();
    expect(mockApi.library.featured).not.toHaveBeenCalled();
    expect(mockApi.library.trending).not.toHaveBeenCalled();
    expect(mockApi.library.categories).not.toHaveBeenCalled();
    expectNoDialogAndOnlyLibrary();
  });

  it('reads one Listing and the caller\'s own subscription state, authenticated', async () => {
    signIn();
    await openDetail(listingDetail(), { subscription: { status: 'active', expires_at: '2024-05-01T00:00:00+00:00' } });

    expect(mockApi.library.detail).toHaveBeenCalledWith(LISTING_ID);
    expect(mockApi.library.detailPublic).not.toHaveBeenCalled();
    await waitFor(() => expect(mockApi.library.subscriptionStatus).toHaveBeenCalledWith(LISTING_ID));
    expectNoDialogAndOnlyLibrary();
  });

  it('reads one Listing anonymously and asks for no subscription state at all', async () => {
    await openDetail(listingDetail());

    expect(mockApi.library.detailPublic).toHaveBeenCalledWith(LISTING_ID);
    expect(mockApi.library.detail).not.toHaveBeenCalled();
    // There is nothing to report for an anonymous visitor, so nothing is asked.
    expect(mockApi.library.subscriptionStatus).not.toHaveBeenCalled();
    expectNoDialogAndOnlyLibrary();
  });

  it('builds no transport, no host and no browser dialog anywhere in its source', () => {
    const source = fs.readFileSync(PAGE_SOURCE_PATH, 'utf8');

    expect(forbiddenConstructsIn(source)).toEqual([]);

    // And the only module it reaches the network through is `../api`. Everything else on this
    // list is presentation or classification, and every one of them is named so that a reader
    // can see at a glance that none of it is a second client:
    //
    //   * `components/ds/*` — the design-system primitives task 5.2 (b9bee71) and task 5.3
    //     (dd2df92) moved this page onto. `Panel` is the card and the panel-state renderer,
    //     `Metric` every figure, `PageHeader`/`SectionHeader` the heading outline,
    //     `CommandButton` every action, `StatusBadge` the subscription chip and
    //     `TradingEnvironmentBadge` the BACKTEST/PAPER/LIVE chip. None of them fetches.
    //   * `design/errorCopy` — task 6.1 (ac423b1): the server's error CODE → an authored
    //     sentence. It reads an error that has already arrived; it issues nothing.
    //   * `design/subscriptionState` — task 26.1: §7.9's subscription-state → badge mapping.
    //   * `hooks/usePanelState` — task 6.1: the §11.1 state machine that WRAPS a reader this
    //     page supplies. The reader it is given is `api.library.detail` / `detailPublic`, so
    //     the transport is still `../api`'s and the hook only sequences it. That is exactly
    //     what the `calledPaths(mockApi)` assertion in every other test in this file proves
    //     independently of this source scan.
    //   * `./paperTradingFormat` — `classifyReadFailure`, pure, no React and no `api`. Read
    //     for ONE field, the server's own error code, rather than growing a second envelope
    //     parser on this page.
    //
    //   * `../utils/razorpayCheckout` — Razorpay Standard Web Checkout. ADDED DELIBERATELY,
    //     and the one entry on this list that touches the network, so it is justified rather
    //     than merely recorded:
    //
    //     `checkout_service._razorpay_order` returns no `checkout_url` — Razorpay has no
    //     hosted session URL in this flow, only an order id and a publishable key. So the
    //     `checkout_url` branch below was unreachable for every Razorpay subscription and the
    //     page reported "this browser cannot present its checkout" to every INR subscriber.
    //     Presenting it requires `checkout.razorpay.com/v1/checkout.js`, which is a payment
    //     gateway's own modal and cannot be proxied through `../api`.
    //
    //     What Requirement 20.2 actually forbids is a SECOND CLIENT FOR THIS APP'S API, and
    //     that still does not exist: the module issues no request to this backend. The
    //     signature check it performs is `api.billing.verifyPayment`, INJECTED by the page as
    //     its `verify` argument, so the only path to our server is still `../api`'s. The
    //     `https://` literal and the `<script>` injection live in that module, which is why
    //     `FORBIDDEN_IN_PAGE` below is unchanged and still passes against this page's source.
    //
    // Still an EXACT set and not a subset — a sixteenth relative import, or `../apiClient`
    // in place of `../api`, fails here exactly as it did before. `FORBIDDEN_IN_PAGE` above is
    // the other half of the same guard and still bans `fetch`, `axios`, `XMLHttpRequest` and
    // an absolute URL anywhere in the file.
    //
    // TWO ENTRIES ADDED BY THE PLAN-LADDER WORK, AND WHY NEITHER WEAKENS THIS GUARD
    // ----------------------------------------------------------------------------
    // `../design/entitlements` and `../components/gates` are what let this page render a LOCKED
    // subscribe control instead of one that 403s: marketplace browsing is open to every plan, so a
    // Free account reaches the listing panel legitimately and has to be told why it cannot
    // subscribe. Both are additions to the RENDERING, not to the transport:
    //
    //   * `../design/entitlements` is a pure reader over the entitlements payload. It holds no
    //     client, issues no request and imports nothing but `./reported`.
    //   * `../components/gates` renders a refusal the SERVER composed. The one request behind it is
    //     `api.billing.getEntitlements`, made inside `hooks/useEntitlements` through `../api` —
    //     the same single client Requirement 20.2 exists to protect. There is still no second
    //     client for this app's API anywhere in this page's graph.
    const specifiers = [...stripComments(source).matchAll(/\bfrom\s+['"]([^'"]+)['"]/g)]
      .map((m) => m[1])
      .filter((s) => s.startsWith('.'));
    expect(specifiers).toEqual([
      '../api',
      '../components/ds/CommandButton',
      '../components/ds/Metric',
      '../components/ds/PageHeader',
      '../components/ds/Panel',
      '../components/ds/SectionHeader',
      '../components/ds/StatusBadge',
      '../components/ds/TradingEnvironmentBadge',
      '../components/gates',
      '../design/entitlements',
      '../design/errorCopy',
      '../design/subscriptionState',
      '../hooks/usePanelState',
      '../utils/razorpayCheckout',
      './paperTradingFormat',
    ]);
  });
});

// ═════════════════════════════════════════════════════════════════════════════════════════
// 2. Outcomes go to `window.showToast`. `window.alert` receives nothing, on any path.
// ═════════════════════════════════════════════════════════════════════════════════════════

describe('outcome reporting (Requirement 20.10)', () => {
  const FREE = { price_minor: 0, price_display: null };
  const PAID = { price_minor: 1998, price_display: '$19.98', currency: 'USD' };

  const envelope = (message, code = 'MARKETPLACE_CLONING_DISABLED') =>
    Object.assign(new Error('request failed'), {
      status: 403,
      data: { error: { code, message } },
    });

  it('reports a successful clone through showToast, and raises no dialog', async () => {
    signIn();
    mockApi.library.clone.mockResolvedValue({ message: 'Cloned as "Momentum Alpha (copy)".' });
    const { user } = await openDetail(listingDetail(FREE));

    await user.click(screen.getByRole('button', { name: /clone free/i }));

    await waitFor(() => expect(window.showToast).toHaveBeenCalledTimes(1));
    expect(window.showToast).toHaveBeenCalledWith('success', 'Cloned as "Momentum Alpha (copy)".');
    expect(mockApi.library.clone).toHaveBeenCalledWith(LISTING_ID);
    expectNoDialogAndOnlyLibrary();
  });

  it('reports a refused clone through showToast with the server\'s own wording, and raises no dialog', async () => {
    signIn();
    mockApi.library.clone.mockRejectedValue(
      envelope('The owner has disabled cloning for this listing.'),
    );
    const { user } = await openDetail(listingDetail(FREE));

    await user.click(screen.getByRole('button', { name: /clone free/i }));

    await waitFor(() => expect(window.showToast).toHaveBeenCalledTimes(1));
    expect(window.showToast).toHaveBeenCalledWith(
      'error',
      'The owner has disabled cloning for this listing.',
    );
    expectNoDialogAndOnlyLibrary();
  });

  it('hands a successful checkout to the provider session the server returned, and raises no dialog', async () => {
    signIn();
    // A fragment URL so jsdom performs the assignment rather than refusing a cross-document
    // navigation. What is asserted is that the page used the server's URL and composed none.
    mockApi.library.checkout.mockResolvedValue({ checkout_url: '#provider-session-42' });
    const { user } = await openDetail(listingDetail(PAID));

    await user.click(screen.getByRole('button', { name: /^subscribe$/i }));

    await waitFor(() => expect(window.location.hash).toBe('#provider-session-42'));
    expect(mockApi.library.checkout).toHaveBeenCalledWith(LISTING_ID, 'USD');
    // The redirect *is* the outcome; nothing is announced, and nothing is a dialog.
    expect(window.showToast).not.toHaveBeenCalled();
    expectNoDialogAndOnlyLibrary();
  });

  it('reports a checkout this browser cannot present through showToast, saying nothing was charged', async () => {
    signIn();
    mockApi.library.checkout.mockResolvedValue({ provider: 'stripe', session_id: 'cs_1' });
    const { user } = await openDetail(listingDetail(PAID));

    await user.click(screen.getByRole('button', { name: /^subscribe$/i }));

    await waitFor(() => expect(window.showToast).toHaveBeenCalledTimes(1));
    const [type, message] = window.showToast.mock.calls[0];
    expect(type).toBe('error');
    expect(message).toContain('stripe');
    expect(message).toMatch(/nothing has been charged/i);
    expectNoDialogAndOnlyLibrary();
  });

  it('reports a failed checkout through showToast, and raises no dialog', async () => {
    signIn();
    mockApi.library.checkout.mockRejectedValue(
      envelope('No entitling payment method is on file.', 'MARKETPLACE_CHECKOUT_FAILED'),
    );
    const { user } = await openDetail(listingDetail(PAID));

    await user.click(screen.getByRole('button', { name: /^subscribe$/i }));

    await waitFor(() => expect(window.showToast).toHaveBeenCalledTimes(1));
    expect(window.showToast).toHaveBeenCalledWith(
      'error',
      'No entitling payment method is on file.',
    );
    expectNoDialogAndOnlyLibrary();
  });

  it('renders the outcome inline when no toast host is mounted, instead of falling back to a dialog', async () => {
    signIn();
    // The page mounted outside `AppShell`: there is no `window.showToast` at all.
    delete window.showToast;
    mockApi.library.clone.mockRejectedValue(envelope('Cloning is disabled by the owner.'));
    const { user } = await openDetail(listingDetail(FREE));

    await user.click(screen.getByRole('button', { name: /clone free/i }));

    // The message is not lost, and it is not a dialog.
    expect(await screen.findByText('Cloning is disabled by the owner.')).toBeTruthy();
    expect(alertSpy).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

// ═════════════════════════════════════════════════════════════════════════════════════════
// 3. Owner-supplied text is text (Requirement 22.8)
// ═════════════════════════════════════════════════════════════════════════════════════════

describe('owner-supplied text is rendered as text (Requirement 22.8)', () => {
  const PAYLOAD = '<img src=x onerror=alert(1)>';

  it('renders the payload in name, description, tags and a review as inert text', async () => {
    signIn();
    const { container } = await openDetail(
      listingDetail({
        name: PAYLOAD,
        description: `${PAYLOAD} description`,
        tags: [PAYLOAD, 'trend'],
        recent_ratings: [
          { rating: 4, created_at: '2024-04-01T00:00:00+00:00', review_text: `${PAYLOAD} review` },
        ],
      }),
    );

    // The whole point: the markup produced no element.
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelectorAll('img, script, iframe, object, embed').length).toBe(0);
    expect(container.querySelector('[onerror]')).toBeNull();

    // …and it was not silently dropped either — all four fields are on screen, as text.
    expect(container.textContent).toContain(`${PAYLOAD} description`);
    expect(container.textContent).toContain(`${PAYLOAD} review`);
    // The tag and the heading both carry the bare payload.
    expect(screen.getAllByText(PAYLOAD).length).toBeGreaterThanOrEqual(2);

    // `onerror=alert(1)` never ran.
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it('renders a payload carried in a catalogue card as text as well', async () => {
    signIn();
    stubCatalogue(catalogueCard({ name: PAYLOAD, category: PAYLOAD }));

    const { container } = renderPage();
    await screen.findByRole('heading', { level: 3, name: PAYLOAD });

    expect(container.querySelector('img')).toBeNull();
    expect(alertSpy).not.toHaveBeenCalled();
  });
});

// ═════════════════════════════════════════════════════════════════════════════════════════
// 4. The three environment sections (Requirement 6.6)
// ═════════════════════════════════════════════════════════════════════════════════════════

/**
 * The environment SECTIONS — `<section data-environment="…">`.
 *
 * `[data-environment]` on its own no longer names one thing. `ds/TradingEnvironmentBadge`
 * publishes the same attribute on its own `<span>` ("published for Property 22 and for
 * anything asserting on the rendered output", `TradingEnvironmentBadge.jsx:224`), and task
 * 5.2 put one of those chips in each section's `ds/SectionHeader`. So the bare attribute
 * selector matches two elements per environment — the section and the chip nested inside it
 * — and `sectionsIn` would report six sections for three, with the chip counted as a section
 * that its own section contains.
 *
 * The element type is the discriminator, and it is the page's own: the sections are
 * `<section>`s (`StrategyMarketplace.jsx:800`), the chip is a `<span>`. Nothing about what is
 * asserted below changes — one section when one environment is measured, three when three
 * are, in `BACKTEST, PAPER, LIVE` order, disjoint and sibling.
 */
const sectionsIn = (container) => [
  ...container.querySelectorAll('section[data-environment]'),
];

describe('BACKTEST / PAPER / LIVE sections (Requirements 6.6, 28.5)', () => {
  it('renders only the environments the Listing carries figures for', async () => {
    signIn();
    const { container } = await openDetail(listingDetail());

    // BACKTEST is measured; PAPER and LIVE are not carried by the projection at all.
    expect(container.querySelector('[data-environment="BACKTEST"]')).not.toBeNull();
    expect(container.querySelector('[data-environment="PAPER"]')).toBeNull();
    expect(container.querySelector('[data-environment="LIVE"]')).toBeNull();
    expect(sectionsIn(container).length).toBe(1);
    // An omitted section is omitted, not zero-filled.
    expect(container.textContent).not.toContain('PAPER Performance');
    expect(container.textContent).not.toContain('LIVE Performance');
  });

  it('omits a section whose figures are all unmeasured rather than rendering zeros', async () => {
    signIn();
    const { container } = await openDetail(
      listingDetail({
        // Present objects, no measured values. `(x || 0).toFixed(2)` — the shape this page used
        // to use — would render six "0.00" cells here.
        paper_performance_summary: {
          total_return_pct: null, win_rate_pct: null, profit_factor: null, total_trades: null,
        },
        paper_risk_metrics: { sharpe_ratio: null, max_drawdown_pct: undefined },
        live_performance_summary: { total_return_pct: '', win_rate_pct: 'n/a' },
        live_risk_metrics: { sharpe_ratio: NaN },
      }),
    );

    expect(container.querySelector('[data-environment="PAPER"]')).toBeNull();
    expect(container.querySelector('[data-environment="LIVE"]')).toBeNull();
    expect(sectionsIn(container).length).toBe(1);
    expect(container.textContent).not.toContain('0.00');
  });

  it('renders all three as visually separated, disjoint sibling sections when all are measured', async () => {
    signIn();
    const { container } = await openDetail(
      listingDetail({
        paper_performance_summary: { total_return_pct: 3.5, total_trades: 12 },
        paper_risk_metrics: { sharpe_ratio: 0.9, max_drawdown_pct: 2.1 },
        live_performance_summary: { total_return_pct: -1.25, win_rate_pct: 44 },
        live_risk_metrics: { sharpe_ratio: 0.4, max_drawdown_pct: 6 },
      }),
    );

    const sections = sectionsIn(container);
    // The same selector that found nothing above now finds three — which is what makes the
    // omission assertion above a fact about the data rather than about the selector.
    expect(sections.map((s) => s.getAttribute('data-environment'))).toEqual([
      'BACKTEST', 'PAPER', 'LIVE',
    ]);
    // Separated: three distinct elements, siblings of one another, none containing another.
    expect(new Set(sections.map((s) => s.parentElement)).size).toBe(1);
    for (const a of sections) {
      for (const b of sections) {
        if (a !== b) expect(a.contains(b)).toBe(false);
      }
    }
    // Each figure inside a section carries that section's environment label (Req 6.6).
    //
    // Asserted per figure, not as a count of bare-text matches. The environment now rides
    // INSIDE each figure's label — `Total return · BACKTEST` (`StrategyMarketplace.jsx:827`,
    // "so the figure and the environment it was measured in cannot be separated") — so an
    // exact-text query for the bare word reaches none of the figures at all. It finds only
    // the `ds/TradingEnvironmentBadge` chip in the section header, and not even that for
    // PAPER, whose chip reads `PAPER TRADING` (`design/semantic.js:213`).
    //
    // `[data-metric-tier]` is `ds/Metric`'s own published hook, one per rendered figure. The
    // `> 1` count it replaces is kept, and the label check is now per figure rather than in
    // aggregate: ONE figure that dropped its environment fails here, where before any two
    // labelled elements in the section were enough to pass.
    for (const section of sections) {
      const env = section.getAttribute('data-environment');
      const figures = [...section.querySelectorAll('[data-metric-tier]')];
      expect(figures.length).toBeGreaterThan(1);
      for (const figure of figures) {
        expect(figure.textContent, `a figure in ${env} does not name its environment`)
          .toContain(env);
      }
    }
    // Nothing crosses over: the LIVE loss is in LIVE and nowhere else. `section[…]` for the
    // same reason `sectionsIn` uses it — the bare attribute also matches the chip inside.
    const live = container.querySelector('section[data-environment="LIVE"]');
    expect(live.textContent).toContain('-1.25%');
    expect(container.querySelector('section[data-environment="BACKTEST"]').textContent)
      .not.toContain('-1.25%');
  });

  it('carries the historical-results statement in BACKTEST and in no other section', async () => {
    signIn();
    const { container } = await openDetail(
      listingDetail({
        paper_performance_summary: { total_return_pct: 3.5 },
        paper_risk_metrics: { sharpe_ratio: 0.9 },
        live_performance_summary: { total_return_pct: 1 },
        live_risk_metrics: { sharpe_ratio: 0.4 },
      }),
    );

    // Exactly one, so both a missing statement and one leaking into PAPER/LIVE fail here.
    const statements = container.querySelectorAll('[data-testid="historical-results-statement"]');
    expect(statements.length).toBe(1);

    const backtest = container.querySelector('section[data-environment="BACKTEST"]');
    expect(within(backtest).getByTestId('historical-results-statement').textContent)
      .toMatch(/past performance does not indicate future results/i);
    expect(within(container.querySelector('section[data-environment="PAPER"]'))
      .queryByTestId('historical-results-statement')).toBeNull();
    expect(within(container.querySelector('section[data-environment="LIVE"]'))
      .queryByTestId('historical-results-statement')).toBeNull();
  });

  it('says so plainly when the Listing carries no figures at all, and states no statement', async () => {
    signIn();
    const { container } = await openDetail(
      listingDetail({ performance_summary: null, risk_metrics: null }),
    );

    expect(sectionsIn(container).length).toBe(0);
    expect(container.textContent).toMatch(/no performance figures have been recorded/i);
    // No section means no BACKTEST section, so the statement has nothing to sit in.
    expect(container.querySelectorAll('[data-testid="historical-results-statement"]').length)
      .toBe(0);
    expect(container.textContent).not.toContain('0.00');
  });
});

// ═════════════════════════════════════════════════════════════════════════════════════════
// 5. An unmeasured figure, and a price nobody could read (Requirements 28.1, 28.5)
// ═════════════════════════════════════════════════════════════════════════════════════════

describe('unmeasured figures and unread prices (Requirements 28.1, 28.5)', () => {
  it('renders an em dash for a figure the server did not send, never 0.00', async () => {
    signIn();
    const { container } = await openDetail(
      listingDetail({
        subscriber_count: null,
        avg_rating: undefined,
        rating_count: null,
        published_at: null,
      }),
    );

    // Subscribers, Rating and Published are all unmeasured here.
    expect(container.textContent).toContain('—');
    expect(container.textContent).not.toContain('0.00');
    expect(container.textContent).not.toMatch(/\b0 conditions\b/);
  });

  it('disables the purchase action when price_minor cannot be read, and charges nothing', async () => {
    signIn();
    // A formatted string is present but the exact integer is not. The page must not offer a
    // purchase at a price it never read.
    const { user } = await openDetail(
      listingDetail({ price_minor: null, price_display: '$19.98', currency: 'USD' }),
    );

    const button = screen.getByRole('button', { name: /price unavailable/i });
    expect(button.disabled).toBe(true);
    expect(screen.queryByRole('button', { name: /^subscribe$/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /clone free/i })).toBeNull();

    // And clicking it reaches no checkout: it carries no handler to reach one with.
    await user.click(button);
    expect(mockApi.library.checkout).not.toHaveBeenCalled();
    expect(mockApi.library.clone).not.toHaveBeenCalled();
    expectNoDialogAndOnlyLibrary();
  });

  it('offers the free clone when the price is a readable zero', async () => {
    signIn();
    // The paired positive: the disabled state above is about an unreadable price, not about
    // every price.
    await openDetail(listingDetail({ price_minor: 0, price_display: null }));

    expect(screen.getByRole('button', { name: /clone free/i }).disabled).toBe(false);
    expect(screen.queryByRole('button', { name: /price unavailable/i })).toBeNull();
  });

  it('renders the caller\'s subscription state as returned, and offers no second purchase', async () => {
    signIn();
    await openDetail(listingDetail(), {
      subscription: { status: 'active', expires_at: '2024-05-01T00:00:00+00:00' },
    });

    const subscribed = await screen.findByRole('button', { name: /^subscribed$/i });
    expect(subscribed.disabled).toBe(true);
    expect(screen.queryByRole('button', { name: /^subscribe$/i })).toBeNull();
    expect(mockApi.library.checkout).not.toHaveBeenCalled();
  });
});

// ═════════════════════════════════════════════════════════════════════════════════════════
// 6. MKT-05 — the deploy flow for a subscriber who already holds the Subscription
// ═════════════════════════════════════════════════════════════════════════════════════════
//
// Before this: a subscriber had no control anywhere on this page that could reach
// `POST /api/library/{id}/deploy` — `isSubscribed` only ever produced a disabled "Subscribed"
// button. This section proves the control exists, calls the real endpoint with what the
// trader typed (and nothing invented for a field they left blank), and that `?listing_id=
// &intent=deploy` on the URL opens the right Listing on mount without a card click.

const ACTIVE_SUBSCRIPTION = { subscription: { status: 'active', expires_at: null } };

describe('deploy (MKT-05): the control a subscriber uses to run what they paid for', () => {
  it('is absent for a listing the caller has not subscribed to', async () => {
    signIn();
    await openDetail(listingDetail(), { subscription: { status: 'not_subscribed' } });

    expect(screen.queryByTestId('deploy-panel')).toBeNull();
  });

  it('appears once the subscription reads back as active', async () => {
    signIn();
    await openDetail(listingDetail(), ACTIVE_SUBSCRIPTION);

    expect(await screen.findByTestId('deploy-panel')).not.toBeNull();
    expect(screen.getByRole('button', { name: /^deploy$/i })).not.toBeNull();
  });

  it('sends only the fields the trader actually typed', async () => {
    signIn();
    mockApi.library.deploy.mockResolvedValue({
      deployment_id: 'dep-1',
      library_id: LISTING_ID,
      message: 'Marketplace deployment created against the owner\'s version.',
    });
    const { user } = await openDetail(listingDetail(), ACTIVE_SUBSCRIPTION);
    await screen.findByTestId('deploy-panel');

    await user.type(screen.getByLabelText(/symbol/i), 'BTC/USDT');
    await user.type(screen.getByLabelText(/timeframe/i), '1h');
    await user.type(screen.getByLabelText(/capital/i), '5000');
    await user.click(screen.getByRole('button', { name: /^deploy$/i }));

    await waitFor(() => expect(mockApi.library.deploy).toHaveBeenCalledWith(LISTING_ID, {
      symbol: 'BTC/USDT',
      timeframe: '1h',
      capital: 5000,
    }));
    expect(window.showToast).toHaveBeenCalledWith(
      'success',
      'Marketplace deployment created against the owner\'s version.',
    );
    expectNoDialogAndOnlyLibrary();
  });

  it('sends an empty body when every field is left blank, rather than inventing one', async () => {
    signIn();
    mockApi.library.deploy.mockResolvedValue({ deployment_id: 'dep-2', library_id: LISTING_ID });
    const { user } = await openDetail(listingDetail(), ACTIVE_SUBSCRIPTION);
    await screen.findByTestId('deploy-panel');

    await user.click(screen.getByRole('button', { name: /^deploy$/i }));

    await waitFor(() => expect(mockApi.library.deploy).toHaveBeenCalledWith(LISTING_ID, {}));
  });

  it('reports a refusal through the toast mechanism, never a dialog', async () => {
    signIn();
    mockApi.library.deploy.mockRejectedValue(new Error('MARKETPLACE_SUBSCRIPTION_EXPIRED'));
    const { user } = await openDetail(listingDetail(), ACTIVE_SUBSCRIPTION);
    await screen.findByTestId('deploy-panel');

    await user.click(screen.getByRole('button', { name: /^deploy$/i }));

    await waitFor(() => expect(window.showToast).toHaveBeenCalledWith('error', expect.any(String)));
    expect(alertSpy).not.toHaveBeenCalled();
  });

  // ─────────────────────────────────────────────────────────────────────────────────────
  // MKT-05 live deployment, bounded by the subscribed period.
  //
  // The backend accepts `mode: 'live'` with the caller's OWN `exchange_account_id`, stamps
  // the deployment with the subscription that authorises it and the instant that
  // authorisation ends, and refuses to create one without both. This page is the only way
  // a trader reaches that, so these cases are about the two things a UI can get wrong with
  // real money: sending live when the trader did not ask for it, and sending an account id
  // that is not one.
  //
  // `expectNoDialogAndOnlyLibrary` is NOT used below. The live path legitimately reads
  // `api.exchange.list()` to populate the account selector, so the correct assertion is
  // "library and exchange, nothing else" rather than "library only".
  // ─────────────────────────────────────────────────────────────────────────────────────

  const ACCOUNT = {
    // An `exchange_connections` row. `id` is the real key a deployment's
    // `exchange_account_id` can store -- `exchange_keys` has no such column at all (its
    // PRIMARY KEY is the composite `(user_id, exchange_id)`, measured against
    // production), which is why the selector reads connections rather than keys.
    id: 'acct-real-uuid',
    exchange_id: 'binance',
    name: 'BINANCE',
  };

  const expectOnlyLibraryAndExchange = () => {
    expect(alertSpy).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    const paths = calledPaths(mockApi);
    expect(paths.length).toBeGreaterThan(0);
    expect(paths.filter((p) => !p.startsWith('library.') && !p.startsWith('exchange.')))
      .toEqual([]);
  };

  const chooseLive = async (user) => {
    mockApi.exchange.listConnections.mockResolvedValue([ACCOUNT]);
    await user.click(screen.getByTestId('deploy-mode-live'));
    return screen.findByTestId('deploy-live-fields');
  };

  it('opens on paper, so live is never the default', async () => {
    signIn();
    await openDetail(listingDetail(), ACTIVE_SUBSCRIPTION);
    await screen.findByTestId('deploy-panel');

    expect(screen.getByTestId('deploy-mode-paper').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('deploy-mode-live').getAttribute('aria-checked')).toBe('false');
    // No live fields and no exchange read until live is actually chosen.
    expect(screen.queryByTestId('deploy-live-fields')).toBeNull();
    expect(mockApi.exchange.listConnections).not.toHaveBeenCalled();
  });

  it('reads the caller\'s exchange accounts only once live is chosen', async () => {
    signIn();
    const { user } = await openDetail(listingDetail(), ACTIVE_SUBSCRIPTION);
    await screen.findByTestId('deploy-panel');

    await chooseLive(user);

    await waitFor(() => expect(mockApi.exchange.listConnections).toHaveBeenCalledTimes(1));
  });

  it('sends mode and the account id the deployment row can actually store', async () => {
    signIn();
    mockApi.library.deploy.mockResolvedValue({ deployment_id: 'dep-live', library_id: LISTING_ID });
    const { user } = await openDetail(listingDetail(), ACTIVE_SUBSCRIPTION);
    await screen.findByTestId('deploy-panel');
    await chooseLive(user);

    // One account, so it is preselected -- picking one of SEVERAL on the trader's behalf
    // would be choosing which account trades their money, which this page does not do.
    await waitFor(() =>
      expect(screen.getByTestId('deploy-account-select').value).toBe('acct-real-uuid'));
    await user.click(screen.getByRole('button', { name: /deploy live/i }));

    await waitFor(() => expect(mockApi.library.deploy).toHaveBeenCalledWith(LISTING_ID, {
      mode: 'live',
      exchange_account_id: 'acct-real-uuid',
    }));
    expectOnlyLibraryAndExchange();
  });

  it('refuses to submit live with no account chosen, and says why on the control', async () => {
    signIn();
    mockApi.exchange.listConnections.mockResolvedValue([ACCOUNT, { ...ACCOUNT, id: 'acct-2', name: 'KRAKEN' }]);
    const { user } = await openDetail(listingDetail(), ACTIVE_SUBSCRIPTION);
    await screen.findByTestId('deploy-panel');
    await user.click(screen.getByTestId('deploy-mode-live'));
    await screen.findByTestId('deploy-live-fields');

    // TWO accounts, so nothing is preselected.
    await waitFor(() => expect(screen.getByTestId('deploy-account-select').value).toBe(''));
    const button = screen.getByRole('button', { name: /deploy live/i });
    expect(button.hasAttribute('disabled')).toBe(true);

    await user.click(button);
    expect(mockApi.library.deploy).not.toHaveBeenCalled();
  });

  it('still sends a paper deployment with no mode when paper is kept', async () => {
    signIn();
    mockApi.library.deploy.mockResolvedValue({ deployment_id: 'dep-paper', library_id: LISTING_ID });
    const { user } = await openDetail(listingDetail(), ACTIVE_SUBSCRIPTION);
    await screen.findByTestId('deploy-panel');

    await user.click(screen.getByRole('button', { name: /^deploy$/i }));

    // No `mode` key at all. The server defaults to paper, and the existing
    // "empty body when every field is blank" property must survive this change.
    await waitFor(() => expect(mockApi.library.deploy).toHaveBeenCalledWith(LISTING_ID, {}));
    expectNoDialogAndOnlyLibrary();
  });

  it('does not offer an account whose real id is missing', async () => {
    signIn();
    // A row with no `account_id` cannot be deployed against -- sending the composite would
    // 404 and look like a missing account rather than a bad id -- so it must not appear.
    mockApi.exchange.listConnections.mockResolvedValue([{ exchange_id: 'binance' }]);
    const { user } = await openDetail(listingDetail(), ACTIVE_SUBSCRIPTION);
    await screen.findByTestId('deploy-panel');
    await user.click(screen.getByTestId('deploy-mode-live'));

    expect(await screen.findByTestId('deploy-no-accounts')).not.toBeNull();
    expect(screen.queryByTestId('deploy-account-select')).toBeNull();
  });

  it('reports a failed account read as a failure, not as "you have no accounts"', async () => {
    signIn();
    mockApi.exchange.listConnections.mockRejectedValue(new Error('EXCHANGE_CONNECTIONS_READ_FAILED'));
    const { user } = await openDetail(listingDetail(), ACTIVE_SUBSCRIPTION);
    await screen.findByTestId('deploy-panel');
    await user.click(screen.getByTestId('deploy-mode-live'));

    // An empty list and a read that did not complete are different statements, and this
    // one must not claim the account holder has no exchange connected.
    expect(await screen.findByTestId('deploy-accounts-error')).not.toBeNull();
    expect(screen.queryByTestId('deploy-no-accounts')).toBeNull();
    expect(screen.queryByTestId('deploy-account-select')).toBeNull();
  });

  it('tells a live deployer the period bound before they commit', async () => {
    signIn();
    const { user } = await openDetail(listingDetail(), ACTIVE_SUBSCRIPTION);
    await screen.findByTestId('deploy-panel');
    const panel = await chooseLive(user);

    // The bound is the thing a subscriber most needs to know, because it is what makes
    // this deployment stop. Asserted on the rendered text, not on a comment.
    expect(panel.textContent).toMatch(/real orders/i);
    expect(panel.textContent).toMatch(/subscription/i);
  });

  it('opens the named Listing directly when the URL carries ?listing_id and intent=deploy', async () => {
    signIn();
    const detail = listingDetail();
    stubCatalogue(catalogueCard({ name: detail.name, price_minor: detail.price_minor }));
    mockApi.library.detail.mockResolvedValue(detail);
    mockApi.library.subscriptionStatus.mockResolvedValue(ACTIVE_SUBSCRIPTION.subscription);

    render(
      <MemoryRouter initialEntries={[`/?listing_id=${LISTING_ID}&intent=deploy`]}>
        <StrategyMarketplace />
      </MemoryRouter>,
    );

    // No card click anywhere in this test -- the URL alone put the page in the detail view.
    await screen.findByRole('heading', { level: 2, name: detail.name });
    expect(await screen.findByTestId('deploy-panel')).not.toBeNull();
    expect(mockApi.library.detail).toHaveBeenCalledWith(LISTING_ID);
  });

  it('does not open any Listing when the URL carries listing_id with no deploy intent', async () => {
    signIn();
    stubCatalogue();

    render(
      <MemoryRouter initialEntries={[`/?listing_id=${LISTING_ID}`]}>
        <StrategyMarketplace />
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { level: 1, name: /strategy marketplace/i });
    expect(mockApi.library.detail).not.toHaveBeenCalled();
    expect(screen.queryByTestId('deploy-panel')).toBeNull();
  });
});

// ═════════════════════════════════════════════════════════════════════════════════════════
// 7. Controls — each assertion above, shown failing when the behaviour it describes is broken
// ═════════════════════════════════════════════════════════════════════════════════════════

describe('controls: the assertions above can fail', () => {
  it('the alert spy records a real dialog, so "zero calls" is an observation', () => {
    // If `window.alert` were not intercepted, every zero-call assertion above would pass
    // vacuously. It is intercepted, and it counts.
    expect(alertSpy).not.toHaveBeenCalled();
    window.alert('a dialog the page never raises');
    expect(alertSpy).toHaveBeenCalledTimes(1);
    expect(alertSpy).toHaveBeenCalledWith('a dialog the page never raises');
    alertSpy.mockClear();
  });

  it('the source-text rule flags a dialog, a hard-coded host and a raw-HTML prop', () => {
    const broken = [
      "const notify = (m) => alert(m);",
      "const base = 'https://api.example.com';",
      "const x = await fetch(base);",
      "return <div dangerouslySetInnerHTML={{ __html: description }} />;",
    ].join('\n');

    const flagged = forbiddenConstructsIn(broken);
    expect(flagged).toContain('a browser dialog (alert/confirm/prompt)');
    expect(flagged).toContain('an absolute http:// or https:// URL');
    expect(flagged).toContain('fetch(');
    expect(flagged).toContain('dangerouslySetInnerHTML');
    // And a clean fixture is not flagged, so the rule is not simply always-true.
    expect(forbiddenConstructsIn("notify('success', message);")).toEqual([]);
  });

  it('the img query finds the payload when it is injected as HTML', () => {
    const PAYLOAD = '<img src=x onerror=alert(1)>';
    // Identical container, identical query, sanitisation removed. The query that returns
    // `null` for the page returns an element here.
    const { container } = render(<div dangerouslySetInnerHTML={{ __html: PAYLOAD }} />);

    expect(container.querySelector('img')).not.toBeNull();
    expect(container.querySelector('[onerror]')).not.toBeNull();
  });

  it('the API-namespace assertion rejects a call made outside api.library', () => {
    // The walk that `expectNoDialogAndOnlyLibrary` relies on: if the page ever reached another
    // namespace, this is the shape the failure would take.
    mockApi.paper.getSummary();
    const paths = calledPaths(mockApi);
    expect(paths).toContain('paper.getSummary');
    expect(paths.filter((p) => !p.startsWith('library.'))).not.toEqual([]);
    mockApi.paper.getSummary.mockClear();
  });
});
