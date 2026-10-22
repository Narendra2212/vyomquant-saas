/*
 * FE-09: one kill-switch toggle must issue exactly ONE PUT /api/risk/settings.
 *
 * WHY THIS FILE EXISTS, AND WHY IT IS SEPARATE
 * ────────────────────────────────────────────
 * `riskSettingsKillSwitch.test.jsx` already asserts "exactly one write" in several places,
 * through its shared `soleUpdateBody()` helper:
 *
 *     await waitFor(() => expect(risk.updateConfig).toHaveBeenCalledTimes(1));
 *
 * An exact-count assertion inside a RETRYING `waitFor` is only safe while a second call is
 * impossible: `waitFor` polls until the callback stops throwing, so whether it sees 1 or 2
 * depends on whether the first poll lands between the two calls. That file's assertions
 * therefore FLAKED rather than failing -- three CI runs across two commits, hitting a
 * different test case each time (they share the helper), including one on `main` where
 * every other job passed.
 *
 * The flake was a symptom. `RiskSettings.handleToggleSwitch` called `saveRiskConfig`
 * INSIDE the `setKillSwitches` updater. A React state updater must be pure, and
 * `src/main.jsx` wraps the app in `<React.StrictMode>`, which double-invokes updaters ON
 * PURPOSE to surface exactly that impurity -- so a toggle issued TWO writes to the
 * risk-settings endpoint. The page's own file header had flagged the shape as "an
 * antipattern ... deliberately not fixed here" in an earlier, unrelated commit.
 *
 * So this file does two things that file cannot:
 *
 *   1. It mounts under `<React.StrictMode>`, which is how the real application renders.
 *      That makes the double invocation DETERMINISTIC instead of a race, so the defect
 *      fails a test every time rather than one run in three.
 *   2. It asserts the call count WITHOUT `waitFor` around the count itself: it first
 *      awaits the write's observable settled effect, and only then reads the count. An
 *      extra call then fails deterministically instead of racing the poll.
 *
 * It is a separate file rather than more cases in the existing one because its mount
 * helper differs (StrictMode) and because the existing file's subject is the request's
 * CONTENT and accessibility, while this one's subject is its CARDINALITY.
 *
 * NEGATIVE CONTROL: verified by reverting `handleToggleSwitch` to the pre-fix shape (the
 * write inside the updater) -- `test_one_toggle_issues_exactly_one_write` then reports 2
 * calls and fails, on every run rather than intermittently.
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

vi.mock('../../../src/api', () => ({ api: { risk }, default: { risk } }));
vi.mock('../../../src/websocketClient', () => ({
  default: { subscribe: wsSubscribe },
}));

import RiskSettings from '../../../src/pages/RiskSettings';

// ── Fixtures: the same shapes riskSettingsKillSwitch.test.jsx uses ──────────────────────

const RISK_CONFIG = Object.freeze({
  max_daily_loss: 4,
  max_positions: 7,
  max_leverage: 3,
  circuit_breaker_armed: true,
  kill_switches: {
    loss: true,
    blackswan: false,
    streak: true,
    capital: false,
  },
});

const STRATEGY_LIMITS = Object.freeze([]);

const MARGIN_HEALTH = Object.freeze({
  margin_level: 240,
  used_margin: 42,
  free_margin: 58,
  risk_score: 17,
});

/** The one operable switch; the other three are unenforced and render disabled (RISK-02). */
const LOSS_SWITCH = 'Daily Loss Limit';

/** How the real application renders: StrictMode on. */
const mountStrict = () =>
  render(
    <React.StrictMode>
      <MemoryRouter>
        <RiskSettings />
      </MemoryRouter>
    </React.StrictMode>,
  );

/*
 * The budget every settle-wait below gets.
 *
 * NOT a workaround for a racy assertion. The exact COUNT assertions in this file sit
 * OUTSIDE `waitFor` precisely so they cannot race; this timeout is for the waits that
 * PRECEDE them, which only need to observe that the write has landed. The page renders
 * five `ds/*` primitive trees and StrictMode renders each of them twice, which the
 * sibling file already documents as able to exceed `waitFor`'s 1s default on a cold
 * worker. Measured rather than guessed: this file passed standalone three times with the
 * default and failed once when run alongside riskSettingsKillSwitch.test.jsx, which is
 * exactly that cold-worker case.
 */
const SETTLE = { timeout: 5000 };

const checkedState = (name) =>
  screen.getByRole('switch', { name }).getAttribute('aria-checked');

/** Mounted, and the server's answer applied -- so nothing below races the initial reads. */
const mountLoaded = async () => {
  const utils = mountStrict();
  await waitFor(
    () => {
      expect(checkedState(LOSS_SWITCH)).toBe('true');
    },
    { timeout: 5000 },
  );
  return utils;
};

beforeEach(() => {
  risk.getConfig.mockResolvedValue(RISK_CONFIG);
  risk.getStrategyLimits.mockResolvedValue(STRATEGY_LIMITS);
  risk.getMarginHealth.mockResolvedValue(MARGIN_HEALTH);
  risk.updateConfig.mockResolvedValue({ data: RISK_CONFIG });
  risk.updateStrategyLimit.mockResolvedValue({ status: 'ok' });
  wsSubscribe.mockReturnValue(() => {});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('RiskSettings under StrictMode: one toggle, one write', () => {
  it('issues exactly one write for one toggle', async () => {
    await mountLoaded();

    fireEvent.click(screen.getByRole('switch', { name: LOSS_SWITCH }));

    // Await the write's SETTLED effect first: the switch flips to its new state and the
    // success notice lands, both of which happen after `saveRiskConfig` resolves. Only
    // then read the count -- so a second call is a deterministic failure rather than
    // something that races a `waitFor` poll.
    await waitFor(() => expect(checkedState(LOSS_SWITCH)).toBe('false'), SETTLE);
    await waitFor(
      () => expect(screen.queryByText(/Risk parameters updated successfully/)).not.toBeNull(),
      SETTLE,
    );

    expect(risk.updateConfig).toHaveBeenCalledTimes(1);
  });

  it('issues exactly one write on Enter, the keyboard path', async () => {
    await mountLoaded();

    fireEvent.keyDown(screen.getByRole('switch', { name: LOSS_SWITCH }), { key: 'Enter' });

    await waitFor(() => expect(checkedState(LOSS_SWITCH)).toBe('false'), SETTLE);
    await waitFor(
      () => expect(screen.queryByText(/Risk parameters updated successfully/)).not.toBeNull(),
      SETTLE,
    );

    expect(risk.updateConfig).toHaveBeenCalledTimes(1);
  });

  it('issues exactly two writes for two toggles, not four', async () => {
    /*
     * The positive control for the two tests above. If `updateConfig` were somehow never
     * called, or called once and then suppressed, those would pass for the wrong reason.
     * Two discrete toggles must produce two writes -- and under the pre-fix code each
     * would have produced two, giving four.
     */
    await mountLoaded();
    const toggle = () => screen.getByRole('switch', { name: LOSS_SWITCH });

    fireEvent.click(toggle());
    await waitFor(() => expect(checkedState(LOSS_SWITCH)).toBe('false'), SETTLE);
    await waitFor(
      () => expect(risk.updateConfig.mock.calls.length).toBeGreaterThan(0), SETTLE,
    );

    fireEvent.click(toggle());
    await waitFor(() => expect(checkedState(LOSS_SWITCH)).toBe('true'), SETTLE);

    await waitFor(() => expect(risk.updateConfig).toHaveBeenCalledTimes(2), SETTLE);
    expect(risk.updateConfig).toHaveBeenCalledTimes(2);
  });

  it('sends the toggled value, so the single write is the RIGHT write', async () => {
    /*
     * De-duplicating is only correct if the surviving call carries the user's intent. The
     * fixture has `loss: true`; one toggle must send `loss: false` and leave the other
     * three guards exactly as the server reported them (RISK-02: the page must not
     * rewrite what the server holds).
     */
    await mountLoaded();

    fireEvent.click(screen.getByRole('switch', { name: LOSS_SWITCH }));
    await waitFor(() => expect(checkedState(LOSS_SWITCH)).toBe('false'), SETTLE);

    const [body] = risk.updateConfig.mock.calls[0];
    expect(body.kill_switches).toEqual({
      loss: false,
      blackswan: false,
      streak: true,
      capital: false,
    });
  });

  it('does not write at all on mount, before any interaction', async () => {
    /*
     * StrictMode double-invokes effects too, not just updaters. A page that wrote from an
     * effect would show up here, and would make every other count in this file wrong.
     */
    await mountLoaded();
    expect(risk.updateConfig).not.toHaveBeenCalled();
  });
});
