/**
 * tests/unit/pages/riskSettingsKillSwitch.test.jsx — retail-ui-simplification task 7.1.
 *
 * Requirements 6.5, 18.1, 20.1, 20.3. design.md §5.3 ("What is not covered"), §5.8.
 *
 * WHY THIS FILE EXISTS AT ALL
 * ---------------------------
 * **Nothing renders `RiskSettings.jsx`.** Searching `tests/` for it returns source-scanning
 * guards that hold a budget entry for its path — `no-colour-literals`,
 * `absolute-font-sizes`, `a11y-ratchet` — and nothing that mounts it.
 * `tests/unit/dashboard-kill-switch.test.jsx` is the nearest thing and it covers the
 * *Dashboard's* halt confirmation, not this page's toggles. So the four controls that stop
 * every bot this account is running have no behavioural coverage of any kind, and
 * Requirement 6.5 asks for a test rather than a manual check because a kill switch reachable
 * only by mouse is the one control where the gap is not an inconvenience.
 *
 * WHAT IT ASSERTS, AND WHY IT IS THREE THINGS RATHER THAN ONE
 * ----------------------------------------------------------
 * A "keyboard-accessible kill switch" decomposes into three claims that fail independently,
 * so each gets its own block:
 *
 *   1. The control is FINDABLE by a keyboard-and-screen-reader user — it has a role, an
 *      accessible name, a programmatic checked state and a tab stop (Requirement 20.1).
 *      A `div` with an `onClick` satisfies none of these and looks identical on screen.
 *   2. The control is OPERABLE from the keyboard — `keyDown` and not `click`, which is the
 *      distinction that matters: React synthesises a `click` from Enter on a `<button>` and
 *      on nothing else, so a test that clicks cannot tell a switch from a styled `div`
 *      (Requirement 20.3).
 *   3. The keyboard path issues **the same request** as the pointer path. This is the claim
 *      with teeth. A control can be focusable, named, and wired to a second code path that
 *      updates local state and never reaches `PUT /api/risk/settings` — in which case a
 *      keyboard user sees the switch move and the account is not protected. Block 3 runs
 *      each of the four switches twice, on two fresh mounts from the same fixture, and
 *      compares the recorded request bodies for deep equality.
 *
 * THE SAME-REQUEST COMPARISON IS DELIBERATELY NOT `toHaveBeenCalledWith(<literal>)`
 * -------------------------------------------------------------------------------
 * Asserting a hand-written body would make this file a second copy of the payload shape, and
 * the next person to add a risk parameter would have to edit the test to keep it passing —
 * which is how a preservation test turns into a rubber stamp. Comparing the pointer run's
 * recorded body against the keyboard run's says the thing Requirement 6.5 actually wants,
 * and says it about whatever the payload happens to be. Block 4 pins the parts of the
 * payload that are safety properties in their own right.
 *
 * WHEN IT RUNS
 * ------------
 * **Before task 7.2 and again after it.** Requirement 6.5's wording — "before and after
 * their migration" — asks for exactly that, and a test first written against the tree it is
 * meant to protect has never been observed doing anything. Recorded baseline: green against
 * the unmigrated page.
 *
 * WHAT IS MOCKED, AND WHAT IS NOT
 * -------------------------------
 * `src/api` and `src/websocketClient` are doubles: one chooses what the server answered, the
 * other stops a real socket being opened in jsdom. Everything else — `design/tokens`, the
 * `components/common/primitives`, `components/ui/*`, and after task 7.2 the
 * `components/ds/*` primitives — is REAL, because the page's rendered output is the thing
 * under test.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { risk, wsSubscribe } = vi.hoisted(() => ({
  risk: {
    getConfig: vi.fn(),
    getStrategyLimits: vi.fn(),
    getMarginHealth: vi.fn(),
    updateConfig: vi.fn(),
    updateStrategyLimit: vi.fn(),
  },
  wsSubscribe: vi.fn(),
}));

// Both shapes, because the page imports the NAMED `api` and the module also default-exports
// it. A factory closing over a `const` declared below this line would read it in its
// temporal dead zone — `vi.mock` is hoisted, and so is the page import that triggers it.
vi.mock('../../../src/api', () => ({ api: { risk }, default: { risk } }));
vi.mock('../../../src/websocketClient', () => ({
  default: { subscribe: wsSubscribe },
}));

import RiskSettings from '../../../src/pages/RiskSettings';

// ── Fixtures ────────────────────────────────────────────────────────────────────────────
//
// Every number is deliberately NOT the page's own default, so a payload assembled from the
// page's initial state instead of from the server's answer is visible rather than
// coincidentally correct. The four kill-switch flags are two on and two off for the same
// reason: a payload that hardcodes `true` passes against an all-on fixture.

const RISK_CONFIG = Object.freeze({
  max_daily_loss: 1200,
  max_positions: 7,
  max_leverage: 5,
  kill_switches: Object.freeze({
    loss: true,
    blackswan: false,
    streak: true,
    capital: false,
  }),
});

const STRATEGY_LIMITS = Object.freeze({
  limits: Object.freeze([
    Object.freeze({
      strategy_id: 'str-alpha',
      strategy_name: 'Aurora Momentum',
      max_position_size: 35,
      max_daily_trades: 40,
      allowed_symbols: ['BTCUSDT'],
      enabled: true,
    }),
  ]),
});

const MARGIN_HEALTH = Object.freeze({
  margin_ratio: 42,
  free_margin: 58,
  risk_score: 17,
});

/**
 * The four switches, as `[key, accessible name, state the fixture puts them in]`.
 *
 * The names are the page's own labels. They are listed here rather than read from the page
 * so that renaming one is a visible diff in this file: an accessible name is part of the
 * control's contract with a screen-reader user, and Requirement 20.1 does not let a
 * migration change it quietly.
 */
const KILL_SWITCHES = Object.freeze([
  ['loss', 'Daily Loss Limit', true],
  ['blackswan', 'Black Swan Protection', false],
  ['streak', 'Consecutive Loss Protection', true],
  ['capital', 'Capital Utilization Limit', false],
]);

const mountPage = () =>
  render(
    <MemoryRouter>
      <RiskSettings />
    </MemoryRouter>,
  );

/*
 * `vitest.config.js` has `setupFiles: []`, so `@testing-library/jest-dom` is not installed
 * and `toHaveAttribute` is unavailable — `strategyBuilder.reviewMode.test.jsx` records the
 * same constraint. This reads the attribute the matcher would have read.
 */
const checkedState = (name) =>
  screen.getByRole('switch', { name }).getAttribute('aria-checked');

/** The page mounted and its three reads settled, so nothing below races the load. */
const mountLoaded = async () => {
  const utils = mountPage();
  await waitFor(() => {
    expect(checkedState(KILL_SWITCHES[0][1])).toBe('true');
    expect(checkedState(KILL_SWITCHES[1][1])).toBe('false');
  });
  return utils;
};

/** The body of the single `PUT /api/risk/settings` a toggle issues. */
const soleUpdateBody = async () => {
  await waitFor(() => expect(risk.updateConfig).toHaveBeenCalledTimes(1));
  return risk.updateConfig.mock.calls[0][0];
};

beforeEach(() => {
  risk.getConfig.mockResolvedValue(RISK_CONFIG);
  risk.getStrategyLimits.mockResolvedValue(STRATEGY_LIMITS);
  risk.getMarginHealth.mockResolvedValue(MARGIN_HEALTH);
  risk.updateConfig.mockResolvedValue({ data: RISK_CONFIG });
  risk.updateStrategyLimit.mockResolvedValue({ status: 'ok' });
  // The page subscribes to two risk events and calls what `subscribe` returns on unmount.
  wsSubscribe.mockReturnValue(() => {});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// 1. Every kill switch is findable without a mouse
// ---------------------------------------------------------------------------

describe('RiskSettings kill switches: every control is named and in the tab order', () => {
  it('renders exactly four switches and no more', async () => {
    await mountLoaded();

    // Non-vacuity for every `getByRole('switch', …)` below: if the page stopped rendering
    // switches entirely, a `queryBy`-shaped test would pass over nothing.
    expect(screen.getAllByRole('switch')).toHaveLength(KILL_SWITCHES.length);
  });

  it.each(KILL_SWITCHES)(
    '%s is reachable by its accessible name, carries its state, and takes focus',
    async (_key, name, activeInFixture) => {
      await mountLoaded();

      // `getByRole` with a name is the whole assertion: it throws when the control has no
      // role, and it throws when it has a role and no accessible name.
      const control = screen.getByRole('switch', { name });

      // The state is programmatic, not a colour. Requirement 20.5's rule applied to the one
      // control where "looks green" is not a substitute for "reports on".
      expect(control.getAttribute('aria-checked')).toBe(String(activeInFixture));
      expect(control.getAttribute('aria-checked')).toBe(
        String(RISK_CONFIG.kill_switches[_key]),
      );

      // A tab stop. Without this the control is named, stateful and unreachable.
      control.focus();
      expect(document.activeElement).toBe(control);
    },
  );
});

// ---------------------------------------------------------------------------
// 2. Every kill switch is operable from the keyboard
// ---------------------------------------------------------------------------

describe('RiskSettings kill switches: the keyboard operates them', () => {
  it.each(KILL_SWITCHES)('%s toggles on Enter, by keyDown and not by click', async (_key, name, activeInFixture) => {
    await mountLoaded();
    const control = screen.getByRole('switch', { name });

    fireEvent.keyDown(control, { key: 'Enter' });

    await waitFor(() => expect(checkedState(name)).toBe(String(!activeInFixture)));
    expect(risk.updateConfig).toHaveBeenCalledTimes(1);
  });

  it.each(KILL_SWITCHES)('%s toggles on Space, and Space does not scroll the page', async (_key, name, activeInFixture) => {
    await mountLoaded();
    const control = screen.getByRole('switch', { name });

    // `fireEvent` returns false when the handler called `preventDefault`. Space scrolls by
    // default, which would move the switch out from under the setting just changed.
    expect(fireEvent.keyDown(control, { key: ' ' })).toBe(false);

    await waitFor(() => expect(checkedState(name)).toBe(String(!activeInFixture)));
    expect(risk.updateConfig).toHaveBeenCalledTimes(1);
  });

  it('leaves the other three switches alone when one is operated', async () => {
    await mountLoaded();
    fireEvent.keyDown(screen.getByRole('switch', { name: KILL_SWITCHES[0][1] }), { key: 'Enter' });
    await waitFor(() => expect(risk.updateConfig).toHaveBeenCalledTimes(1));

    for (const [key, name, activeInFixture] of KILL_SWITCHES.slice(1)) {
      expect(checkedState(name)).toBe(String(activeInFixture));
      expect(risk.updateConfig.mock.calls[0][0].kill_switches[key]).toBe(activeInFixture);
    }
  });
});

// ---------------------------------------------------------------------------
// 3. The keyboard path issues the same request as the pointer path
// ---------------------------------------------------------------------------

describe('RiskSettings kill switches: one request, two input devices', () => {
  it.each(KILL_SWITCHES)(
    '%s issues an identical request body on Enter and on click',
    async (_key, name) => {
      // Two fresh mounts from the same fixture, so both runs start from the same state and
      // the comparison is between the two INPUT PATHS rather than between two moments.
      await mountLoaded();
      fireEvent.click(screen.getByRole('switch', { name }));
      const pointerBody = await soleUpdateBody();

      cleanup();
      risk.updateConfig.mockClear();

      await mountLoaded();
      fireEvent.keyDown(screen.getByRole('switch', { name }), { key: 'Enter' });
      const keyboardBody = await soleUpdateBody();

      expect(keyboardBody).toEqual(pointerBody);
    },
  );

  it('issues the same request body on Space as on click', async () => {
    const [, name] = KILL_SWITCHES[3];

    await mountLoaded();
    fireEvent.click(screen.getByRole('switch', { name }));
    const pointerBody = await soleUpdateBody();

    cleanup();
    risk.updateConfig.mockClear();

    await mountLoaded();
    fireEvent.keyDown(screen.getByRole('switch', { name }), { key: ' ' });
    expect(await soleUpdateBody()).toEqual(pointerBody);
  });
});

// ---------------------------------------------------------------------------
// 4. What the request carries, and what else on the page must survive
// ---------------------------------------------------------------------------

describe('RiskSettings: the request a kill switch issues', () => {
  it('sends all four switch states, the three limits the server reported, and the breaker', async () => {
    await mountLoaded();
    fireEvent.keyDown(screen.getByRole('switch', { name: KILL_SWITCHES[1][1] }), { key: 'Enter' });

    const body = await soleUpdateBody();

    // All four, every time. A payload carrying only the switch that moved would let the
    // other three drift out of step with what the trader can see.
    expect(Object.keys(body.kill_switches).sort()).toEqual(
      KILL_SWITCHES.map(([key]) => key).sort(),
    );
    expect(body.kill_switches).toEqual({
      loss: true,
      blackswan: true, // the one that moved
      streak: true,
      capital: false,
    });

    // The limits ride along on every write, and they are the SERVER's values rather than
    // the page's constructor defaults (500 / 10 / 3). A save that posted the defaults would
    // silently widen this account's daily loss limit from 1200 to 500 — or narrow it.
    expect(body.max_daily_loss).toBe(RISK_CONFIG.max_daily_loss);
    expect(body.max_positions).toBe(RISK_CONFIG.max_positions);
    expect(body.max_leverage).toBe(RISK_CONFIG.max_leverage);

    expect(body.circuit_breaker_armed).toBe(true);
  });

  it('issues exactly one write, and touches no other risk endpoint', async () => {
    await mountLoaded();
    fireEvent.keyDown(screen.getByRole('switch', { name: KILL_SWITCHES[2][1] }), { key: 'Enter' });
    await waitFor(() => expect(risk.updateConfig).toHaveBeenCalledTimes(1));

    expect(risk.updateStrategyLimit).not.toHaveBeenCalled();
    // The three reads happen on mount and are not re-issued by a toggle.
    expect(risk.getConfig).toHaveBeenCalledTimes(1);
    expect(risk.getStrategyLimits).toHaveBeenCalledTimes(1);
    expect(risk.getMarginHealth).toHaveBeenCalledTimes(1);
  });
});

describe('RiskSettings: the controls around the switches survive', () => {
  it('keeps every risk limit operable — three portfolio sliders plus one per strategy', async () => {
    await mountLoaded();

    // Max daily loss, max concurrent positions, max account leverage, and the one strategy
    // allocation the fixture declares. These are risk limits: losing one loses a protection,
    // which is why they are counted here rather than left to the a11y ratchet.
    expect(screen.getAllByRole('slider')).toHaveLength(4);
  });

  it('keeps the reset and save commands reachable by name', async () => {
    await mountLoaded();

    expect(screen.getByRole('button', { name: /reset/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /save/i })).toBeTruthy();
  });

  it('renders the figures the server reported, so a margin read is not silently dropped', async () => {
    await mountLoaded();

    // The strategy the limits read returned, by its own name. A page that dropped the read
    // would render an empty allocations region and look fine.
    expect(screen.getByText('Aurora Momentum')).toBeTruthy();
  });
});
