/**
 * tests/unit/pages/strategyMarketplace.test.jsx — retail-ui-simplification task 5.1.
 *
 * Requirements 10.5, 11.2, 11.3, 11.4, 12.4, 18.1, 20.1. design.md §5.6, §5.8, §7.
 *
 * WHY THIS FILE EXISTS AT ALL
 * ---------------------------
 * **No frontend test rendered this page.** Searching `tests/` for it returns five
 * source-scanning guards that hold an entry for its path — `no-colour-literals`,
 * `absolute-font-sizes`, `no-placeholders`, `no-native-dialogs`, `a11y-ratchet` — and
 * nothing that mounts it. Its only behavioural coverage is
 * `tests/unit/design/subscriptionState.test.js`, which covers the MODULE Requirement 10.6
 * forbids reimplementing and never the page. So the whole of Requirements 10, 11 and 12
 * lands on a 1,209-line file whose rendered output is asserted nowhere, and
 * Requirement 16's no-functional-change constraint would be a reviewer's opinion.
 *
 * THE FOUR BLOCKS, AND WHICH COMMIT TURNS EACH GREEN
 * --------------------------------------------------
 *   1. The cards are keyboard-activatable and carry their listing's name
 *      (Requirements 12.4, 20.1). Fails today: `:664` and `:729` are `div`s with an
 *      `onClick`, `cursor-pointer`, no `role`, no `tabIndex` and no key handler — which is
 *      precisely the `count: 4` in `eslint-rules/a11y-ratchet.js`. Green after task 5.3.
 *   2. The four failure classes render four different pieces of authored copy, resolved
 *      through `design/errorCopy.js`, and none of them is `err.message`
 *      (Requirements 11.2, 11.3). Fails today: one `catch` at `:380`–`:382` sets one
 *      string for the network not answering, the server faulting, an unauthenticated
 *      caller and an unentitled one. Green after task 6.1.
 *   3. The `42703` case renders `unavailable`, naming what cannot be shown, rather than an
 *      empty catalogue (Requirement 11.4). Fails today, and this is the live production
 *      surface: `library_strategies.price_minor` is added by migration `007`, `007` is
 *      unapplied, so `library.py:720`/`:785` raise the structured
 *      `MARKETPLACE_READ_FAILED` — and the page discards the code, shows "Failed to load
 *      marketplace data" AND renders the catalogue's own "no strategies found" beside it.
 *      Green after task 6.1.
 *   4. `featured` and `trending` stay distinguishable once the `Sparkles` ribbon goes
 *      (Requirement 10.5, design Decision D6). Fails today: the only distinction is
 *      `bg-status-warning` + `Sparkles`, a hue `design/semantic.js` reserves for a state a
 *      trader should act on, standing in for an operator flag with no performance basis.
 *      Green after task 5.2.
 *
 * WHAT IS NOT MOCKED, AND WHY THAT MATTERS
 * ----------------------------------------
 * `design/errorCopy.js`, `hooks/usePanelState.js`, `design/subscriptionState.js` and every
 * `components/ds/*` primitive are REAL here. The only double is `src/api`, and it exists to
 * choose what the server answered. `errorCopy.property.test.js` already proves
 * `translateError` is safe over its whole input space; what it cannot prove is that this
 * page calls it, and that is this file's job (design.md §5.6).
 *
 * The authored sentences are asserted through `CATEGORY_COPY` / `CODE_COPY` rather than
 * retyped, so a test that passes cannot be one that agrees with a copy change nobody
 * reviewed.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const { library } = vi.hoisted(() => ({
  library: {
    featured: vi.fn(),
    featuredPublic: vi.fn(),
    trending: vi.fn(),
    trendingPublic: vi.fn(),
    categories: vi.fn(),
    categoriesPublic: vi.fn(),
    browse: vi.fn(),
    browsePublic: vi.fn(),
    detail: vi.fn(),
    detailPublic: vi.fn(),
    subscriptionStatus: vi.fn(),
    clone: vi.fn(),
    checkout: vi.fn(),
  },
}));

vi.mock('../../../src/api', () => ({ default: { library } }));

import StrategyMarketplace from '../../../src/pages/StrategyMarketplace';
import { ApiError } from '../../../src/apiClient';
import { CATEGORY_COPY, CODE_COPY } from '../../../src/design/errorCopy';

// ── Fixtures ────────────────────────────────────────────────────────────────────────────
//
// Three listings with three distinguishable names, one per section, so a query by
// accessible name cannot match the wrong card.

const FEATURED = Object.freeze({
  listing_id: 'lst-featured',
  name: 'Aurora Momentum',
  creator_alias: 'aurora-labs',
  category: 'trend',
  performance_summary: { total_return_pct: 12.5 },
  risk_metrics: { sharpe_ratio: 1.82 },
  subscriber_count: 120,
  price_minor: 49900,
  price_display: '₹499',
  currency: 'INR',
});

const TRENDING = Object.freeze({
  listing_id: 'lst-trending',
  name: 'Vega Breakout',
  creator_alias: 'vega-desk',
  category: 'breakout',
  performance_summary: { total_return_pct: -3.25 },
  risk_metrics: { sharpe_ratio: 0.94 },
  subscriber_count: 41,
  clone_count: 210,
  avg_rating: 4.2,
  price_minor: 0,
  currency: 'INR',
});

const CATALOGUE = Object.freeze({
  listing_id: 'lst-catalogue',
  name: 'Helios Reversion',
  creator_alias: 'helios',
  category: 'mean-reversion',
  performance_summary: { total_return_pct: 4.1 },
  risk_metrics: { sharpe_ratio: 1.1 },
  subscriber_count: 9,
  avg_rating: 3.5,
  price_minor: 19900,
  price_display: '₹199',
  currency: 'INR',
});

/**
 * An exception text that must never reach the screen (Requirement 11.3).
 *
 * It is the shape of the real one: the production failure is PostgreSQL `42703` on
 * `library_strategies.price_minor`, and a page that renders `err.message` puts a column
 * name and an exception class in front of a retail trader.
 */
const LEAKY_MESSAGE =
  'UndefinedColumnError: column library_strategies.price_minor does not exist at /api/library/browse';

/** The string `:382` sets for every failure mode today. Nothing may render it after 6.1. */
const ONE_STRING_FOR_EVERYTHING = 'Failed to load marketplace data';

const apiError = ({ status, code, message = LEAKY_MESSAGE, requestId } = {}) =>
  new ApiError(message, {
    url: '/api/library/browse',
    method: 'get',
    status,
    statusText: status ? String(status) : undefined,
    data: code ? { error: { code, message: 'server-side words nobody authored' } } : undefined,
    requestId,
  });

/**
 * The four failure classes Requirement 11.2 names, with the authored sentence each one
 * must reach. The trader's next action differs in every row — retry, wait, sign in,
 * upgrade — which is the whole reason one string cannot serve all four.
 */
const FAILURE_CLASSES = Object.freeze([
  {
    name: 'the network did not answer',
    error: () => apiError({}),
    expected: () => CATEGORY_COPY.NETWORK_ERROR.headline,
  },
  {
    name: 'the server faulted',
    error: () => apiError({ status: 500 }),
    expected: () => CATEGORY_COPY.SERVER_ERROR.headline,
  },
  {
    name: 'the caller is not authenticated',
    error: () => apiError({ status: 401 }),
    expected: () => CATEGORY_COPY.AUTH_ERROR.headline,
  },
  {
    name: 'the caller is not entitled',
    error: () => apiError({ status: 403, code: 'MARKETPLACE_NOT_SUBSCRIBED' }),
    expected: () => CODE_COPY.MARKETPLACE_NOT_SUBSCRIBED.detail,
  },
]);

const mount = () =>
  render(
    <MemoryRouter>
      <StrategyMarketplace />
    </MemoryRouter>,
  );

/** Everything the page has on screen, whitespace-normalised. */
const pageText = () => (document.body.textContent || '').replace(/\s+/g, ' ').trim();

beforeEach(() => {
  // A credential is present, so the authenticated `api.library.*` methods are the ones
  // called. The `*Public` pair is mocked too: a page that switched arms would otherwise
  // fail on `undefined is not a function` rather than on the assertion.
  sessionStorage.setItem('token', 'test-token');

  library.featured.mockResolvedValue({ items: [FEATURED] });
  library.featuredPublic.mockResolvedValue({ items: [FEATURED] });
  library.trending.mockResolvedValue({ items: [TRENDING] });
  library.trendingPublic.mockResolvedValue({ items: [TRENDING] });
  library.categories.mockResolvedValue({ categories: [{ name: 'trend', count: 3 }] });
  library.categoriesPublic.mockResolvedValue({ categories: [{ name: 'trend', count: 3 }] });
  library.browse.mockResolvedValue({ items: [CATALOGUE], pages: 1 });
  library.browsePublic.mockResolvedValue({ items: [CATALOGUE], pages: 1 });
  library.detail.mockResolvedValue({ ...CATALOGUE, condition_summaries: [] });
  library.detailPublic.mockResolvedValue({ ...CATALOGUE, condition_summaries: [] });
  library.subscriptionStatus.mockResolvedValue({ status: 'not_subscribed' });
});

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.clearAllMocks();
});

// ────────────────────────────────────────────────────────────────────────────────────────
// BLOCK 1 — the cards are operable by keyboard and named (Requirements 12.4, 20.1)
// ────────────────────────────────────────────────────────────────────────────────────────

describe('Marketplace: a card is a control, not a div with a handler', () => {
  it('names every listing card after its listing, and puts it in the tab order', async () => {
    mount();

    const catalogue = await screen.findByRole('button', { name: /Helios Reversion/i });
    const featured = await screen.findByRole('button', { name: /Aurora Momentum/i });

    // In the tab order — not merely clickable. `tabIndex` is what a keyboard user
    // reaches the card with, and `ds/DataTable`'s row activation is the precedent
    // Requirement 12.4 names.
    for (const card of [catalogue, featured]) {
      expect(card.tabIndex).toBe(0);
    }
  });

  it('opens the listing on Enter, with no pointer involved', async () => {
    const user = userEvent.setup();
    mount();

    const card = await screen.findByRole('button', { name: /Helios Reversion/i });
    card.focus();
    expect(document.activeElement).toBe(card);

    await user.keyboard('{Enter}');

    await waitFor(() => {
      expect(library.detail).toHaveBeenCalledWith(CATALOGUE.listing_id);
    });
  });

  it('opens the listing on Space as well, because a button does', async () => {
    const user = userEvent.setup();
    mount();

    const card = await screen.findByRole('button', { name: /Aurora Momentum/i });
    card.focus();

    await user.keyboard(' ');

    await waitFor(() => {
      expect(library.detail).toHaveBeenCalledWith(FEATURED.listing_id);
    });
  });

  it('keeps every filter, sort and search control named and operable', async () => {
    const user = userEvent.setup();
    mount();

    // Requirement 16.2/16.5: the rebuild removes no control. Each of these is queried by
    // its accessible name, so a control that lost its label fails here rather than
    // silently becoming a mystery box.
    const search = await screen.findByRole('textbox', { name: /search strategies/i });
    const sort = await screen.findByRole('combobox', { name: /sort listings/i });
    const all = await screen.findByRole('button', { name: /^All$/ });
    const category = await screen.findByRole('button', { name: /^trend/i });

    await user.type(search, 'momentum');
    await user.selectOptions(sort, 'rating');
    await user.click(category);
    await user.click(all);

    // The read is re-issued with the question the trader asked, and nothing about the
    // request shape changed (Requirement 10.7).
    await waitFor(() => {
      expect(library.browse).toHaveBeenCalled();
    });
    const params = library.browse.mock.calls.map(([p]) => p);
    expect(params.some((p) => p.sort === 'rating')).toBe(true);
    expect(params.some((p) => p.category === 'trend')).toBe(true);
    expect(params.every((p) => p.limit === 20)).toBe(true);
  });
});

// ────────────────────────────────────────────────────────────────────────────────────────
// BLOCK 2 — four failure classes, four answers (Requirements 11.2, 11.3)
// ────────────────────────────────────────────────────────────────────────────────────────

describe('Marketplace: a failed catalogue read says which failure it was', () => {
  beforeEach(() => {
    // The other three reads keep their own catch blocks and are not this block's subject;
    // emptying them keeps the only failure on screen the one under test.
    library.featured.mockResolvedValue({ items: [] });
    library.trending.mockResolvedValue({ items: [] });
  });

  for (const failure of FAILURE_CLASSES) {
    it(`renders authored copy when ${failure.name}`, async () => {
      library.browse.mockRejectedValue(failure.error());
      mount();

      await screen.findByText(failure.expected());

      const text = pageText();
      // Requirement 11.3: no exception text, no column name, no internal path.
      expect(text).not.toContain(LEAKY_MESSAGE);
      expect(text).not.toContain('UndefinedColumnError');
      expect(text).not.toContain('/api/library');
      // Requirement 11.2: and not the one string that used to serve all four.
      expect(text).not.toContain(ONE_STRING_FOR_EVERYTHING);
    });
  }

  it('gives the four classes four different answers, not one string four times', async () => {
    const rendered = [];

    for (const failure of FAILURE_CLASSES) {
      library.browse.mockRejectedValue(failure.error());
      mount();
      await screen.findByText(failure.expected());
      // The failure region only — a page-level diff would pass on the hero copy alone.
      const region = document.querySelector('[role="alert"], [data-panel-state="unavailable"]');
      expect(region).not.toBeNull();
      rendered.push((region.textContent || '').replace(/\s+/g, ' ').trim());
      cleanup();
      vi.clearAllMocks();
      library.featured.mockResolvedValue({ items: [] });
      library.trending.mockResolvedValue({ items: [] });
      library.categories.mockResolvedValue({ categories: [] });
      library.detail.mockResolvedValue({ ...CATALOGUE });
      library.subscriptionStatus.mockResolvedValue({ status: 'not_subscribed' });
    }

    expect(rendered).toHaveLength(4);
    expect(new Set(rendered).size).toBe(4);
  });

  it('offers a retry on a retryable failure and issues the same read again', async () => {
    const user = userEvent.setup();
    library.browse.mockRejectedValueOnce(apiError({ status: 500 }));
    mount();

    const retry = await screen.findByRole('button', { name: /try again/i });
    library.browse.mockResolvedValue({ items: [CATALOGUE], pages: 1 });
    await user.click(retry);

    await screen.findByRole('button', { name: /Helios Reversion/i });
    expect(library.browse.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});

// ────────────────────────────────────────────────────────────────────────────────────────
// BLOCK 3 — the 42703 case is unavailable, not empty (Requirement 11.4)
// ────────────────────────────────────────────────────────────────────────────────────────

describe('Marketplace: a schema fault is not an empty catalogue', () => {
  beforeEach(() => {
    library.featured.mockResolvedValue({ items: [] });
    library.trending.mockResolvedValue({ items: [] });
  });

  it('renders unavailable, naming what cannot be shown, on MARKETPLACE_READ_FAILED', async () => {
    // What is on the wire in production today: `library.py:720`/`:785` refuse to answer a
    // failed read with a zero-filled 200 and raise the structured code instead.
    library.browse.mockRejectedValue(
      apiError({ status: 500, code: 'MARKETPLACE_READ_FAILED', requestId: 'req-42703' }),
    );
    mount();

    await screen.findByText(CODE_COPY.MARKETPLACE_READ_FAILED.detail, { exact: false });

    const unavailable = document.querySelector('[data-panel-state="unavailable"]');
    expect(unavailable).not.toBeNull();

    const text = pageText();
    // "no strategies exist" and "we cannot read the catalogue" are different facts, and
    // only the first is the trader's to act on. The empty branch must not be on screen.
    expect(text).not.toContain('No strategies found matching your criteria');
    expect(text).not.toContain('No strategies match');
    expect(text).not.toContain(ONE_STRING_FOR_EVERYTHING);
    expect(text).not.toContain(LEAKY_MESSAGE);
  });

  it('keeps empty distinguishable from unavailable (Requirement 19.4)', async () => {
    library.browse.mockResolvedValue({ items: [], pages: 1 });
    mount();

    // A genuinely empty catalogue is `empty`, with copy that names what to do about it —
    // and it is NOT the unavailable state.
    await waitFor(() => {
      expect(document.querySelector('[data-empty-variant]')).not.toBeNull();
    });
    expect(document.querySelector('[data-panel-state="unavailable"]')).toBeNull();
  });
});

// ────────────────────────────────────────────────────────────────────────────────────────
// BLOCK 4 — featured and trending stay distinguishable by declared means (Req 10.5, D6)
// ────────────────────────────────────────────────────────────────────────────────────────

describe('Marketplace: featured and trending state their basis', () => {
  it('states the basis of each section rather than implying it per card', async () => {
    mount();

    // `is_featured` is an operator flag ordered by `published_at` (library.py:674) — no
    // performance basis. `trending` is `clone_count` desc then `avg_rating` desc
    // (library.py:759). The headings say so, once per section.
    await screen.findByRole('heading', { name: /selected by vyomquant/i });
    await screen.findByRole('heading', { name: /most cloned/i });
  });

  it('distinguishes a featured card from a trending one by elevation and border', async () => {
    mount();

    const [featured, trending, catalogue] = await waitFor(() => {
      const cards = ['featured', 'trending', 'catalogue'].map((kind) =>
        document.querySelector(`[data-listing-card="${kind}"]`),
      );
      cards.forEach((card) => expect(card).not.toBeNull());
      return cards;
    });

    // Declared tokens only: `--shadow-raised` against `--shadow-panel`,
    // `--color-line-strong` against `--color-line-default`. Both are in `tokens.css`,
    // both survive `no-colour-literals`' zero, and the distinction is a shape rather
    // than a hue (Requirement 20.5).
    expect(featured.className).toContain('shadow-raised');
    expect(featured.className).toContain('border-line-strong');
    expect(trending.className).toContain('shadow-panel');
    expect(trending.className).toContain('border-line-default');
    expect(catalogue.className).toContain('shadow-panel');
    expect(featured.className).not.toBe(trending.className);
  });

  it('marks a featured card with a word, not a gold pill beside a Sharpe ratio', async () => {
    mount();

    const featured = await waitFor(() => {
      const card = document.querySelector('[data-listing-card="featured"]');
      expect(card).not.toBeNull();
      return card;
    });

    // The editorial fact survives as text (Requirement 16.3 — the distinction the server
    // draws is not silently dropped), in `ds/StatusBadge`'s neutral group.
    const badge = featured.querySelector('[data-status-group]');
    expect(badge).not.toBeNull();
    expect(badge.getAttribute('data-status-group')).toBe('neutral');
    expect((badge.textContent || '').toLowerCase()).toContain('selected');

    // And the treatment that read as a claim about quality is gone: `bg-status-warning`
    // is the hue `design/semantic.js` reserves for a state a trader should act on.
    expect(featured.innerHTML).not.toContain('bg-status-warning');
    expect(featured.innerHTML).not.toContain('rounded-2xl');
    expect(featured.innerHTML).not.toContain('bg-gradient-to');
  });

  it('keeps the subscription badge reading design/subscriptionState.js (Requirement 10.6)', async () => {
    const user = userEvent.setup();
    library.subscriptionStatus.mockResolvedValue({
      status: 'active',
      expires_at: '2030-01-31T00:00:00Z',
    });
    mount();

    // Activated by pointer, so this holds before task 5.3 adds the keyboard path as well
    // as after it: Requirement 10.6 is about the module the badge reads, not about how the
    // card was reached.
    const card = await waitFor(() => {
      const found = document.querySelector('[data-listing-card="catalogue"]');
      expect(found).not.toBeNull();
      return found;
    });
    await user.click(card);

    // `resolveSubscriptionView`'s declared word for an ACTIVE row, carried by the one
    // primitive allowed to turn a state into a colour — never the raw column value.
    const badge = await waitFor(() => {
      const found = [...document.querySelectorAll('[data-status-group]')].find((el) =>
        /subscribed/i.test(el.textContent || ''),
      );
      expect(found).toBeTruthy();
      return found;
    });
    expect(badge.getAttribute('data-status-group')).not.toBe('');
    expect(pageText()).not.toContain('ACTIVE');
  });
});
