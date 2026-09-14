/**
 * @fileoverview Dashboard — the Requirement 3.3 alert strip, and its three outcomes.
 *
 * vyomquant-ui-redesign task 19.2 part A. design.md §7.1. Requirements 3.3, 14.5, 16.2, 19.1.
 *
 * WHY THIS IS A THIRD DASHBOARD FILE
 * =================================
 * `dashboard-tier1.test.jsx` is named for tier 1 and carries an empty account;
 * `dashboard-tier2.test.jsx` is named for tier 2 and both files' headers say the Requirement
 * 3.3 strip is task 19.2's and is not asserted there. This is that assertion.
 *
 * WHAT IS PINNED HERE
 * ===================
 *   1. **Placement.** The strip renders ABOVE tier 1 and OUTSIDE both tier containers, which
 *      is why `design/pageHierarchy.js` registers `alertCondition` as UNTIERED — any tier
 *      number would make Property 4 false. Decided from the DOM through `data-region`, not
 *      from the JSX.
 *   2. **It names which arm fired.** The summary is the announcement, and each fired arm is
 *      named beneath it with its subjects and the server's own status spellings. An arm that
 *      did not fire is named nowhere.
 *   3. **The unevaluable case renders the DECLARED reason.** `pageFields`'
 *      `dashboard/alertCondition` has `absence: UNMEASURABLE`; the reason is read from the
 *      declaration here rather than retyped, so a page that paraphrased it would fail.
 *   4. **States read with none firing renders NOTHING.** Including on the real
 *      constant-`"connected"` venue payload, which is the one case where an all-clear band
 *      would be a claim about a link nothing measured.
 *   5. **Both live risk affordances survive** (Requirement 19.1): `Resume Trading` reaches
 *      the existing kill-switch confirmation, and `Review Risk Settings` is a real link to
 *      `/app/risk`. No kill-switch logic is exercised or changed by either.
 *   6. **`recent_activity.insights` no longer reaches the screen.** It has no `pageFields`
 *      entry and is not one of the three declared inputs.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import Dashboard from '../../src/pages/Dashboard';
import * as dashboardModule from '../../src/api/modules/dashboard';
import { PAGES, PAGE_FIELD_BY_KEY, pageFieldKey } from '../../src/design/pageFields';
import { PAGE_HIERARCHY_BY_PAGE, tierSelector } from '../../src/design/pageHierarchy';

/* `ds/Chart` is stubbed, not recharts — `dashboard-tier2.test.jsx`'s reasoning verbatim. */
vi.mock('../../src/components/ds/Chart', () => {
  const Stub = () => <figure data-testid="chart" />;
  return { __esModule: true, Chart: Stub, default: Stub };
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE DECLARATION, AND THE FIXTURE
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const HIERARCHY = PAGE_HIERARCHY_BY_PAGE[PAGES.DASHBOARD];

/** The `alertCondition` declaration — the reason and the untiered registration. */
const DECLARED = PAGE_FIELD_BY_KEY[pageFieldKey({ page: PAGES.DASHBOARD, field: 'alertCondition' })];

/**
 * A `GET /api/dashboard` body, with the three declared field sets controllable.
 *
 * The venue default is the real one: `status: "connected"`, `latency_ms: 35`, both constants
 * in `dashboard_aggregation_service.get_exchange_health`.
 */
const body = ({
  exchanges = [{ exchange_id: 'binance', status: 'connected', latency_ms: 35, last_sync: '2026-08-26T12:00:00Z' }],
  items = [{ id: 'strat_1', name: 'BTC Trend Follower', status: 'running', health: 'healthy' }],
  executions = [{ id: 'fill_1', symbol: 'SOL/USDT', status: 'filled', price: 145.5, amount: 10 }],
  risk = {},
  insights = [],
} = {}) => ({
  environment: 'live',
  overview: { total_value: 45250, today_pnl: 400, cumulative_pnl: 5250, currency: 'USDT' },
  positions: [],
  degraded: null,
  executions,
  risk: {
    current_drawdown_pct_v2: 3.2,
    open_positions_count: 0,
    circuit_breaker_armed: true,
    kill_switch_active: false,
    ...risk,
  },
  health: {
    exchange_api_latency_ms: 42,
    exchange_api_latency_status: 'optimal',
    order_state_sync_status: 'synchronized',
  },
  exchange: { total_exchanges: 1, connected_exchanges: 1, can_trade: true, exchanges },
  strategies: { total: items.length, active: items.length, paused: 0, items },
  recent_activity: { signals: [], insights },
  equity_curve: [{ timestamp: '2026-08-26T00:00:00Z', equity: 45250 }],
});

const read = () => vi.spyOn(dashboardModule.dashboardApi, 'getDashboard');

const mount = () => render(<MemoryRouter><Dashboard /></MemoryRouter>);

/* ══════════════════════════════════════════════════════════════════════════════════════
 * DOM HELPERS
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const region = (key) => document.querySelector(`[data-region="${key}"]`);
const strip = () => region('alertCondition');
const tierOneContainer = () => document.querySelector(tierSelector(PAGES.DASHBOARD, 1));
const tierTwoContainer = () => document.querySelector(tierSelector(PAGES.DASHBOARD, 2));

const precedes = (first, second) =>
  Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);

/**
 * Wait until the ONE read has answered.
 *
 * The tier-1 GRID, not the tier-1 panel: `ds/Panel` renders no children while loading, so
 * the grid existing is the read having settled. Waiting on the strip itself would be
 * circular — its absence is what half these cases assert.
 */
const settled = async () => {
  await waitFor(() => expect(tierOneContainer()).not.toBeNull());
};

/* ══════════════════════════════════════════════════════════════════════════════════════
 * PLACEMENT — ABOVE TIER 1, OUTSIDE BOTH TIERS
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard alert strip — where it renders (Requirement 3.3)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('is declared UNTIERED, which is why a tier number would make Property 4 false', () => {
    // Asserted from the declaration first: the strip is above tier 1 because §7.1 puts it
    // there, not because this page happens to render it early.
    const untiered = HIERARCHY.untiered.map((entry) => entry.field);
    expect(untiered).toContain('alertCondition');
    expect(HIERARCHY.tiers.some((entry) => entry.key === 'alertCondition')).toBe(false);
  });

  it('renders above tier 1 and outside both tier containers', async () => {
    read().mockResolvedValue(body({
      exchanges: [{ exchange_id: 'binance', status: 'disconnected', latency_ms: 35 }],
    }));

    mount();
    await settled();

    const element = strip();
    expect(element).not.toBeNull();
    expect(precedes(element, tierOneContainer())).toBe(true);
    expect(tierOneContainer().contains(element)).toBe(false);
    expect(tierTwoContainer().contains(element)).toBe(false);
    // Nor inside the panel that wraps tier 1, which is what "outside" has to mean for a
    // strip that must not be read as one of the four figures (Requirement 3.4).
    expect(region('tier-1').contains(element)).toBe(false);
    // Exactly one, so "the strip" is a single decidable element.
    expect(document.querySelectorAll('[data-region="alertCondition"]')).toHaveLength(1);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * OUTCOME 1 — FIRING: THE STRIP NAMES WHICH ARM
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard alert strip — {evaluated: true, firing: true}', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('names the one arm that fired, its subject and the server\'s own status word', async () => {
    read().mockResolvedValue(body({
      items: [
        { id: 'strat_1', name: 'BTC Trend Follower', status: 'crashed' },
        { id: 'strat_2', name: 'Calm One', status: 'running' },
      ],
    }));

    mount();
    await settled();

    const element = strip();
    expect(element).not.toBeNull();
    expect(element.getAttribute('data-alert-condition')).toBe('firing');
    expect(element.textContent).toContain('1 strategy in an error state');
    // The subject, and `crashed` verbatim — the word the subsystem used, not one this
    // client chose. "stopped on error" would be a paraphrase of a state nothing reported.
    expect(element.textContent).toContain('BTC Trend Follower');
    expect(element.textContent).toContain('crashed');

    // Exactly one arm is named, and it is the strategy arm.
    const named = [...element.querySelectorAll('[data-alert-arm]')]
      .map((node) => node.getAttribute('data-alert-arm'));
    expect(named).toEqual(['strategy']);
    // The arms that did not fire are named nowhere — no absence is stated as a finding.
    expect(element.textContent).not.toContain('exchange');
    expect(element.textContent).not.toContain('execution');
  });

  it('names all three arms, in declaration order, when all three fire', async () => {
    read().mockResolvedValue(body({
      exchanges: [{ exchange_id: 'binance', status: 'disconnected', latency_ms: 35 }],
      items: [{ id: 'strat_1', name: 'BTC Trend Follower', status: 'crashed' }],
      executions: [{ id: 'ord_9', symbol: 'ETH/USDT', status: 'rejected' }],
    }));

    mount();
    await settled();

    const element = strip();
    const named = [...element.querySelectorAll('[data-alert-arm]')]
      .map((node) => node.getAttribute('data-alert-arm'));
    expect(named).toEqual(['exchange', 'strategy', 'execution']);

    expect(element.textContent).toContain('1 exchange not connected');
    expect(element.textContent).toContain('1 strategy in an error state');
    expect(element.textContent).toContain('1 execution failed or rejected');
    expect(element.textContent).toContain('binance');
    expect(element.textContent).toContain('ord_9');

    // No `[Review]` action, and that is a departure from §7.1's mock with a reason: the
    // disjunction has three subjects on three different pages, so one button would have to
    // guess, and one link per arm would duplicate the tier-2 panels' three links under the
    // same accessible names. Requirement 3.3 asks the strip to SUMMARISE, which it does.
    expect(element.querySelectorAll('a')).toHaveLength(0);
    expect(element.querySelectorAll('button')).toHaveLength(0);
  });

  it('takes its hue, icon, border and live-region role from `severity`, which is a constant', async () => {
    read().mockResolvedValue(body({
      executions: [{ id: 'ord_9', symbol: 'ETH/USDT', status: 'failed' }],
    }));

    mount();
    await settled();

    const element = strip();
    // `warning`, not `critical`: the derivation does not rank a disconnected venue against
    // a rejected order, so nothing here knows how bad it is — and Requirement 16.2 forbids
    // interrupting a trader for a message whose severity was guessed.
    expect(element.getAttribute('data-alert-severity')).toBe('warning');
    expect(element.getAttribute('role')).toBe('status');
    expect(element.getAttribute('data-status-group')).toBe('warning');
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * OUTCOME 2 — READ, NONE FIRED: SILENCE
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard alert strip — {evaluated: true, firing: false}', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders NOTHING when states were read and none of them fired', async () => {
    read().mockResolvedValue(body());

    mount();
    await settled();

    expect(strip()).toBeNull();
    // And no all-clear anywhere: silence is not an assertion, and a green band would be one.
    expect(document.body.textContent).not.toContain('no alert condition could be evaluated');
    expect(document.body.textContent).not.toContain('All systems');
  });

  it('renders nothing on the real constant-`"connected"` venue payload', async () => {
    // `pageFields`' note: `status` is the literal `"connected"` for every entry, from no
    // measurement. The arm is implemented and simply cannot fire from this read — and its
    // silence must not surface as "all exchanges connected".
    read().mockResolvedValue(body({
      exchanges: [
        { exchange_id: 'binance', status: 'connected', latency_ms: 35 },
        { exchange_id: 'bybit', status: 'connected', latency_ms: 35 },
      ],
    }));

    mount();
    await settled();

    expect(strip()).toBeNull();
    expect(document.body.textContent).not.toContain('exchanges connected');
    expect(document.body.textContent).not.toContain('not connected');
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * OUTCOME 3 — NOTHING READABLE: THE DECLARED REASON
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard alert strip — {evaluated: false, firing: false}', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders the DECLARED reason, and does not read as an all-clear', async () => {
    read().mockResolvedValue(body({ exchanges: [], items: [], executions: [] }));

    mount();
    await settled();

    const element = strip();
    expect(element).not.toBeNull();
    expect(element.getAttribute('data-alert-condition')).toBe('unevaluable');
    // Read from the declaration, not retyped: a paraphrase here would fail.
    expect(DECLARED.reason).toBe(
      'No exchange, strategy or execution state was reported, so no alert condition could '
      + 'be evaluated.',
    );
    expect(element.textContent).toContain(DECLARED.reason);
    expect(element.textContent).toContain('This is not a report that');

    // `info` resolves to the neutral group, so the band carries no hue: "no state was
    // reported" is an absence of information, not a current state, a risk or an action
    // (Requirement 1.5). And it is polite, not assertive (Requirement 16.2).
    expect(element.getAttribute('data-alert-severity')).toBe('info');
    expect(element.getAttribute('data-status-group')).toBe('neutral');
    expect(element.getAttribute('role')).toBe('status');

    // No arm is named, because no arm found anything.
    expect(element.querySelectorAll('[data-alert-arm]')).toHaveLength(0);
  });

  it('says nothing at all while the read is still in flight', async () => {
    // Without the gate this band would announce that no state was reported BEFORE the
    // account was read, which is a claim about the account made before reading it.
    let release;
    read().mockImplementation(() => new Promise((resolve) => { release = resolve; }));

    mount();

    await waitFor(() => expect(region('tier-1')).not.toBeNull());
    expect(region('tier-1').getAttribute('data-panel-state')).toBe('loading');
    expect(strip()).toBeNull();
    expect(document.body.textContent).not.toContain('no alert condition could be evaluated');

    release(body({ exchanges: [], items: [], executions: [] }));
    await settled();
    // And once the read answers, the reason is on screen.
    expect(strip()).not.toBeNull();
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE TWO RISK BANDS — Requirement 19.1's live controls, still reachable
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard alert strip — the two `riskState` bands (Requirement 19.1)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('reports an active kill switch and keeps `Resume Trading` reaching the confirmation', async () => {
    read().mockResolvedValue(body({ risk: { kill_switch_active: true } }));

    mount();
    await settled();

    const band = region('kill-switch-active');
    expect(band).not.toBeNull();
    expect(band.textContent).toContain('Emergency kill switch active');
    // A halt IS assertive: trading has stopped, and this is the one message on the page that
    // earns interrupting whatever a screen reader is saying (Requirement 16.2).
    expect(band.getAttribute('data-alert-severity')).toBe('critical');
    expect(band.getAttribute('role')).toBe('alert');
    // Above tier 1, like the derived strip.
    expect(precedes(band, tierOneContainer())).toBe(true);

    // The affordance is live: it opens the EXISTING confirmation, which is the only place
    // the recovery is authorised. Nothing about the switch's logic is exercised here.
    fireEvent.click(within(band).getByRole('button', { name: 'Resume Trading' }));
    expect(screen.getByText('Deactivate Emergency Kill Switch')).toBeDefined();
  });

  it('reports a tripped circuit breaker with a real link to /app/risk and no invented cause', async () => {
    read().mockResolvedValue(body({ risk: { circuit_breaker_armed: false } }));

    mount();
    await settled();

    const band = region('circuit-breaker');
    expect(band).not.toBeNull();
    expect(band.textContent).toContain('Risk circuit breaker triggered');
    expect(band.getAttribute('data-alert-severity')).toBe('warning');

    // A link, not a `button onClick={navigate}`: it is a navigation, so it belongs in the
    // tab order as one. This is also the page's only route to `/app/risk`, which task
    // 19.1b's removal of the Risk & Safety Matrix relies on.
    const link = within(band).getByRole('link', { name: /Review Risk Settings/ });
    expect(link.getAttribute('href')).toBe('/app/risk');

    // `circuit_breaker_armed` is one boolean. The old copy named a cause — "Daily loss
    // threshold or max drawdown reached" — that the response does not carry.
    expect(band.textContent).not.toContain('Daily loss threshold');
    expect(band.textContent).toContain('did not report which limit');
  });

  it('renders neither band, and no strip, when both readings are healthy', async () => {
    read().mockResolvedValue(body());

    mount();
    await settled();

    expect(region('kill-switch-active')).toBeNull();
    expect(region('circuit-breaker')).toBeNull();
    expect(strip()).toBeNull();
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE FOURTH INPUT THAT IS NOT ONE
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard alert strip — `recent_activity.insights` is not an input', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('does not render insights, and does not let them fabricate a condition', async () => {
    // No `pageFields` entry, no §7.1 row, and two of the three items the aggregation service
    // publishes are prose it hardcodes. Prose in a live region is a finding nothing measured.
    read().mockResolvedValue(body({
      insights: [
        { id: 'ins_1', type: 'warning', text: 'Bybit API rate limit usage reached 78% of capacity', actionPath: '/app/exchange', actionText: 'Check Rate Limits' },
        { id: 'ins_2', type: 'error', text: 'Risk Circuit Breakers active', actionPath: '/app/risk' },
      ],
    }));

    mount();
    await settled();

    expect(document.body.textContent).not.toContain('Bybit API rate limit usage');
    expect(document.body.textContent).not.toContain('Check Rate Limits');
    // The three declared field sets all read clean, so the page says nothing — the insights
    // cannot make a condition exist.
    expect(strip()).toBeNull();
  });
});
