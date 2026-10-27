/**
 * RISK-03: when the server lowers a limit, the page has to SAY so.
 *
 * WHAT THE SERVER NOW DOES
 * ────────────────────────
 * `PUT /api/risk/settings` holds `max_positions` and `max_leverage` to the platform
 * ceilings (`core/risk_manager.RiskThresholds`, the same figures the order path enforces
 * for an account with no settings row) and STORES the clamped value rather than the one
 * asked for -- because the defect being closed is that what was stored was not what was
 * enforced. It reports what it changed in `limit_adjustments`, and `message` carries its
 * own sentence naming the ceiling.
 *
 * WHY THIS FILE EXISTS
 * ────────────────────
 * `saveRiskConfig` already wrote the server's values back into the form
 * (`setMaxPos(res.data.max_positions ?? ...)`), so the slider ALREADY snapped to the
 * clamped number before this change. That is the half that looks like a bug: a trader
 * drags to 7 slots, the control jumps back to 5, and the notice says *Risk parameters
 * updated successfully.* Nothing tells them a ceiling exists or what it is.
 *
 * So the subject here is the pairing -- the value the control settles on AND the sentence
 * that explains it, in one assertion each way round. `riskSettingsKillSwitch.test.jsx`
 * covers the request's content and accessibility and
 * `riskSettingsStrictModeSingleWrite.test.jsx` covers its cardinality; neither looks at
 * what the page does with an adjusted RESPONSE, and both would stay green if the sentence
 * regressed.
 *
 * `data-alert-severity` is asserted rather than the colour: `ds/Alert` has five severities
 * and picks the hue, icon and live-region role from that one string. `warning` is the
 * deliberate choice -- the save SUCCEEDED, so `error` would overstate it, and `warning` is
 * the polite (`role="status"`) severity, so a trader is told once rather than interrupted.
 *
 * NEGATIVE CONTROL: measured, not assumed. Reverting `saveRiskConfig`'s notice to the
 * unconditional `{ severity: 'info', message: 'Risk parameters updated successfully.' }`
 * fails exactly the two cases about the SENTENCE -- `explains the adjustment ...` and
 * `survives a server that reports an adjustment without a message`. The other two stay
 * green on purpose: `settles the control ...` describes behaviour the page already had
 * (it adopts `res.data`), and `reports a plain success ...` is the unchanged path. A file
 * where every case went red would have meant the test was watching the mount, not the fix.
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

/*
 * `isAuthenticated` is required, not optional -- a `vi.mock` factory replaces the WHOLE
 * module and `src/hooks/useEntitlements.js` imports it, so omitting it fails the RUN.
 * tests/test_api_mock_factories_declare_isauthenticated.py enforces this.
 */
vi.mock('../../../src/api', () => ({
  api: { risk },
  default: { risk },
  isAuthenticated: () => true,
}));
vi.mock('../../../src/websocketClient', () => ({
  default: { subscribe: wsSubscribe },
}));

import RiskSettings from '../../../src/pages/RiskSettings';

// ── Fixtures ────────────────────────────────────────────────────────────────────────────

/** The account's stored settings: 7 slots, above the platform ceiling of 5. */
const RISK_CONFIG = Object.freeze({
  max_daily_loss: 4,
  max_positions: 7,
  max_leverage: 3,
  circuit_breaker_armed: true,
  kill_switches: {
    loss: true, blackswan: false, streak: true, capital: false,
  },
});

/** What the server answers once it has held the submission to the ceilings. */
const CLAMPED_RESPONSE = Object.freeze({
  status: 'ok',
  message:
    'Risk settings saved. max_positions was lowered to the platform limit of 5. The order '
    + 'path enforces that ceiling, so a higher value would have been stored but never '
    + 'honoured.',
  limit_adjustments: [
    Object.freeze({
      field: 'max_positions',
      requested: 7,
      applied: 5,
      reason: 'max_positions was lowered to the platform limit of 5.',
    }),
  ],
  platform_ceilings: { max_positions: 5, max_leverage: 10 },
  data: { ...RISK_CONFIG, max_positions: 5 },
});

/** The ordinary answer: nothing adjusted. */
const UNCHANGED_RESPONSE = Object.freeze({
  status: 'ok',
  message: 'Risk settings saved successfully',
  limit_adjustments: [],
  platform_ceilings: { max_positions: 5, max_leverage: 10 },
  data: RISK_CONFIG,
});

/** The one operable switch; the other three render disabled (RISK-02). */
const LOSS_SWITCH = 'Daily Loss Limit';

/** The page renders five `ds/*` trees; 1s is not always enough on a cold worker. */
const SETTLE = { timeout: 5000 };

const checkedState = (name) =>
  screen.getByRole('switch', { name }).getAttribute('aria-checked');

const mountLoaded = async () => {
  const utils = render(
    <MemoryRouter>
      <RiskSettings />
    </MemoryRouter>,
  );
  // The server's answer applied, so nothing below races the initial reads. 7 slots is the
  // stored value and the thing the clamp will move.
  await waitFor(() => expect(screen.queryByText('7 Slots')).not.toBeNull(), SETTLE);
  return utils;
};

/** Toggling the one live switch is the page's most direct write path. */
const save = () => fireEvent.click(screen.getByRole('switch', { name: LOSS_SWITCH }));

const noticeSeverity = () =>
  document.querySelector('[data-ds="alert"]')?.getAttribute('data-alert-severity');

/** The editable position for max positions, which is what the next save submits. */
const positionsControl = () => document.querySelector('#risk-max-positions');

beforeEach(() => {
  risk.getConfig.mockResolvedValue(RISK_CONFIG);
  risk.getStrategyLimits.mockResolvedValue([]);
  risk.getMarginHealth.mockResolvedValue({
    margin_level: 240, used_margin: 42, free_margin: 58, risk_score: 17,
  });
  risk.updateStrategyLimit.mockResolvedValue({ status: 'ok' });
  wsSubscribe.mockReturnValue(() => {});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('RiskSettings: a limit the server lowered', () => {
  it('explains the adjustment instead of reporting a plain success', async () => {
    risk.updateConfig.mockResolvedValue(CLAMPED_RESPONSE);
    await mountLoaded();

    save();

    await waitFor(
      () => expect(screen.queryByText(/platform limit of 5/)).not.toBeNull(),
      SETTLE,
    );

    // The success sentence must be GONE, not merely accompanied. Leaving it would tell the
    // trader their 7 was accepted in the same breath as the control showing 5.
    expect(screen.queryByText(/Risk parameters updated successfully/)).toBeNull();

    // Polite, not assertive, and not `error`: the save worked.
    expect(noticeSeverity()).toBe('warning');
  });

  it('settles the control on the value the server will enforce', async () => {
    risk.updateConfig.mockResolvedValue(CLAMPED_RESPONSE);
    await mountLoaded();

    expect(positionsControl().value).toBe('7');

    save();

    // The CONTROL's position, not the reported figure beside it. The page keeps those two
    // deliberately distinct -- the figure is the server's last READ (`getConfig`, still 7
    // here because a save does not refetch) and the control is the editable position. It
    // is the control that has to land on the enforced value, because it is what the next
    // save will submit: left at 7, every subsequent write would re-submit a limit the
    // server has already refused to honour.
    await waitFor(() => expect(positionsControl().value).toBe('5'), SETTLE);
  });

  it('reports a plain success when nothing was adjusted', async () => {
    risk.updateConfig.mockResolvedValue(UNCHANGED_RESPONSE);
    await mountLoaded();

    save();

    await waitFor(() => expect(checkedState(LOSS_SWITCH)).toBe('false'), SETTLE);
    await waitFor(
      () => expect(screen.queryByText(/Risk parameters updated successfully/)).not.toBeNull(),
      SETTLE,
    );

    // The warning must not cry wolf on an ordinary save, and the control must keep the
    // trader's own value rather than being clamped by the page on its own initiative.
    expect(noticeSeverity()).toBe('info');
    expect(screen.queryByText(/platform limit of/)).toBeNull();
    expect(positionsControl().value).toBe('7');
  });

  it('survives a server that reports an adjustment without a message', async () => {
    // The page must not render `undefined` into the notice if the envelope ever changes.
    risk.updateConfig.mockResolvedValue({
      ...CLAMPED_RESPONSE,
      message: undefined,
    });
    await mountLoaded();

    save();

    await waitFor(
      () => expect(screen.queryByText(/lowered to the platform ceiling/)).not.toBeNull(),
      SETTLE,
    );
    expect(noticeSeverity()).toBe('warning');
  });
});
