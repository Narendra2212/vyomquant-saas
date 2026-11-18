/**
 * ═══════════════════════════════════════════════════════════════════════════
 * tests/unit/pages/billingHistoryPaymentMetadata.test.jsx — BILL-HIST-02
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * WHAT WAS BROKEN
 * ---------------------------------------------------------------------------
 * The backend half of this landed first. Migration 070 declared the five columns the Razorpay
 * webhook writer had always named but `billing_invoices` never had — `provider`,
 * `provider_payment_id`, `amount_usd`, `amount_inr`, `plan` — so until it was applied every
 * invoice write failed silently inside a `try/except logger.warning` and the ledger held
 * nothing at all. BILL-HIST-01 then added `provider`, `providerPaymentId` and `plan` to
 * `get_invoices`'s projection, nullable on purpose for rows that predate the migration.
 *
 * `pages/Billing.jsx` threw all three away. `invoiceRows` projected six keys — id, reference,
 * fullReference, date, amount, denomination, status — and `INVOICE_COLUMNS` had four. So a
 * trader could see that ₹999 had been taken and could not see what it bought, which gateway
 * captured it, or any reference to quote when asking about it. Beside that, the page carried a
 * standing *Payment methods* panel whose read answers `[]` on every production account,
 * because VyomQuant stores no payment instrument: a permanent claim that this product keeps
 * your card on file and cannot currently read it. And the row list was cut with `.slice(0, 5)`
 * — no pagination, no count, nothing on screen admitting the cut — so a sixth invoice existed
 * nowhere in the product.
 *
 * WHAT THIS FILE HOLDS
 * ---------------------------------------------------------------------------
 * The three properties that are easy to lose in a later pass, each asserted in both
 * directions, because an assertion in one direction only is satisfiable by deleting the
 * feature:
 *
 *   1. The metadata REACHES the row when the server sends it (§2), and
 *   2. NOTHING IS INVENTED when the server does not (§3, §4, §6). A fabricated `'razorpay'`
 *      would be the single most plausible substitution on this page — it is the only gateway,
 *      so it would look right on every row and be checkable on none — and attributing the
 *      account's current card to a historical charge would be the same error one level down.
 *   3. The ledger is COMPLETE (§5). Every invoice the server sent is reachable, and the real
 *      total is on screen rather than implied by five rows.
 *
 * §1 asserts the removed panel is gone AND that the client method behind it still exists:
 * removing a dead reader is not removing the endpoint, and the difference is what a future
 * cleanup would blur.
 *
 * WHAT IS REAL AND WHAT IS DOUBLED
 * ---------------------------------------------------------------------------
 * `src/api` is module-mocked, which is this repo's convention for a page suite
 * (`degradedSurfaces.test.jsx`, `strategyMarketplace.test.jsx`). Every `ds/*` primitive is
 * REAL — `Panel`, `DataTable`, `NotAvailableMarker`, `StatusBadge` — because the rendered
 * markup is the subject, not a stand-in for it. The absence copy is read from
 * `design/pageFields.js` rather than retyped here: a test that restated the sentences would
 * pass against a page that had quietly changed what it tells a trader.
 *
 * NO SESSION IS INSTALLED, so `isAuthenticated()` is false and the subscription effect returns
 * before it opens anything. The socket is `billingSocketLifecycle.test.jsx`'s subject and
 * nothing here needs it; `websocketClient` is doubled only so an import cannot reach a real
 * one.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import fs from 'node:fs';
import path from 'node:path';

import { SRC, stripComments } from '../guards/source-scan.js';
import { PAGES, PAGE_FIELDS_BY_PAGE, VERDICT } from '../../../src/design/pageFields';

const { mockBilling, mockAuth } = vi.hoisted(() => ({
  mockBilling: {
    getPlans: vi.fn(),
    getEntitlements: vi.fn(),
    getInvoices: vi.fn(),
    getPaymentMethods: vi.fn(),
    createCheckout: vi.fn(),
    setCurrency: vi.fn(),
    cancelSubscription: vi.fn(),
    resumeSubscription: vi.fn(),
  },
  mockAuth: { getMe: vi.fn() },
}));

vi.mock('../../../src/api', () => {
  const api = { billing: mockBilling, auth: mockAuth };
  return {
    api,
    endpoints: api,
    default: api,
    // The page gates its socket effect on this. False, so the effect returns immediately.
    isAuthenticated: () => false,
    getToken: () => null,
  };
});

vi.mock('../../../src/websocketClient', () => ({
  default: {
    acquire: vi.fn(),
    release: vi.fn(),
    subscribeChannel: vi.fn(() => () => {}),
    subscribe: vi.fn(() => () => {}),
    onStatus: vi.fn(() => () => {}),
    onOpen: vi.fn(() => () => {}),
  },
}));

import Billing from '../../../src/pages/Billing';

// ---------------------------------------------------------------------------
// The declaration is the source of the copy, in the test as on the page
// ---------------------------------------------------------------------------

const BILLING_FIELD = Object.fromEntries(
  PAGE_FIELDS_BY_PAGE[PAGES.BILLING].map((field) => [field.field, field]),
);

/** The accessible name `ds/NotAvailableMarker` builds, so it is not retyped per assertion. */
const markerName = (field) => `${field.label}: not available`;

// ---------------------------------------------------------------------------
// Fixtures. Shaped as `GET /api/billing/invoices` answers, key for key.
// ---------------------------------------------------------------------------

/**
 * A row written by the Razorpay webhook after migration 070: every provider column filled.
 *
 * `providerPaymentId` is a payment IDENTIFIER, not a credential — it names a charge and
 * cannot authorise one — and this value is synthetic either way.
 */
const SETTLED_INVOICE = Object.freeze({
  id: 'a41f27c9-61b4-4d2f-9a07-7c1f5b0e3d88',
  date: '2026-10-04T08:12:44Z',
  amtUSD: null,
  amtINR: 999,
  status: 'paid',
  currency: 'INR',
  provider: 'razorpay',
  providerPaymentId: 'pay_SyNtH3t1cId',
  plan: 'pro',
});

/**
 * A row that predates the provider columns: the three provider fields are null.
 *
 * This is not a hypothetical shape. Migration 070 left all five columns nullable rather than
 * back-dating a `'razorpay'` and a `0` onto whatever was already there, so any row written
 * before it carries exactly these nulls — and `get_invoices` passes them through, because "a
 * null is honest where a fabricated 'razorpay' would not be".
 */
const LEGACY_INVOICE = Object.freeze({
  id: 'd0c8b5a2-1e77-4f30-bb19-6e4a2c9f0147',
  date: '2026-07-19T11:02:00Z',
  amtUSD: null,
  amtINR: 499,
  status: 'paid',
  currency: 'INR',
  provider: null,
  providerPaymentId: null,
  plan: null,
});

/** `count` settled invoices with distinct ids, newest first, as the server orders them. */
const ledgerOf = (count) =>
  Array.from({ length: count }, (_, index) => ({
    ...SETTLED_INVOICE,
    id: `${String(index).padStart(2, '0')}c8b5a2-1e77-4f30-bb19-6e4a2c9f0147`,
    date: `2026-${String(12 - (index % 12)).padStart(2, '0')}-04T08:12:44Z`,
    providerPaymentId: `pay_SyNth${String(index).padStart(3, '0')}`,
  }));

// ---------------------------------------------------------------------------
// Mount helpers
// ---------------------------------------------------------------------------

const mountBilling = async (invoices) => {
  mockBilling.getInvoices.mockResolvedValue(invoices);
  let view;
  await act(async () => {
    view = render(<Billing />);
  });
  // The reads resolve immediately; one flushed turn takes every panel to its resolved state.
  await act(async () => {});
  return view;
};

/**
 * The billing-history `ds/Panel`, so no assertion reads the whole page.
 *
 * Found by walking up to `ds/Panel`'s own `<section data-panel-state>` from its heading,
 * rather than by a class or a nesting depth: the panel's state is on that element, so this
 * also fails loudly if the heading ever stops being inside one.
 */
const historyPanel = () => {
  const panel = screen
    .getByRole('heading', { name: /billing history/i })
    .closest('[data-panel-state]');
  expect(panel, 'the billing history heading must sit inside a ds/Panel').toBeTruthy();
  return panel;
};

/** Body rows of the invoice table, in render order. `<thead>` rows excluded. */
const invoiceRowEls = () =>
  Array.from(document.querySelectorAll('tbody tr[data-row-id]'));

/** One row's cell for `key`, which `ds/DataTable` stamps with `data-column-key`. */
const cell = (rowEl, key) => rowEl.querySelector(`[data-column-key="${key}"]`);

beforeEach(() => {
  mockBilling.getPlans.mockResolvedValue({
    plans: [],
    currency: 'INR',
    currency_symbol: '₹',
  });
  mockBilling.getEntitlements.mockResolvedValue({
    plan: 'pro',
    features: [],
    quotas: {},
    usage: {},
    subscription_status: 'active',
    renewal_date: '2026-11-04T00:00:00Z',
    cancel_at_period_end: false,
  });
  mockBilling.getInvoices.mockResolvedValue([]);
  mockBilling.getPaymentMethods.mockResolvedValue([]);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

// ══════════════════════════════════════════════════════════════════════════
// 1. The standalone Payment methods panel is GONE — not hidden
// ══════════════════════════════════════════════════════════════════════════

describe('BILL-HIST-02: the stored-payment-method panel is removed', () => {
  it('renders no Payment methods panel in any read state', async () => {
    await mountBilling([SETTLED_INVOICE]);
    expect(screen.queryByRole('heading', { name: /payment methods/i })).toBeNull();
    // Its empty arm was the only state it could reach in production, so its headline is the
    // string most likely to survive a half-removal.
    expect(screen.queryByText(/no payment methods on file/i)).toBeNull();
  });

  it('claims no card brand, masked card number or expiry anywhere on the page', async () => {
    await mountBilling([SETTLED_INVOICE, LEGACY_INVOICE]);
    const page = document.body.textContent;
    expect(page, 'the dot-mask only ever framed a stored last four').not.toMatch(/••••/);
    expect(page).not.toMatch(/\bvisa\b|\bmastercard\b|\brupay\b|\bamex\b/i);
    expect(
      screen.queryByLabelText(markerName(BILLING_FIELD.cardLast4)),
      'a marker for a stored card is still a claim that one is kept here',
    ).toBeNull();
    expect(screen.queryByLabelText(markerName(BILLING_FIELD.cardExpiry))).toBeNull();
  });

  it('issues no stored-methods read on mount', async () => {
    await mountBilling([SETTLED_INVOICE]);
    expect(
      mockBilling.getPaymentMethods,
      'a read whose response reaches no panel is a request for nothing',
    ).not.toHaveBeenCalled();
    // The three reads that DO render something still fire, exactly once each.
    expect(mockBilling.getPlans).toHaveBeenCalledTimes(1);
    expect(mockBilling.getEntitlements).toHaveBeenCalledTimes(1);
    expect(mockBilling.getInvoices).toHaveBeenCalledTimes(1);
  });

  it('keeps the endpoint and its client method, because only the reader was dead', () => {
    /*
      Without this the test above is satisfied by deleting the payment-methods integration
      outright. `GET /api/billing/payment-methods` is an honest endpoint, older desktop builds
      call it, and a deployment that one day stores an instrument needs it back.
    */
    const clientModule = fs.readFileSync(
      path.join(SRC, 'api', 'modules', 'billing.js'),
      'utf8',
    );
    expect(stripComments(clientModule)).toMatch(/^\s*getPaymentMethods\s*:/m);
    expect(stripComments(clientModule)).toMatch(/^\s*deletePaymentMethod\s*:/m);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 2. The metadata the API already sends reaches the row
// ══════════════════════════════════════════════════════════════════════════

describe('BILL-HIST-02: a settled invoice carries its payment metadata', () => {
  it('states the plan the charge bought', async () => {
    await mountBilling([SETTLED_INVOICE]);
    const [row] = invoiceRowEls();
    expect(within(cell(row, 'plan')).getByText('Pro')).toBeTruthy();
  });

  it('states the gateway that captured the charge', async () => {
    await mountBilling([SETTLED_INVOICE]);
    const [row] = invoiceRowEls();
    expect(within(cell(row, 'payment')).getByText('Razorpay')).toBeTruthy();
  });

  it('shows the gateway payment reference masked, with the whole value reachable', async () => {
    await mountBilling([SETTLED_INVOICE]);
    const [row] = invoiceRowEls();
    const payment = cell(row, 'payment');

    // Truncated for the cell (design.md §2.5 mechanism 4 permits it for an IDENTIFIER)…
    const masked = within(payment).getByTitle(SETTLED_INVOICE.providerPaymentId);
    expect(masked.textContent).toBe('pay_SyNt…');
    expect(masked.textContent.length).toBeLessThan(SETTLED_INVOICE.providerPaymentId.length);
    // …and the full reference is what a trader quotes, so it must still be obtainable.
    expect(masked.getAttribute('title')).toBe(SETTLED_INVOICE.providerPaymentId);
  });

  it('keeps the four original columns reading as they did', async () => {
    await mountBilling([SETTLED_INVOICE]);
    const [row] = invoiceRowEls();
    // The reference, truncated, with the whole id on `title`.
    expect(within(cell(row, 'reference')).getByTitle(SETTLED_INVOICE.id)).toBeTruthy();
    // The amount denominated by the ROW's own currency, never by a `$` chosen by elimination.
    expect(cell(row, 'amount').textContent).toBe('999 INR');
    expect(cell(row, 'date').textContent.trim()).not.toBe('');
    expect(cell(row, 'status').textContent).toMatch(/paid/i);
  });

  it('never reads the account plan into an invoice row', async () => {
    /*
      `GET /api/billing/entitlements` says this account is on `pro` NOW. An invoice is a
      statement about a charge THEN, so a row whose own `plan` is null must not borrow it: a
      charge for a tier the account has since left would be relabelled to match the present.

      Queried for the RENDERED value rather than against the cell's text, because the cell's
      text includes the marker's `sr-only` reason — and that sentence contains the word
      "provider", which a `/pro/i` match on `textContent` would hit. Asserting on substrings
      of declared copy is how an absence test passes for the wrong reason.
    */
    await mountBilling([LEGACY_INVOICE]);
    const [row] = invoiceRowEls();
    expect(within(cell(row, 'plan')).queryByText('Pro')).toBeNull();
    expect(
      within(cell(row, 'plan')).getByLabelText(markerName(BILLING_FIELD.invoicePlan)),
    ).toBeTruthy();
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 3. A row that predates the metadata says so, and invents nothing
// ══════════════════════════════════════════════════════════════════════════

describe('BILL-HIST-02: a pre-migration row renders an explicit absence', () => {
  it.each([
    ['plan', 'invoicePlan'],
    ['payment', 'invoiceProvider'],
    ['payment', 'invoicePaymentReference'],
  ])('%s carries the declared marker for %s', async (columnKey, fieldName) => {
    await mountBilling([LEGACY_INVOICE]);
    const [row] = invoiceRowEls();
    const field = BILLING_FIELD[fieldName];

    const marker = within(cell(row, columnKey)).getByLabelText(markerName(field));
    // Requirement 19.3: the em-dash is the glyph, the REASON is the load-bearing half.
    expect(marker.getAttribute('title')).toBe(field.reason);
    expect(marker.getAttribute('title').length).toBeGreaterThan(20);
  });

  it('never fills the gateway in on the row\'s behalf', async () => {
    /*
      Razorpay being the only gateway is exactly why defaulting to it is unsafe: it would look
      right on every row and be checkable on none.

      Asserted against the RENDERED value, not the cell's text. Two declared reasons quote the
      word themselves — the provider reason says `"Razorpay" is not filled in on their behalf`
      and the instrument reason names Razorpay Checkout — and both are in the cell as `sr-only`
      copy, so a `textContent` match would fail on honest copy and pass on a stripped page.
    */
    await mountBilling([LEGACY_INVOICE]);
    const [row] = invoiceRowEls();
    const payment = cell(row, 'payment');

    expect(within(payment).queryByText('Razorpay')).toBeNull();
    expect(
      within(payment).getByLabelText(markerName(BILLING_FIELD.invoiceProvider)),
    ).toBeTruthy();
    // And no payment reference is conjured either, in any shortened form.
    expect(within(payment).queryByTitle(/^pay_/)).toBeNull();
  });

  it('renders no zero, blank or placeholder in place of the absent fields', async () => {
    await mountBilling([LEGACY_INVOICE]);
    const [row] = invoiceRowEls();
    for (const key of ['plan', 'payment']) {
      const text = cell(row, key).textContent;
      expect(text.trim(), `${key} must not be blank — a blank cell states nothing`).not.toBe('');
      expect(text, `${key} must not substitute a zero`).not.toMatch(/\b0\b/);
      expect(text).not.toMatch(/\bN\/A\b|\bnull\b|\bundefined\b|\bunknown\b/i);
    }
  });

  it('tells an absent field apart from a present one in the same column', async () => {
    // The whole point of §6 of design.md: a figure that is genuinely there and one that could
    // not be read must not be the same pixels. Both rows, one column, one mount.
    await mountBilling([SETTLED_INVOICE, LEGACY_INVOICE]);
    const [settled, legacy] = invoiceRowEls();
    expect(within(cell(settled, 'plan')).getByText('Pro')).toBeTruthy();
    expect(
      within(cell(legacy, 'plan')).getByLabelText(markerName(BILLING_FIELD.invoicePlan)),
    ).toBeTruthy();
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 4. The instrument is never claimed — on ANY row
// ══════════════════════════════════════════════════════════════════════════

describe('BILL-HIST-02: no row claims which card or UPI handle paid', () => {
  it('states the instrument absence on a fully populated row too', async () => {
    /*
      This is the assertion that stops the obvious "improvement". The stored-methods read
      reports a brand and a last four, and joining TODAY'S default instrument onto a
      HISTORICAL invoice would attribute a charge to a card that may not have existed when it
      was taken. `billing_invoices` has no instrument column at all, so the honest rendering on
      a row where everything else IS known is still an absence with a reason.
    */
    await mountBilling([SETTLED_INVOICE]);
    const [row] = invoiceRowEls();
    const field = BILLING_FIELD.invoiceInstrument;

    expect(field.verdict).toBe(VERDICT.UNAVAILABLE);
    const marker = within(cell(row, 'payment')).getByLabelText(markerName(field));
    expect(marker.getAttribute('title')).toBe(field.reason);
    expect(field.reason).toMatch(/razorpay checkout/i);
  });

  it('declares the instrument at no path, so no page can read one', () => {
    // An UNAVAILABLE entry with a path would invite exactly the join the test above forbids.
    expect(BILLING_FIELD.invoiceInstrument.path).toBeNull();
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 5. The whole ledger is reachable — the 5-row cap is gone
// ══════════════════════════════════════════════════════════════════════════

describe('BILL-HIST-02: the ledger is paginated, not truncated', () => {
  const LEDGER_SIZE = 13;

  it('renders more than the five rows the old cap allowed', async () => {
    await mountBilling(ledgerOf(LEDGER_SIZE));
    expect(
      invoiceRowEls().length,
      'a billing ledger silently cut to five rows hid every older charge',
    ).toBeGreaterThan(5);
  });

  it('reports the real total, so a reader knows the page is one of several', async () => {
    await mountBilling(ledgerOf(LEDGER_SIZE));
    // `ds/DataTable`'s footer count is a live region, which is what makes the truncation
    // visible instead of silent.
    const status = within(historyPanel()).getByRole('status');
    expect(status.textContent).toMatch(new RegExp(`of ${LEDGER_SIZE}\\b`));
  });

  it('reaches every invoice the server sent by walking the pages', async () => {
    const ledger = ledgerOf(LEDGER_SIZE);
    await mountBilling(ledger);

    const seen = new Set();
    const collect = () => invoiceRowEls().forEach((row) => seen.add(row.dataset.rowId));
    collect();

    const nextPage = () => screen.queryByRole('button', { name: /next page/i });
    // Bounded by the ledger itself, so a broken control cannot spin here.
    for (let guard = 0; guard <= LEDGER_SIZE && nextPage() && !nextPage().disabled; guard += 1) {
      const button = nextPage();
      // eslint-disable-next-line no-await-in-loop
      await act(async () => {
        button.click();
      });
      collect();
    }

    expect(seen.size).toBe(LEDGER_SIZE);
    for (const invoice of ledger) {
      expect(seen.has(invoice.id), `invoice ${invoice.id} is unreachable`).toBe(true);
    }
  });

  it('returns to the first page when the ledger is read again', async () => {
    /*
      A refetch can shorten the ledger. `ds/DataTable` clamps a stale page number for its own
      render, so the distinguishing case is a shorter ledger that STILL has the page the reader
      was on: 26 invoices is three pages, 13 is two, and page 3 clamps to 2 rather than to 1.
      Without the reset the trader lands on page 2 of a ledger they never navigated.
    */
    await mountBilling(ledgerOf(26));
    const footer = () => within(historyPanel()).getByRole('status').textContent;

    const next = () => screen.getByRole('button', { name: /next page/i });
    await act(async () => { next().click(); });
    await act(async () => { next().click(); });
    expect(footer(), 'the walk must actually reach the last page').toMatch(/of 26$/);
    expect(footer()).toMatch(/^Showing 25/);

    mockBilling.getInvoices.mockResolvedValue(ledgerOf(LEDGER_SIZE));
    await act(async () => {
      screen.getByRole('button', { name: /refresh/i }).click();
    });
    await waitFor(() => {
      expect(footer()).toMatch(new RegExp(`of ${LEDGER_SIZE}$`));
    });
    expect(footer(), 'a shorter ledger must open at its first page').toMatch(/^Showing 1–/);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 6. No receipt link is invented, and the empty ledger still says the right thing
// ══════════════════════════════════════════════════════════════════════════

describe('BILL-HIST-02: the receipt absence is stated, not linked around', () => {
  it('renders no outbound link from the ledger', async () => {
    await mountBilling([SETTLED_INVOICE]);
    const links = Array.from(historyPanel().querySelectorAll('a[href]'));
    expect(
      links.map((a) => a.getAttribute('href')),
      'the response carries no receipt URL, and a Razorpay console link would point at a '
        + 'merchant-side page a customer cannot sign in to',
    ).toEqual([]);
  });

  it('states once why there is no receipt to download', async () => {
    await mountBilling([SETTLED_INVOICE]);
    const field = BILLING_FIELD.invoiceReceipt;
    expect(field.verdict).toBe(VERDICT.UNAVAILABLE);
    expect(within(historyPanel()).getByText(field.reason)).toBeTruthy();
  });

  it('renders the sentence once for the ledger, not once per row', async () => {
    await mountBilling(ledgerOf(4));
    expect(screen.getAllByText(BILLING_FIELD.invoiceReceipt.reason)).toHaveLength(1);
  });
});

describe('BILL-HIST-02: the empty ledger', () => {
  it('says nothing has been billed, and names what a row will carry', async () => {
    /*
      `billing_invoices` held zero rows in production when this was written, so this is the
      state most accounts see. It has to be a statement about the ACCOUNT — `ds/Panel`'s
      separate `error` arm is what says the read failed, and collapsing the two is the defect
      Property 9 exists for.
    */
    await mountBilling([]);
    expect(within(historyPanel()).getByText(/no invoices yet/i)).toBeTruthy();
    expect(historyPanel().textContent).toMatch(/payment reference/i);
    expect(invoiceRowEls()).toHaveLength(0);
  });

  it('distinguishes an empty ledger from a failed read', async () => {
    mockBilling.getInvoices.mockRejectedValue(new Error('invoice read failed'));
    await act(async () => {
      render(<Billing />);
    });
    await act(async () => {});
    expect(screen.queryByText(/no invoices yet/i)).toBeNull();
    expect(within(historyPanel()).getByRole('alert')).toBeTruthy();
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 7. The dead portal is gone from the declaration and the endpoint map
// ══════════════════════════════════════════════════════════════════════════

describe('BILL-HIST-02: nothing points a trader at a portal that does not exist', () => {
  it('composes no billing/portal URL in utils/endpoints.js', () => {
    const source = stripComments(fs.readFileSync(path.join(SRC, 'utils', 'endpoints.js'), 'utf8'));
    expect(source).not.toMatch(/billing\/portal/);
    expect(source, 'a dead URL in a shared map reads as a live capability').not.toMatch(
      /^\s*portal\s*:/m,
    );
  });

  it('leaves no billing absence copy naming a provider portal', () => {
    /*
      `POST /api/billing/portal` answers 501 to every caller and BILLING-PORTAL-02 removed the
      two controls that reached it. Six declared `reason` sentences still told a trader to
      open it — "Manage billing opens the payment provider's portal…" — which is a page's
      absence copy sending them to a button that is not there, for a portal Razorpay does not
      publish.
    */
    const offenders = PAGE_FIELDS_BY_PAGE[PAGES.BILLING]
      .filter((field) => /portal|manage billing/i.test(field.reason ?? ''))
      .map((field) => field.field);
    expect(offenders).toEqual([]);
  });

  it('gives every new invoice-metadata entry a reason a trader can act on', () => {
    for (const name of [
      'invoicePlan',
      'invoiceProvider',
      'invoicePaymentReference',
      'invoiceInstrument',
      'invoiceReceipt',
    ]) {
      const field = BILLING_FIELD[name];
      expect(field, `${name} must be declared`).toBeTruthy();
      expect(field.reason.trim().length, name).toBeGreaterThan(20);
    }
  });
});
