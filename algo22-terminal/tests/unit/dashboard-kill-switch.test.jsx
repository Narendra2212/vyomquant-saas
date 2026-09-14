/**
 * @fileoverview Dashboard — the kill switch's confirmation surface.
 *
 * vyomquant-ui-redesign task 19.2 part B. design.md §8.4, §5.1, §8.2.
 * Requirements 7.6, 8.5, 14.5, 18.3, 19.1.
 *
 * WHY THIS IS A FOURTH DASHBOARD FILE
 * ==================================
 * `dashboard-tier1` and `dashboard-tier2` both say the kill switch and its modal are task
 * 19.2's and are not asserted there; `dashboard-alert-strip` is part A's strip and pins only
 * that `Resume Trading` REACHES a confirmation. This is part B's own surface.
 *
 * WHAT IS PINNED HERE, AND WHY EACH CLAIM IS THE ONE WORTH PINNING
 * ===============================================================
 *   1. **The confirmation is `ds/ConfirmDialog intent="destructive"`** (§8.4, Requirement
 *      7.6) — not a hand-rolled `position: fixed` div. The intent is asserted on BOTH arms,
 *      because one risk control has one confirmation surface.
 *   2. **The halt is gated behind an acknowledgement, and confirm is unreachable until it is
 *      ticked** (§8.4: "Activate kill switch … **Yes** — halts all trading"). Asserted twice
 *      over: through the `disabled` attribute, and through a raw bubbling click that ignores
 *      it — the second is the only one that tests `ConfirmDialog`'s in-handler guard, which
 *      is what makes the gate a rule rather than a rendering (its safety decision 2).
 *   3. **`riskApi.killSwitch` is called with the UNCHANGED `{ reason }` argument, once, and
 *      only after confirming** (Requirement 19.1). The `reason` template is
 *      `handleConfirmKillSwitchAction`'s and is frozen byte for byte, so it is pinned as the
 *      exact string it sends — including the ledger word, which follows the page control.
 *      Nothing is issued when the dialog merely OPENS.
 *   4. **Live and paper are unmistakably different confirmations** (Requirement 8.5, §8.2).
 *      The old modal drew that distinction with ONE `#10b981`/`#818cf8` ternary on the words
 *      `LIVE TRADING (REAL CAPITAL)` / `PAPER SIMULATION`. Both strings are now gone, and the
 *      distinction is pinned on all four of §8.2's axes plus two prose surfaces — the
 *      description and the review grid's long form — so it survives for a trader who cannot
 *      tell the two hues apart.
 *   5. **The recovery is NOT gated** (§8.4). See that test's comment: the inventory spends an
 *      acknowledgement on irreversible and real-funds actions only, and the object is not
 *      constructed at all rather than constructed and hidden.
 *
 * No risk-control logic is exercised or changed by any of this. The handler, its two
 * `riskApi` calls, the `"activate"｜"recover"` machine, both `risk.kill_switch_*`
 * subscriptions and the two `riskState` derivations are byte-identical to what stood before
 * part B; what changed is the surface, and the surface is what this file reads.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import Dashboard from '../../src/pages/Dashboard';
import * as dashboardModule from '../../src/api/modules/dashboard';
import * as riskModule from '../../src/api/modules/risk';
import { ENVIRONMENT } from '../../src/design/semantic';
import { PAGES, PAGE_FIELD_BY_KEY, pageFieldKey } from '../../src/design/pageFields';
import { tierSelector } from '../../src/design/pageHierarchy';

/* `ds/Chart` is stubbed, not recharts — `dashboard-tier1.test.jsx`'s reasoning verbatim. */
vi.mock('../../src/components/ds/Chart', () => {
  const Stub = () => <figure data-testid="chart" />;
  return { __esModule: true, Chart: Stub, default: Stub };
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE DECLARATION, THE COPY, AND THE FIXTURE
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const declared = (field) => PAGE_FIELD_BY_KEY[pageFieldKey({ page: PAGES.DASHBOARD, field })];

/**
 * The dialog's copy, per arm and per ledger.
 *
 * Retyped from `pages/Dashboard.jsx`'s frozen constants rather than imported, because they
 * are module-private there. That is deliberate: this file is the second reader of those
 * sentences, so a silent edit to either one fails here rather than shipping.
 */
const HALT_TITLE = 'Activate Emergency Kill Switch';
const RECOVER_TITLE = 'Deactivate Emergency Kill Switch';
const HALT_CONFIRM_LABEL = 'Yes, HALT TRADING IMMEDIATELY';
const RECOVER_CONFIRM_LABEL = 'Resume Operations';

const HALT_DESCRIPTION = Object.freeze({
  LIVE: 'WARNING: Activating the Emergency Kill Switch will IMMEDIATELY halt all live '
    + 'strategy execution loops and block all new order submissions on connected live '
    + 'exchanges.',
  PAPER: 'Activating the Emergency Kill Switch will freeze all paper simulation bots and '
    + 'prevent new simulated trades.',
});

const RECOVER_DESCRIPTION =
  'Deactivating the Emergency Kill Switch will resume normal algorithmic execution and '
  + 'order submissions.';

const ACKNOWLEDGEMENT_LABEL = 'I understand this halts all trading on this account';

const ACKNOWLEDGEMENT_STATEMENT = Object.freeze({
  LIVE: 'Confirming halts every live strategy execution loop on this account and blocks all '
    + 'new order submissions on connected live exchanges.',
  PAPER: 'Confirming freezes every paper simulation bot on this account and blocks all new '
    + 'simulated order submissions.',
});

/** The `reason` the frozen handler sends, per ledger. The template is not re-derived here. */
const REASON = (ledger) => `Emergency manual halt triggered from Dashboard (${ledger})`;

/** The two strings the old modal drew the ledger distinction with, and which must be gone. */
const RETIRED_LIVE_COPY = 'LIVE TRADING (REAL CAPITAL)';
const RETIRED_PAPER_COPY = 'PAPER SIMULATION';

/**
 * A `GET /api/dashboard` body.
 *
 * `strategies.active` (2) and `risk.open_positions_count` (3) are the review grid's two
 * figures and disagree with the list lengths on purpose: the grid reads the SAME reported
 * fields the panels below read, and never `positions.length`.
 */
const body = ({ environment = 'live', risk = {} } = {}) => ({
  environment,
  overview: { total_value: 45250, today_pnl: 400, cumulative_pnl: 5250, currency: 'USDT' },
  positions: [],
  degraded: null,
  executions: [],
  risk: {
    current_drawdown_pct_v2: 3.2,
    open_positions_count: 3,
    circuit_breaker_armed: true,
    kill_switch_active: false,
    ...risk,
  },
  health: { exchange_api_latency_ms: null, order_state_sync_status: 'synchronized' },
  exchange: { total_exchanges: 0, connected_exchanges: 0, can_trade: true, exchanges: [] },
  strategies: { total: 4, active: 2, paused: 2, items: [] },
  recent_activity: { signals: [], insights: [] },
  equity_curve: [],
});

/** The read answers for whichever ledger it was asked about. */
const read = () => vi.spyOn(dashboardModule.dashboardApi, 'getDashboard')
  .mockImplementation((params) => Promise.resolve(body({ environment: params?.environment })));

const halt = () => vi.spyOn(riskModule.riskApi, 'killSwitch')
  .mockResolvedValue({ status: 'halted', kill_switch_active: true, message: 'Halted.' });

const recover = () => vi.spyOn(riskModule.riskApi, 'recoverKillSwitch')
  .mockResolvedValue({ status: 'active', kill_switch_active: false, message: 'Recovered.' });

const mount = () => render(<MemoryRouter><Dashboard /></MemoryRouter>);

/* ══════════════════════════════════════════════════════════════════════════════════════
 * DOM HELPERS
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const tierOneContainer = () => document.querySelector(tierSelector(PAGES.DASHBOARD, 1));

/** Wait until the ONE read has answered — `dashboard-alert-strip.test.jsx`'s helper. */
const settled = async () => {
  await waitFor(() => expect(tierOneContainer()).not.toBeNull());
};

/** The dialog, or `null`. It is portalled to `document.body`, so this is not a container query. */
const dialog = () => document.querySelector('[data-ds="confirm-dialog"]');
const ds = (name) => document.querySelector(`[data-ds="${name}"]`);
const confirmAction = () => ds('confirm-dialog-confirm');

/** Select the ledger through the page control, and wait for the read it re-issues. */
const selectLedger = async (label, spy) => {
  const before = spy.mock.calls.length;
  fireEvent.click(screen.getByRole('radio', { name: label }));
  await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(before));
};

/** Open the confirmation from whichever arm the trigger is currently offering. */
const openConfirmation = async (name) => {
  fireEvent.click(screen.getByRole('button', { name }));
  await waitFor(() => expect(dialog()).not.toBeNull());
  return dialog();
};

/** §8.2's four axes, off the dialog header's `ds/TradingEnvironmentBadge`. */
const ledgerAxes = () => {
  const strip = ds('environment-strip');
  expect(strip, 'the dialog rendered no environment badge').not.toBeNull();
  return {
    id: strip.getAttribute('data-environment'),
    border: strip.getAttribute('data-environment-border'),
    icon: strip.getAttribute('data-environment-icon'),
    // The badge's own label element: the span directly after the screen-reader prefix,
    // which is why the primitive gives the label a span of its own. NOT `span:last-of-type`
    // — the `strip` variant appends the long form as a further span, so that selector reads
    // the `long` axis a second time and leaves the `label` axis unasserted.
    label: strip.querySelector('span.sr-only + span').textContent,
    long: strip.getAttribute('title'),
  };
};

/** The review grid as `{label: renderedText}`, with the not-available marker as `null`. */
const reviewGrid = () => {
  const grid = ds('confirm-dialog-review');
  expect(grid, 'the dialog rendered no review grid').not.toBeNull();
  const rows = {};
  for (const row of grid.children) {
    const value = row.querySelector('dd');
    rows[row.querySelector('dt').textContent] =
      value.getAttribute('data-ds') === 'not-available' ? null : value.textContent;
  }
  return rows;
};

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE HALT — `intent="destructive"`, AND NOTHING ISSUED ON OPEN
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard kill switch — the halt confirmation (task 19.2b, §8.4)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('routes the halt through `ConfirmDialog intent="destructive"` and issues nothing on open', async () => {
    read();
    const killSwitch = halt();

    mount();
    await settled();

    await openConfirmation('EMERGENCY HALT');

    // Requirement 7.6 / §8.4: the destructive inventory's own surface, with the modal chrome
    // the hand-rolled div never had.
    expect(dialog().getAttribute('data-ds-intent')).toBe('destructive');
    expect(dialog().getAttribute('role')).toBe('dialog');
    expect(dialog().getAttribute('aria-modal')).toBe('true');
    expect(within(dialog()).getByRole('heading', { level: 2 }).textContent).toBe(HALT_TITLE);
    expect(confirmAction().textContent).toBe(HALT_CONFIRM_LABEL);

    // §8.4's review grid: the ledger's long form and the two figures the switch is about to
    // act on, read from the reported fields rather than from `positions.length`.
    expect(reviewGrid()).toEqual({
      'Execution environment': ENVIRONMENT.LIVE.long,
      [declared('activeStrategyCount').label]: '2',
      [declared('openPositionsCount').label]: '3',
    });

    // Opening a confirmation is not confirming one. Requirement 19.1's boundary: this dialog
    // issues no request, and the page's only caller is the frozen handler.
    expect(killSwitch).not.toHaveBeenCalled();
  });

  it('renders the not-available marker, never a `0`, for a figure the server did not report', async () => {
    // Requirement 14.5 on the confirmation itself: `open_positions_count` is `null` — never
    // `0` — when BC-2 could not count the positions, and a `0` here would tell a trader the
    // halt has nothing open to act on.
    vi.spyOn(dashboardModule.dashboardApi, 'getDashboard')
      .mockResolvedValue(body({ risk: { open_positions_count: null } }));
    halt();

    mount();
    await settled();

    await openConfirmation('EMERGENCY HALT');

    expect(reviewGrid()[declared('openPositionsCount').label]).toBeNull();
    expect(ds('confirm-dialog-review').textContent).not.toContain('0');
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE ACKNOWLEDGEMENT — §8.4's "Yes — halts all trading"
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard kill switch — the halt is gated (§8.4, Requirement 7.6)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('cannot reach `riskApi.killSwitch` until the acknowledgement is ticked', async () => {
    read();
    const killSwitch = halt();

    mount();
    await settled();

    await openConfirmation('EMERGENCY HALT');

    // The gate exists, and it is a control with an accessible name rather than a sentence.
    const box = within(dialog()).getByRole('checkbox', { name: ACKNOWLEDGEMENT_LABEL });
    expect(box.checked).toBe(false);
    expect(confirmAction().disabled).toBe(true);

    // 1. The ordinary path. A trader pressing confirm before ticking gets nothing.
    fireEvent.click(confirmAction());
    expect(killSwitch).not.toHaveBeenCalled();

    /*
     * 2. The path `disabled` does not cover. A raw bubbling click reaches React's handler
     * whatever the attribute says, which is the only way to exercise `ConfirmDialog`'s
     * in-handler guard — the difference between the gate being a rendering and being a rule
     * (its safety decision 2). A stray effect, a `.click()` from a helper, or a refactor that
     * styles the button instead of disabling it all arrive here.
     */
    confirmAction().dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    expect(killSwitch).not.toHaveBeenCalled();

    // 3. Ticked, and only now is confirm reachable.
    fireEvent.click(box);
    expect(box.checked).toBe(true);
    expect(confirmAction().disabled).toBe(false);

    fireEvent.click(confirmAction());
    await waitFor(() => expect(killSwitch).toHaveBeenCalledTimes(1));

    // The frozen argument, exactly as `handleConfirmKillSwitchAction` builds it: ONE object
    // with ONE key. A second argument, or a changed sentence, changes what is written to the
    // audit trail (Requirement 19.1).
    expect(killSwitch.mock.calls[0]).toEqual([{ reason: REASON('LIVE') }]);

    // The frozen success path's two writes, read off the surface: `res.kill_switch_active`
    // was true, so the dialog closed and the trigger now offers the recovery.
    await waitFor(() => expect(dialog()).toBeNull());
    expect(screen.getByRole('button', { name: 'RESUME TRADING' })).toBeDefined();
    expect(screen.queryByRole('button', { name: 'EMERGENCY HALT' })).toBeNull();
  });

  it('re-arms the gate on every open, so one tick cannot authorise a second halt', async () => {
    read();
    const killSwitch = halt();

    mount();
    await settled();

    await openConfirmation('EMERGENCY HALT');
    fireEvent.click(within(dialog()).getByRole('checkbox', { name: ACKNOWLEDGEMENT_LABEL }));
    expect(confirmAction().disabled).toBe(false);

    // Cancel rather than confirm. `ds/ConfirmDialog` allows it because nothing is in flight.
    fireEvent.click(ds('confirm-dialog-cancel'));
    await waitFor(() => expect(dialog()).toBeNull());
    expect(killSwitch).not.toHaveBeenCalled();

    await openConfirmation('EMERGENCY HALT');
    // A dialog that remembered the tick is a dialog that can halt trading without an
    // acknowledgement in this session.
    expect(within(dialog()).getByRole('checkbox', { name: ACKNOWLEDGEMENT_LABEL }).checked)
      .toBe(false);
    expect(confirmAction().disabled).toBe(true);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * LIVE VERSUS PAPER — Requirement 8.5, §8.2's four axes AND two prose surfaces
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard kill switch — the two ledgers confirm differently (Requirement 8.5)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('differs on all four badge axes, in the description and in the review grid', async () => {
    const spy = read();
    halt();

    mount();
    await settled();

    // ── LIVE ──────────────────────────────────────────────────────────────────────
    await openConfirmation('EMERGENCY HALT');
    const live = ledgerAxes();
    expect(live).toEqual({
      id: 'LIVE',
      border: 'solid',
      icon: 'Radio',
      label: ENVIRONMENT.LIVE.label,
      long: ENVIRONMENT.LIVE.long,
    });
    expect(within(dialog()).getByText(HALT_DESCRIPTION.LIVE)).toBeDefined();
    expect(within(dialog()).getByText(ACKNOWLEDGEMENT_STATEMENT.LIVE)).toBeDefined();
    expect(reviewGrid()['Execution environment']).toBe(ENVIRONMENT.LIVE.long);

    fireEvent.click(ds('confirm-dialog-cancel'));
    await waitFor(() => expect(dialog()).toBeNull());

    // ── PAPER ─────────────────────────────────────────────────────────────────────
    await selectLedger('Paper', spy);
    await openConfirmation('EMERGENCY HALT');
    const paper = ledgerAxes();
    expect(paper).toEqual({
      id: 'PAPER',
      border: 'dashed',
      icon: 'FlaskConical',
      label: ENVIRONMENT.PAPER.label,
      long: ENVIRONMENT.PAPER.long,
    });
    expect(within(dialog()).getByText(HALT_DESCRIPTION.PAPER)).toBeDefined();
    expect(within(dialog()).getByText(ACKNOWLEDGEMENT_STATEMENT.PAPER)).toBeDefined();
    expect(reviewGrid()['Execution environment']).toBe(ENVIRONMENT.PAPER.long);

    // Every axis differs, and the two prose surfaces differ with them. The old modal had ONE
    // differing axis — a hex on one line of eyebrow text — so a trader who cannot separate
    // `#10b981` from `#818cf8` read the same confirmation for both ledgers.
    for (const axis of ['id', 'border', 'icon', 'label', 'long']) {
      expect(paper[axis], `the ${axis} axis is the same on both ledgers`).not.toBe(live[axis]);
    }
    expect(HALT_DESCRIPTION.PAPER).not.toBe(HALT_DESCRIPTION.LIVE);

    // The paper statement claims nothing about real funds or connected venues: §8.4's kill
    // switch row is not tagged Requirement 8.2, so this is the halts-all-trading gate rather
    // than the real-funds one, and halting a paper ledger halts all trading in that ledger.
    expect(ACKNOWLEDGEMENT_STATEMENT.PAPER).not.toMatch(/real funds|live/i);

    // And neither of the two strings the retired ternary painted is anywhere on the page.
    expect(document.body.textContent).not.toContain(RETIRED_LIVE_COPY);
    expect(document.body.textContent).not.toContain(RETIRED_PAPER_COPY);
  });

  it('sends the ledger the page control selected in the frozen `reason`', async () => {
    const spy = read();
    const killSwitch = halt();

    mount();
    await settled();

    await selectLedger('Paper', spy);
    await openConfirmation('EMERGENCY HALT');
    fireEvent.click(within(dialog()).getByRole('checkbox', { name: ACKNOWLEDGEMENT_LABEL }));
    fireEvent.click(confirmAction());

    await waitFor(() => expect(killSwitch).toHaveBeenCalledTimes(1));
    // `environment.toUpperCase()`, unchanged — the audit trail records which ledger was
    // halted, and it is the page control's ledger rather than the payload's `environment`.
    expect(killSwitch.mock.calls[0]).toEqual([{ reason: REASON('PAPER') }]);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE RECOVERY — UNGATED, ON PURPOSE (§8.4)
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard kill switch — the recovery is not gated (§8.4)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  /*
   * WHY NO ACKNOWLEDGEMENT HERE, PER §8.4
   * -------------------------------------
   * §8.4's inventory gives one to "Activate kill switch" ("**Yes** — halts all trading") and
   * spends one elsewhere on exactly two kinds of action: irreversible ones ("Cancel all
   * orders … it is bulk and irreversible") and real-funds ones (Requirement 8.2's deploy).
   * Recovery is neither: it places no order, moves no funds, and is reversible by the halt
   * itself — the same reversibility test §8.4 uses to refuse one to "Delete / archive
   * strategy" ("No — reversible via archive"). Gating it would also put a checkbox between a
   * trader and the restoration of trading, which trains the box to be ticked without being
   * read, and that is the failure mode the halt's gate exists to avoid.
   *
   * So the asymmetry below is the design, not an omission — and the object is NOT
   * constructed off the halt arm, which is §8.4's own reading of "SHALL NOT display": there
   * is nothing rendered, and nothing hidden either.
   */
  it('offers no acknowledgement, and confirming calls `recoverKillSwitch` with no argument', async () => {
    vi.spyOn(dashboardModule.dashboardApi, 'getDashboard')
      .mockResolvedValue(body({ risk: { kill_switch_active: true, risk_level: 'blocked' } }));
    const killSwitch = halt();
    const recoverKillSwitch = recover();

    mount();
    await settled();

    // The halted state flips the trigger's arm — the control reporting its own state.
    await openConfirmation('RESUME TRADING');

    // One risk control, one confirmation surface: the intent stays `destructive` rather than
    // painting the resumption of automated order submission as routine.
    expect(dialog().getAttribute('data-ds-intent')).toBe('destructive');
    expect(within(dialog()).getByRole('heading', { level: 2 }).textContent).toBe(RECOVER_TITLE);
    expect(within(dialog()).getByText(RECOVER_DESCRIPTION)).toBeDefined();
    expect(confirmAction().textContent).toBe(RECOVER_CONFIRM_LABEL);

    // Not constructed: no acknowledgement region, no checkbox, and none of the halt's copy.
    expect(ds('confirm-dialog-acknowledgement')).toBeNull();
    expect(within(dialog()).queryAllByRole('checkbox')).toHaveLength(0);
    expect(dialog().textContent).not.toContain(ACKNOWLEDGEMENT_LABEL);

    // So confirm is live on the first render, with nothing to satisfy first.
    expect(confirmAction().disabled).toBe(false);
    fireEvent.click(confirmAction());

    await waitFor(() => expect(recoverKillSwitch).toHaveBeenCalledTimes(1));
    // The frozen call takes no argument, and the halt's endpoint is not touched by this arm.
    expect(recoverKillSwitch.mock.calls[0]).toEqual([]);
    expect(killSwitch).not.toHaveBeenCalled();

    // `res.kill_switch_active` was false, so the dialog closed and the halt is on offer again.
    await waitFor(() => expect(dialog()).toBeNull());
    expect(screen.getByRole('button', { name: 'EMERGENCY HALT' })).toBeDefined();
  });
});
