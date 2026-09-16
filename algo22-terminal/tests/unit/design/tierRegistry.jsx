/**
 * @fileoverview The Property 4 tier registry — `{hierarchy, render adapter}` pairs.
 *
 * vyomquant-ui-redesign task 16.3a. design.md §7, §18. Requirements 3.1, 3.2, 3.4, 6.2, 6.3,
 * 7.1, 7.2, 7.3, 10.1, 10.2. Property P4.
 *
 * `src/design/pageHierarchy.js` declares the four tiered pages as data. It cannot render them,
 * and Property 4's two clauses — every tier-*n* element precedes every tier-*(n+1)* element, and
 * no tier-1 element renders outside the page's single tier-1 container — are claims about a
 * rendered DOM. This module is the missing half: for each declared hierarchy, the adapter that
 * puts that page on screen with a payload the property chose.
 *
 * Today it holds ONE entry, Portfolio (task 16.1/16.2). Tasks 19.5, 20.4 and 23.5 add Dashboard,
 * Live Trading and Backtester — see {@link PENDING_TIER_PAGES}, which names them and their tasks
 * so an unregistered page is a recorded gap rather than an omission nobody can see.
 *
 * ═══════════════════════════════════════════════════════════════════════════════════════
 * THE ENTRY CONTRACT
 * ═══════════════════════════════════════════════════════════════════════════════════════
 *
 * An entry is a frozen object with exactly these members. Registering a page means writing one
 * of these and its file-scope mocks; it means changing nothing else.
 *
 *   page            A `PAGES` value. Equal to `hierarchy.page` — {@link assertTierEntry} checks
 *                   it, because two spellings of one page in one entry is a registry that
 *                   selects containers for a page it did not render.
 *
 *   hierarchy       The `PAGE_HIERARCHY_BY_PAGE` entry for `page`. Passed rather than looked up
 *                   by the property, so the pairing is what the registry declares.
 *
 *   requiredMocks   `[{module, why}]` — the modules the CONSUMING TEST FILE must `vi.mock` at
 *                   file scope for this entry to mount. `vi.mock` is hoisted per file and a
 *                   `vi.mock` inside this module would not apply to its importer, so the mock
 *                   cannot live here. What lives here is the DECLARATION of it: a consumer that
 *                   forgets one gets a mount that fails for jsdom's reasons rather than a
 *                   mysterious hang, and a reviewer can see what a page costs before adding it.
 *
 *   payloadFields   The tier field keys `payload()` can place a value for, in tier order. This
 *                   is the property's generator domain: part (b) builds one `fast-check` record
 *                   over these keys and hands it to `payload()`.
 *
 *   payload(values) Build this page's read payload(s) from `values`, a map of
 *                   `payloadFields` key → the value to place at that field's read path.
 *                   `{}` builds the healthy fixture. A key mapped to {@link ABSENT} deletes the
 *                   path, which is not the same reading as `null` and must be generable
 *                   separately. The RESULT IS OPAQUE to the property: it is handed straight back
 *                   to `mount()`. That is what lets Live Trading's payload be four bodies and a
 *                   socket frame while Portfolio's is one body and three series, with no
 *                   per-page branch in the property.
 *
 *   mount(payload)  ASYNC. Installs this page's reads from `payload`, renders it, and resolves
 *                   only once the page has finished settling — see below. Resolves to a
 *                   {@link TierMount}. Async and not a synchronous `render` because Portfolio
 *                   needs one read and Live Trading needs four plus a socket double: a
 *                   synchronous adapter fits exactly one of the four pages.
 *
 * WHAT "SETTLED" MEANS, AND WHY IT IS NOT "THE TIER-1 CONTAINER APPEARED"
 * ----------------------------------------------------------------------
 * `mount` resolves when the page's reads have landed and its tiers are on screen — OR when the
 * page has decided it has none. The second half is not a hedge, it is the property's whole
 * input space: Property 4 generates absent, `null`, zero and wrongly-typed fields, and §7.4's
 * rule for a tier-1 row on an unreadable read is that the figures are NOT RENDERED AT ALL
 * (Requirement 6.6 says the same for Backtester). A `mount` that waited for
 * `[data-page-tier="1"]` would hang on every payload that legitimately produces no tier 1, and
 * a five-second `waitFor` timeout times 300 runs is an hour of red. So each entry supplies
 * `settled()`, a predicate over the DOM meaning "no region on this page is still loading", and
 * both clauses of Property 4 hold vacuously — and are asserted to hold vacuously — for a page
 * that rendered no tiers.
 *
 * WHERE THE PAYLOAD GENERATOR GOES
 * --------------------------------
 * The VALUE domain belongs to the property: absent / `null` / `0` / wrong-type is one
 * `fast-check` arbitrary and it is the same arbitrary for all four pages. The PLACEMENT belongs
 * to the entry: only Portfolio's entry knows that `investedCapital` is written at
 * `overview.used_balance` and only Live Trading's will know which of its four bodies a field is
 * on. `payloadFields` + `payload(values)` is that seam, and it is why part (b) needs no page
 * knowledge at all.
 *
 * HOUSE RULES THIS MODULE KEEPS
 * -----------------------------
 * Every selector is built from `pageHierarchy`'s declared attributes — `data-page`,
 * `data-page-tier`, `data-metric-tier` — and there is no class name anywhere. A refactor that
 * renames a utility class must not fail Property 4, and one that drops a tier attribute must.
 */

import { act, cleanup, render, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';

import Portfolio from '../../../src/pages/Portfolio';
import * as dashboardModule from '../../../src/api/modules/dashboard';
import * as portfolioModule from '../../../src/api/modules/portfolio';
import { PAGES } from '../../../src/design/pageFields';
import {
  METRIC_TIER_ATTRIBUTE,
  PAGE_HIERARCHY_BY_PAGE,
  TIERS,
  tierSelector,
} from '../../../src/design/pageHierarchy';

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE PAYLOAD VOCABULARY
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/**
 * "This field is not on the body at all."
 *
 * A sentinel and not `undefined`, because `{total_value: undefined}` and a body with no
 * `total_value` key are different objects and only one of them is what a server that omitted a
 * figure sends. `ds/Metric` renders the not-available marker for both, but a page that reads
 * `Object.hasOwn` would tell them apart, so the generator has to be able to produce each.
 */
export const ABSENT = Symbol('tierRegistry.ABSENT');

/**
 * Write `value` at a dotted `path` on a plain object, or delete the leaf for {@link ABSENT}.
 *
 * Deliberately dumb: no array indices, no `[]` segments. Every path in
 * {@link PORTFOLIO_PAYLOAD_PATHS} is a dotted scalar path, and a payload builder that quietly
 * accepted `positions[].contracts` would place a value nothing reads.
 */
const placeAt = (body, path, value) => {
  const segments = path.split('.');
  const leaf = segments.pop();
  let cursor = body;
  for (const segment of segments) {
    if (cursor[segment] === null || typeof cursor[segment] !== 'object') cursor[segment] = {};
    cursor = cursor[segment];
  }
  if (value === ABSENT) delete cursor[leaf];
  else cursor[leaf] = value;
  return body;
};

/* ══════════════════════════════════════════════════════════════════════════════════════
 * PORTFOLIO — §7.6, Requirements 10.1, 10.2, 10.3
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/**
 * Where each of Portfolio's generable fields is written on `GET /api/dashboard`.
 *
 * Spelled here, in the fixture, rather than read out of `pageFields.path`. A payload builder that
 * derived its paths from the module under test would agree with any re-pointing of a field,
 * including a wrong one — `tests/unit/lib/signalTraceStages.property.test.js`'s convention, and
 * the reason `portfolio-rendering.test.jsx` asserts `declared('availableBalance').path` as a
 * literal too. `tierRegistry.test.jsx` cross-checks every entry below against the declaration,
 * so drift is a failure in one named test rather than a property that passes over the wrong body.
 *
 * `investedCapital` is the one field whose key is not its path: it is DERIVED, `pageFields`
 * gives it no `path` at all, and `overview.used_balance` is the input the server computes it
 * from (§7.6). Placing a value there is placing it where the figure comes from.
 */
export const PORTFOLIO_PAYLOAD_PATHS = Object.freeze({
  totalValue: 'overview.total_value',
  availableBalance: 'overview.available_balance',
  investedCapital: 'overview.used_balance',
  unrealisedPnl: 'overview.unrealized_pnl',
  realisedPnlToday: 'overview.today_realized_pnl',
  lifetimeRealizedPnl: 'overview.realized_pnl',
  totalExposure: 'overview.total_exposure',
  currentDrawdown: 'risk.current_drawdown_pct_v2',
});

/**
 * The healthy `GET /api/dashboard` body, as far as this page reads one.
 *
 * The same shape and the same figures as `portfolio-rendering.test.jsx`'s fixture, for the same
 * reason: `degraded: null` and a counted `risk.open_positions_count` are the HEALTHY readings
 * and a body that omits them is not the healthy case. `risk.current_drawdown_pct` carries a
 * different number from its `_v2` neighbour on purpose — BC-1 left the deprecated field on the
 * wire, and a page reading it would render 12.5% here.
 */
const portfolioHealthyBody = () => ({
  positions: [
    {
      id: 'pos_binance_btc_usdt',
      symbol: 'BTC/USDT',
      exchange_id: 'binance',
      side: 'long',
      contracts: 0.5,
      entry_price: 60000,
      mark_price: 63000,
      notional: 31500,
      leverage: 5,
      unrealized_pnl: 1500,
      liquidation_price: 48000,
      margin: 6300,
      margin_type: 'cross',
    },
  ],
  degraded: null,
  overview: {
    total_value: 65000,
    available_balance: 40000,
    used_balance: 25000,
    unrealized_pnl: 1500,
    today_realized_pnl: 800,
    realized_pnl: 12345.5,
    total_exposure: 30000,
    currency: 'USDT',
  },
  risk: {
    open_positions_count: 1,
    current_drawdown_pct_v2: 3.2,
    current_drawdown_pct: 12.5,
  },
});

/** The three tier-3 reads, which answer BARE ARRAYS — no envelope. */
const portfolioHealthySeries = () => ({
  allocation: [{ asset: 'BTC', pct: 62.5 }, { asset: 'USDT', pct: 37.5 }],
  equityCurve: [
    { timestamp: '2024-03-11', equity: 61000 },
    { timestamp: '2024-03-12', equity: 65000 },
  ],
  heatmap: [{ date: '2024-03-12', pnl_usd: 800 }],
});

/**
 * Portfolio's payload: the one dashboard body its tier 1 and tier 2 are built from, plus the
 * three tier-3 series.
 *
 * Four reads on the LIVE ledger, one payload. The page issues them in a single
 * `Promise.allSettled`, so a property that varied them independently would be varying something
 * the page cannot observe separately.
 */
const portfolioPayload = (values = {}) => {
  const dashboard = portfolioHealthyBody();
  for (const [field, value] of Object.entries(values)) {
    const path = PORTFOLIO_PAYLOAD_PATHS[field];
    if (path === undefined) {
      throw new Error(
        `tierRegistry: portfolio payload was given "${field}", which is not one of its `
          + `payloadFields (${Object.keys(PORTFOLIO_PAYLOAD_PATHS).join(', ')}). A generated `
          + 'field with nowhere to go would be a run that varied nothing.',
      );
    }
    placeAt(dashboard, path, value);
  }
  return { dashboard, ...portfolioHealthySeries() };
};

/**
 * Portfolio's reads, installed as spies over the api modules.
 *
 * `portfolioApi.getSummary` is spied to THROW: §7.6 is that `GET /api/portfolio/summary` carries
 * neither `available_balance` nor a drawdown, so tier 1 cannot be assembled from it and task
 * 16.1 stopped calling it. A registry that let the call succeed would hide a regression to it.
 */
const installPortfolioReads = (payload) => {
  vi.spyOn(portfolioModule.portfolioApi, 'getSummary').mockImplementation(() => {
    throw new Error('Portfolio must not read GET /api/portfolio/summary (§7.6, task 16.1).');
  });
  vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockResolvedValue(payload.dashboard);
  vi.spyOn(portfolioModule.portfolioApi, 'getAllocation').mockResolvedValue(payload.allocation);
  vi.spyOn(portfolioModule.portfolioApi, 'getEquityCurve').mockResolvedValue(payload.equityCurve);
  vi.spyOn(portfolioModule.portfolioApi, 'getHeatmap').mockResolvedValue(payload.heatmap);
};

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE MOUNT
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/**
 * "No region on this page is still loading."
 *
 * `ds/Panel` writes `data-panel-state`, so this is decidable from the DOM without reaching into
 * the page's state. It is true both when the tiers rendered and when the page decided it has
 * none, which is exactly the settle Property 4 needs — see the module docblock.
 */
const noPanelLoading = () => document.querySelector('[data-panel-state="loading"]') === null;

/**
 * @typedef {object} TierMount
 * @property {string} page
 * @property {import('@testing-library/react').RenderResult} view The render result, for `within`.
 * @property {(tier: number) => Element|null} tierContainer This page's container for one tier.
 * @property {() => Array<{tier: number, element: Element}>} metrics Every `ds/Metric` root on the
 *   page in DOCUMENT ORDER, with the tier it declares. Document order is what Property 4's first
 *   clause is about, and `querySelectorAll` returns it.
 * @property {() => void} unmount Unmounts and restores the spies this mount installed.
 */

/**
 * Render one page, settle it, and hand back DOM accessors keyed on the declared attributes.
 *
 * `MemoryRouter` unconditionally, for every page. Portfolio does not need one today; Live
 * Trading does, and a registry where the wrapper is per-entry is a registry where adding a page
 * means editing the mount. One wrapper, no branch.
 */
const mountPage = async (page, Component, payload, installReads) => {
  installReads(payload);

  let view;
  await act(async () => {
    view = render(<MemoryRouter><Component /></MemoryRouter>);
  });

  await waitFor(() => {
    if (!noPanelLoading()) throw new Error(`${page} is still loading a region.`);
  });

  return {
    page,
    view,
    tierContainer: (tier) => document.querySelector(tierSelector(page, tier)),
    metrics: () => [...document.querySelectorAll(`[${METRIC_TIER_ATTRIBUTE}]`)].map((element) => ({
      tier: Number(element.getAttribute(METRIC_TIER_ATTRIBUTE)),
      element,
    })),
    unmount: () => {
      cleanup();
      vi.restoreAllMocks();
    },
  };
};

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE ENTRIES
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/** §7.6 Portfolio — one read, no socket, no router dependency. The simple case. */
export const PORTFOLIO_TIER_ENTRY = Object.freeze({
  page: PAGES.PORTFOLIO,
  hierarchy: PAGE_HIERARCHY_BY_PAGE[PAGES.PORTFOLIO],
  requiredMocks: Object.freeze([
    Object.freeze({
      module: 'src/components/ds/Chart',
      why: 'Tier 3 is three charts. recharts needs layout APIs jsdom does not implement, and '
        + '`Portfolio.jsx` reaches it through `lazy(() => import("../components/ds/Chart"))`, so '
        + 'a `vi.mock("recharts", …)` would have to enumerate every export the lazy chunk '
        + 'resolves. Stub the module the page imports instead — `portfolio-rendering.test.jsx`\'s '
        + 'approach since task 16.2. Property 4 asserts nothing about a chart\'s interior.',
    }),
  ]),
  payloadFields: Object.freeze(Object.keys(PORTFOLIO_PAYLOAD_PATHS)),
  payload: portfolioPayload,
  mount: (payload) => mountPage(PAGES.PORTFOLIO, Portfolio, payload, installPortfolioReads),
});

/**
 * The registry. Seeded with Portfolio (task 16.3), grown by tasks 19.5, 20.4 and 23.5.
 */
export const TIER_REGISTRY = Object.freeze([PORTFOLIO_TIER_ENTRY]);

/** One entry by `PAGES` value. `undefined` for a page nobody has registered yet. */
export const TIER_REGISTRY_BY_PAGE = Object.freeze(
  Object.fromEntries(TIER_REGISTRY.map((entry) => [entry.page, entry])),
);

/**
 * The declared-but-unregistered pages, with the task that registers each.
 *
 * A gap Property 4 can report rather than one it cannot see. `pageHierarchy` declares four
 * hierarchies and this registry holds one adapter; without this list, "Property 4 covers
 * Portfolio" and "Property 4 covers every tiered page" look identical from inside the suite.
 */
export const PENDING_TIER_PAGES = Object.freeze([
  Object.freeze({ page: PAGES.DASHBOARD, task: '19.5' }),
  Object.freeze({ page: PAGES.LIVE_TRADING, task: '20.4' }),
  Object.freeze({ page: PAGES.BACKTESTER, task: '23.5' }),
]);

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE CONTRACT, CHECKED
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/** The members every entry has, so a new entry cannot be half-written. */
export const TIER_ENTRY_MEMBERS = Object.freeze([
  'page',
  'hierarchy',
  'requiredMocks',
  'payloadFields',
  'payload',
  'mount',
]);

/**
 * Throw unless `entry` satisfies the contract in the module docblock.
 *
 * Called by `tierRegistry.test.jsx` over every entry, and worth calling from part (b) too: an
 * entry whose `page` and `hierarchy.page` disagree selects tier containers for a page it did not
 * render, and every clause of Property 4 then passes over an empty node list.
 */
export const assertTierEntry = (entry) => {
  for (const member of TIER_ENTRY_MEMBERS) {
    if (entry[member] === undefined) {
      throw new Error(`tierRegistry: an entry is missing "${member}".`);
    }
  }
  if (entry.hierarchy.page !== entry.page) {
    throw new Error(
      `tierRegistry: entry for "${entry.page}" carries the hierarchy for `
        + `"${entry.hierarchy.page}".`,
    );
  }
  if (typeof entry.payload !== 'function' || typeof entry.mount !== 'function') {
    throw new Error(`tierRegistry: ${entry.page}'s payload and mount must both be functions.`);
  }
  if (entry.payloadFields.length === 0) {
    throw new Error(
      `tierRegistry: ${entry.page} declares no payloadFields, so a property over it would `
        + 'vary nothing.',
    );
  }
  return entry;
};

/**
 * The tiers one hierarchy actually declares, ascending.
 *
 * §18 gives three tiers and §7.1's Dashboard has only two, so "the tiers of this page" is a
 * per-page answer read off the declaration — not `TIERS`, which is the domain.
 */
export const declaredTiersOf = (hierarchy) =>
  Object.freeze(TIERS.filter((tier) => hierarchy.tiers.some((field) => field.tier === tier)));

/** One hierarchy's fields for one tier, in declaration order. */
export const tierFieldsOf = (hierarchy, tier) =>
  hierarchy.tiers.filter((field) => field.tier === tier);
