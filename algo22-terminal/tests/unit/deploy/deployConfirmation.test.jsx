/**
 * `components/deploy/DeployConfirmation` — vyomquant-ui-redesign task 10.5.
 * Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 19.1. `design.md` §8.1, §8.2, §8.3, §8.5.
 *
 * The state machine itself is asserted by `tests/unit/lib/deployFlow.test.js` (35 cases).
 * This file asserts the one thing only the rendered component can be wrong about: **when the
 * backend mutation is issued**, and what the trader sees on the way to it.
 *
 * P13's shape, here: the deploy endpoint is not called at `Configure`, not called at
 * `Review` on the Live path, not called from `AckLive` until the acknowledgement is ticked
 * *and* confirm is activated, called exactly once when it is, and not called at all by
 * Escape or cancel.
 *
 * P14's shape, here: the real-funds statement is absent from the Paper and Backtest DOM
 * entirely — not hidden, not `aria-hidden`, not present-and-unstyled. `flow.steps` carries no
 * `ackLive` entry off the Live path, so there is nothing for this component to hide.
 *
 * The API is mocked at the module boundary rather than injected through a prop: the endpoint
 * is fixed by Requirement 19.1, and a seam that let a caller point the deploy somewhere else
 * would be a worse component than one that is awkward to test.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, screen, act, fireEvent, waitFor } from '@testing-library/react';

// `vi.mock`'s factory is hoisted above every module-level statement, so the spies it closes
// over have to be hoisted with it.
const { deployVersion, deployPreflight } = vi.hoisted(() => ({
  deployVersion: vi.fn(),
  deployPreflight: vi.fn(),
}));

vi.mock('../../../src/api', () => ({
  endpoints: { strategies: { deployVersion, deployPreflight } },
  api: { strategies: { deployVersion, deployPreflight } },
  default: { strategies: { deployVersion, deployPreflight } },
}));

import DeployConfirmation from '../../../src/components/deploy/DeployConfirmation';
import { resetOverlayRegistry } from '../../../src/components/ds/overlayRegistry';
import { deploymentRequest } from '../../../src/lib/deployPreflight';

/* ══════════════════════════════════════════════════════════════════════════
 * Fixtures
 * ══════════════════════════════════════════════════════════════════════════ */

/** A fully configured deployment. All eight Requirement 8.1 fields are available. */
const BASE_CONFIG = Object.freeze({
  strategyId: 'strat-1',
  version: '1.2',
  account: Object.freeze({ id: 'acct-9', label: 'Binance main', exchange_id: 'binance' }),
  strategyName: 'RSI Reversion',
  market: 'BTC/USDT',
  capital: '10000',
  tradeSizePct: '2',
  riskConfigId: 'risk-3',
});

const configFor = (environment, overrides) =>
  Object.freeze({ ...BASE_CONFIG, environment, ...(overrides ?? {}) });

/**
 * A preflight summary that permits the deploy.
 *
 * Passed in rather than polled, which is also the shape a page uses: `Strategies.jsx` already
 * runs one `useDeployPreflight`, and handing its summary in is what stops this component
 * asking the same question a second time.
 */
const PASSING_GATE = Object.freeze({
  deployable: true,
  reported: true,
  conditions: Object.freeze([
    Object.freeze({ name: 'exchange_account', status: 'passed' }),
    Object.freeze({ name: 'risk_config', status: 'passed' }),
  ]),
  failed: Object.freeze([]),
  pending: Object.freeze([]),
  error: null,
  isLoading: false,
  checkedAt: 1_700_000_000_000,
});

const BLOCKING_GATE = Object.freeze({
  ...PASSING_GATE,
  deployable: false,
  reported: false,
  conditions: Object.freeze([
    Object.freeze({
      name: 'exchange_account',
      status: 'failed',
      message: 'No exchange account is connected.',
    }),
  ]),
});

beforeEach(() => {
  resetOverlayRegistry();
  vi.stubEnv('DEV', true);
  deployVersion.mockReset();
  deployVersion.mockResolvedValue({ success: true, deployment: { id: 'dep-1' } });
  deployPreflight.mockReset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  resetOverlayRegistry();
});

/* ══════════════════════════════════════════════════════════════════════════
 * Queries
 * ══════════════════════════════════════════════════════════════════════════ */

const confirmButton = () => document.body.querySelector('[data-ds="confirm-dialog-confirm"]');
const cancelButton = () => document.body.querySelector('[data-ds="confirm-dialog-cancel"]');
const flowNode = () => document.body.querySelector('[data-deploy="flow"]');
const stateOf = () => flowNode()?.getAttribute('data-deploy-state') ?? null;
const stepIds = () =>
  [...document.body.querySelectorAll('[data-deploy-step]')].map((node) =>
    node.getAttribute('data-deploy-step'),
  );
const ackRegion = () => document.body.querySelector('[data-ds="confirm-dialog-acknowledgement"]');
const markers = () => [...document.body.querySelectorAll('[data-metric-marker="not-available"]')];

/** One click on the dialog's confirm action. */
const clickConfirm = () => {
  fireEvent.click(confirmButton());
};

function open(props) {
  return render(
    <DeployConfirmation open config={configFor('PAPER')} onCancel={() => {}} {...props} />,
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * The step list, rendered from the flow (Requirement 8.4, P14)
 * ══════════════════════════════════════════════════════════════════════════ */

describe('DeployConfirmation: the step list is the flow\'s, not a filtered fixed one', () => {
  it('renders three steps on the Live path and two off it', () => {
    open({ config: configFor('LIVE'), preflight: PASSING_GATE });
    expect(stepIds()).toEqual(['configure', 'review', 'ackLive']);
    cleanup();
    resetOverlayRegistry();

    open({ config: configFor('PAPER'), preflight: PASSING_GATE });
    expect(stepIds()).toEqual(['configure', 'review']);
  });

  it('names the environment and titles the dialog from the flow (Requirement 8.5)', () => {
    open({ config: configFor('LIVE'), preflight: PASSING_GATE });
    expect(flowNode().getAttribute('data-deploy-environment')).toBe('LIVE');
    expect(screen.getByRole('dialog', { name: 'Deploy to live trading' })).toBeTruthy();
    expect(screen.getByRole('dialog').getAttribute('data-ds-intent')).toBe('live');
  });

  it('titles a paper deployment differently and does not paint it in the live intent', () => {
    open({ config: configFor('PAPER'), preflight: PASSING_GATE });
    expect(screen.getByRole('dialog', { name: 'Start paper session' })).toBeTruthy();
    expect(screen.getByRole('dialog').getAttribute('data-ds-intent')).toBe('neutral');
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * P13 — nothing reaches the backend before the confirmation step is satisfied
 * ══════════════════════════════════════════════════════════════════════════ */

describe('DeployConfirmation: the deploy call is not made before it is confirmed', () => {
  it('does not call the endpoint at Configure', () => {
    open({ config: configFor('LIVE'), preflight: PASSING_GATE });
    expect(stateOf()).toBe('Configure');
    expect(deployVersion).not.toHaveBeenCalled();
  });

  it('does not call the endpoint at Review on the Live path', () => {
    open({ config: configFor('LIVE'), preflight: PASSING_GATE });
    clickConfirm();
    expect(stateOf()).toBe('Review');
    expect(deployVersion).not.toHaveBeenCalled();
  });

  it('does not call the endpoint on arriving at Review on the Paper path', () => {
    open({ config: configFor('PAPER'), preflight: PASSING_GATE });
    clickConfirm();
    expect(stateOf()).toBe('Review');
    expect(deployVersion).not.toHaveBeenCalled();
  });

  it('does not call the endpoint on arriving at AckLive', () => {
    open({ config: configFor('LIVE'), preflight: PASSING_GATE });
    clickConfirm(); // → Review
    clickConfirm(); // → AckLive
    expect(stateOf()).toBe('AckLive');
    expect(deployVersion).not.toHaveBeenCalled();
  });

  it('holds confirm shut at AckLive until the acknowledgement is ticked (Requirement 8.3)', () => {
    open({ config: configFor('LIVE'), preflight: PASSING_GATE });
    clickConfirm();
    clickConfirm();

    expect(ackRegion()).not.toBeNull();
    const checkbox = screen.getByRole('checkbox');
    expect(checkbox.checked).toBe(false);
    expect(confirmButton().disabled).toBe(true);

    // The box, then the button — two trader actions, in that order, or nothing.
    clickConfirm();
    expect(deployVersion).not.toHaveBeenCalled();
    expect(stateOf()).toBe('AckLive');

    fireEvent.click(checkbox);
    expect(confirmButton().disabled).toBe(false);
    // Ticking alone is not a confirmation.
    expect(deployVersion).not.toHaveBeenCalled();
  });

  it('sends the request only once the ticked acknowledgement is explicitly confirmed', async () => {
    const config = configFor('LIVE');
    open({ config, preflight: PASSING_GATE });
    clickConfirm();
    clickConfirm();
    fireEvent.click(screen.getByRole('checkbox'));

    await act(async () => {
      clickConfirm();
    });

    const expected = deploymentRequest(config);
    expect(deployVersion).toHaveBeenCalledTimes(1);
    expect(deployVersion).toHaveBeenCalledWith('strat-1', '1.2', expected.body, {
      environment: expected.environment,
    });
    expect(expected.environment).toBe('live');
  });

  it('sends from Review on the Paper path, with no acknowledgement in between', async () => {
    const config = configFor('PAPER');
    open({ config, preflight: PASSING_GATE });
    clickConfirm(); // → Review

    await act(async () => {
      clickConfirm(); // → Submitting, directly
    });

    const expected = deploymentRequest(config);
    expect(deployVersion).toHaveBeenCalledTimes(1);
    expect(deployVersion).toHaveBeenCalledWith('strat-1', '1.2', expected.body, {
      environment: expected.environment,
    });
    expect(expected.environment).toBe('paper');
  });

  it('reports the accepted deployment to the page once', async () => {
    const deployed = vi.fn();
    open({ config: configFor('PAPER'), preflight: PASSING_GATE, onDeployed: deployed });
    clickConfirm();
    await act(async () => {
      clickConfirm();
    });

    await waitFor(() => expect(deployed).toHaveBeenCalledTimes(1));
    expect(deployed).toHaveBeenCalledWith({ success: true, deployment: { id: 'dep-1' } });
    expect(stateOf()).toBe('Deployed');
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * Exactly once, and not again while in flight
 * ══════════════════════════════════════════════════════════════════════════ */

describe('DeployConfirmation: the request cannot be sent twice', () => {
  /** A deploy that never settles, so the flow stays in `Submitting` for the whole test. */
  function inFlight() {
    let settle;
    deployVersion.mockImplementation(
      () =>
        new Promise((resolve) => {
          settle = resolve;
        }),
    );
    return () => settle;
  }

  it('does not repeat the request when confirm is activated again in flight', async () => {
    const settle = inFlight();
    const view = render(
      <DeployConfirmation
        open
        config={configFor('PAPER')}
        onCancel={() => {}}
        preflight={PASSING_GATE}
      />,
    );

    clickConfirm(); // → Review
    await act(async () => {
      clickConfirm(); // → Submitting
    });

    expect(stateOf()).toBe('Submitting');
    expect(deployVersion).toHaveBeenCalledTimes(1);

    // Both actions are shut while a request is in flight, and Escape is inert.
    expect(confirmButton().disabled).toBe(true);
    expect(cancelButton().disabled).toBe(true);

    clickConfirm();
    fireEvent.click(cancelButton());
    fireEvent.keyDown(document.activeElement ?? document, { key: 'Escape' });
    expect(deployVersion).toHaveBeenCalledTimes(1);

    // A parent re-render must not re-issue the submission either.
    view.rerender(
      <DeployConfirmation
        open
        config={configFor('PAPER')}
        onCancel={() => {}}
        onFailed={() => {}}
        preflight={PASSING_GATE}
      />,
    );
    expect(deployVersion).toHaveBeenCalledTimes(1);

    await act(async () => {
      settle()({ success: true });
    });
    expect(deployVersion).toHaveBeenCalledTimes(1);
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * P14 — the real-funds statement exists only on the Live path
 * ══════════════════════════════════════════════════════════════════════════ */

describe('DeployConfirmation: the real-funds statement (Requirements 8.2, 8.4)', () => {
  it('is present on the Live path, naming the venue and the account', () => {
    open({ config: configFor('LIVE'), preflight: PASSING_GATE });
    clickConfirm();
    clickConfirm();
    expect(ackRegion().textContent).toContain(
      'Confirming will place real orders on binance using real funds in account Binance main.',
    );
    expect(
      screen.getByLabelText('I understand this places real orders with real funds'),
    ).toBeTruthy();
  });

  for (const environment of ['PAPER', 'BACKTEST']) {
    it(`is absent from the whole ${environment} DOM, on every step`, () => {
      open({ config: configFor(environment), preflight: PASSING_GATE });

      const seen = [];
      for (let step = 0; step < 2; step += 1) {
        seen.push(document.body.textContent);
        expect(ackRegion()).toBeNull();
        expect(screen.queryByRole('checkbox')).toBeNull();
        expect(stepIds()).not.toContain('ackLive');
        clickConfirm();
      }

      for (const text of seen) {
        expect(text).not.toMatch(/real funds/i);
        expect(text).not.toMatch(/real orders/i);
        expect(text).not.toMatch(/I understand/i);
      }
    });
  }
});

/* ══════════════════════════════════════════════════════════════════════════
 * Requirement 8.1 — the eight review fields
 * ══════════════════════════════════════════════════════════════════════════ */

describe('DeployConfirmation: the review grid', () => {
  const labels = () =>
    [...document.body.querySelectorAll('[data-ds="confirm-dialog-review"] dt')].map(
      (node) => node.textContent,
    );

  it('renders all eight fields, in the requirement\'s order', () => {
    open({ config: configFor('PAPER'), preflight: PASSING_GATE });
    clickConfirm();
    expect(labels()).toEqual([
      'Strategy',
      'Version',
      'Exchange',
      'Account',
      'Market',
      'Quantity / sizing',
      'Risk configuration',
      'Estimated exposure',
    ]);
    expect(markers()).toHaveLength(0);
  });

  it('renders the not-available marker for a field the configuration does not carry, and does not disable confirm', async () => {
    const config = configFor('PAPER', { riskConfigId: undefined });
    open({ config, preflight: PASSING_GATE });
    clickConfirm();

    // Still eight rows: a missing field is never an omitted row.
    expect(labels()).toHaveLength(8);
    const marker = markers();
    expect(marker).toHaveLength(1);
    expect(marker[0].getAttribute('aria-label')).toBe('Risk configuration: not available');
    expect(marker[0].getAttribute('title')).toBe(
      'The deployment configuration does not name a risk configuration.',
    );
    // Never a zero, never a blank.
    expect(marker[0].textContent).not.toMatch(/\b0\b/);

    // §8.3: "the flow is not blocked by it".
    expect(confirmButton().disabled).toBe(false);
    await act(async () => {
      clickConfirm();
    });
    expect(deployVersion).toHaveBeenCalledTimes(1);
  });

  it('renders the estimated exposure the preflight would send, grouped and unrounded', () => {
    open({
      config: configFor('PAPER', { capital: '250000', tradeSizePct: '2' }),
      preflight: PASSING_GATE,
    });
    clickConfirm();
    const values = [...document.body.querySelectorAll('[data-ds="confirm-dialog-review"] dd')];
    expect(values[7].textContent).toBe('5,000');
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * Cancel and Escape (Requirement 8.3)
 * ══════════════════════════════════════════════════════════════════════════ */

describe('DeployConfirmation: leaving the flow sends nothing', () => {
  it('sends nothing on cancel, from any step', () => {
    const cancelled = vi.fn();
    open({ config: configFor('LIVE'), preflight: PASSING_GATE, onCancel: cancelled });
    clickConfirm();
    clickConfirm();
    fireEvent.click(screen.getByRole('checkbox'));

    fireEvent.click(cancelButton());
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(deployVersion).not.toHaveBeenCalled();
    expect(stateOf()).toBe('Cancelled');
  });

  it('sends nothing on Escape, with the acknowledgement already ticked', () => {
    const cancelled = vi.fn();
    open({ config: configFor('LIVE'), preflight: PASSING_GATE, onCancel: cancelled });
    clickConfirm();
    clickConfirm();
    fireEvent.click(screen.getByRole('checkbox'));

    fireEvent.keyDown(document.activeElement ?? document, { key: 'Escape' });
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(deployVersion).not.toHaveBeenCalled();
    expect(stateOf()).toBe('Cancelled');
  });

  it('steps back without sending, and re-asks for the acknowledgement', () => {
    open({ config: configFor('LIVE'), preflight: PASSING_GATE });
    clickConfirm();
    clickConfirm();
    fireEvent.click(screen.getByRole('checkbox'));

    fireEvent.click(document.body.querySelector('[data-deploy="back"]'));
    expect(stateOf()).toBe('Review');
    expect(ackRegion()).toBeNull();
    expect(deployVersion).not.toHaveBeenCalled();

    clickConfirm();
    expect(stateOf()).toBe('AckLive');
    expect(screen.getByRole('checkbox').checked).toBe(false);
    expect(confirmButton().disabled).toBe(true);
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * The preflight stays the authority (Requirement 19.1, 13.4, 13.6)
 * ══════════════════════════════════════════════════════════════════════════ */

describe('DeployConfirmation: the preflight is the authority', () => {
  it('renders the existing panel on the Review step, from the summary it was handed', () => {
    open({ config: configFor('PAPER'), preflight: PASSING_GATE });
    expect(document.body.querySelector('[data-testid="preflight-panel"]')).toBeNull();
    clickConfirm();
    const panel = document.body.querySelector('[data-testid="preflight-panel"]');
    expect(panel).not.toBeNull();
    expect(panel.getAttribute('data-deployable')).toBe('true');
    expect(document.body.querySelectorAll('[data-testid="preflight-condition"]')).toHaveLength(2);
  });

  it('refuses to submit while the gate says the deployment is not permitted', () => {
    open({ config: configFor('PAPER'), preflight: BLOCKING_GATE });
    clickConfirm();
    expect(stateOf()).toBe('Review');
    expect(document.body.querySelector('[data-deploy="gate-refusal"]').textContent).toBe(
      'Not every mandatory deployment check has passed yet, so nothing was deployed.',
    );

    clickConfirm();
    expect(deployVersion).not.toHaveBeenCalled();
    expect(stateOf()).toBe('Review');
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * Structural blockers and failure copy
 * ══════════════════════════════════════════════════════════════════════════ */

describe('DeployConfirmation: refusals a trader can act on', () => {
  it('holds every forward edge shut, and says why, when the target is unresolved', () => {
    open({ config: configFor('production'), preflight: PASSING_GATE });
    expect(stepIds()).toEqual(['configure']);
    expect(document.body.querySelector('[data-deploy="blocked"]').textContent).toContain(
      'The deployment target is not one of Live, Paper or Backtest',
    );
    clickConfirm();
    expect(stateOf()).toBe('Configure');
    expect(deployVersion).not.toHaveBeenCalled();
  });

  it('refuses a strategy with no version, in the words the page already refuses with', () => {
    open({ config: configFor('PAPER', { version: undefined }), preflight: PASSING_GATE });
    expect(document.body.querySelector('[data-deploy="blocked"]').textContent).toContain(
      'This strategy has no current version to deploy.',
    );
    clickConfirm();
    expect(deployVersion).not.toHaveBeenCalled();
  });

  it('renders a refused deploy as translated copy, never a status or an exception', async () => {
    const failed = vi.fn();
    deployVersion.mockRejectedValue(
      Object.assign(new Error('AxiosError: Request failed with status code 503'), {
        status: 503,
        category: 'SERVER_ERROR',
        requestId: 'req-77',
      }),
    );

    open({ config: configFor('PAPER'), preflight: PASSING_GATE, onFailed: failed });
    clickConfirm();
    await act(async () => {
      clickConfirm();
    });

    await waitFor(() => expect(stateOf()).toBe('Failed'));
    expect(failed).toHaveBeenCalledTimes(1);

    const alert = document.body.querySelector('[data-ds="dialog-error"]');
    expect(alert).not.toBeNull();
    expect(alert.textContent).toContain('Something went wrong on our side');
    expect(alert.textContent).not.toMatch(/503/);
    expect(alert.textContent).not.toMatch(/\w*(Error|Exception)\b/);

    // §8.3's `Failed --> Review`, and nothing re-sent by taking it.
    fireEvent.click(confirmButton());
    expect(stateOf()).toBe('Review');
    expect(deployVersion).toHaveBeenCalledTimes(1);
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * Opening, and the page's own Configure content
 * ══════════════════════════════════════════════════════════════════════════ */

describe('DeployConfirmation: opening and closing', () => {
  it('renders nothing when closed and sends nothing', () => {
    render(
      <DeployConfirmation
        open={false}
        config={configFor('LIVE')}
        onCancel={() => {}}
        preflight={PASSING_GATE}
      />,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(deployVersion).not.toHaveBeenCalled();
  });

  it('opens a fresh flow, remembering nothing from the previous opening', () => {
    const view = render(
      <DeployConfirmation
        open
        config={configFor('LIVE')}
        onCancel={() => {}}
        preflight={PASSING_GATE}
      />,
    );
    clickConfirm();
    clickConfirm();
    expect(stateOf()).toBe('AckLive');

    const props = { config: configFor('LIVE'), onCancel: () => {}, preflight: PASSING_GATE };
    view.rerender(<DeployConfirmation open={false} {...props} />);
    view.rerender(<DeployConfirmation open {...props} />);

    expect(stateOf()).toBe('Configure');
    expect(deployVersion).not.toHaveBeenCalled();
  });

  it('hosts the page\'s target form on the Configure step only', () => {
    open({
      config: configFor('PAPER'),
      preflight: PASSING_GATE,
      children: <p data-testid="target-form">the page&apos;s own form</p>,
    });
    expect(screen.getByTestId('target-form')).toBeTruthy();
    clickConfirm();
    expect(screen.queryByTestId('target-form')).toBeNull();
  });
});
