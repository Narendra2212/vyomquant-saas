/**
 * The pre-deployment summary panel (task 18.1). Requirements 13.3, 13.4, 13.5, 13.6.
 *
 * WHAT IS ASSERTED, AND WHY EACH PART MATTERS
 * ===========================================
 * * **Every condition the server reported is rendered**, with its own verdict — that is the
 *   whole of Requirement 13.3, and it is why there is no client-side list of condition
 *   names: a name this build has never seen must still appear, or a newly added mandatory
 *   check would be invisible to the very author it blocks.
 * * **`pending` is not drawn as a softer `passed`.** It means the condition could not be
 *   evaluated because something it depends on failed, and it keeps Deploy disabled exactly
 *   as `failed` does (Requirement 13.4). Its glyph, its word and its colour are all its
 *   own.
 * * **The wording is the server's**, character for character, so the panel and the write
 *   path name a refusal identically and nothing is paraphrased into a friendlier claim.
 * * **A summary that could not be read is rendered as "nothing verified"**, never as an
 *   empty, reassuring list.
 *
 * The fixtures are the wire shapes (`BindingSummary.to_dict()`), read through the shipped
 * `normalizePreflight` rather than hand-built — the assertion is on the JSON-to-UI mapping
 * end to end, so nothing is mocked here at all.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import DeployPreflightPanel, {
  PENDING_FALLBACK_NOTE,
  UNAVAILABLE_NOTE,
  conditionLabel,
  orderConditions,
} from '../../src/components/DeployPreflightPanel';
import {
  CONDITION_FAILED,
  CONDITION_PASSED,
  CONDITION_PENDING,
  normalizePreflight,
} from '../../src/lib/deployPreflight';

// ── Fixtures: the wire ──────────────────────────────────────────────────────────────────

const FAILURE_MESSAGE =
  'Available balance $412.00 is less than the required $500.00 (notional + fees).';

const allPassed = () => ({
  deployable: true,
  conditions: [
    { name: 'deploy_prerequisites', status: CONDITION_PASSED },
    { name: 'version_ready', status: CONDITION_PASSED },
    { name: 'exchange_account', status: CONDITION_PASSED, detail: { exchange_id: 'kraken' } },
  ],
});

const partiallyFailed = () => ({
  deployable: false,
  conditions: [
    { name: 'version_ready', status: CONDITION_PASSED },
    {
      name: 'account_balance',
      status: CONDITION_FAILED,
      code: 'INSUFFICIENT_BALANCE',
      message: FAILURE_MESSAGE,
    },
    { name: 'symbol_available', status: CONDITION_PENDING, reason: 'blocked by account_balance' },
  ],
});

/** A fixed instant, so the "last checked" line is deterministic. */
const CHECKED_AT = new Date('2024-05-01T10:20:30Z').getTime();

const renderPanel = (body, extra = {}) =>
  render(<DeployPreflightPanel {...normalizePreflight(body)} checkedAt={CHECKED_AT} {...extra} />);

const rows = () => [...document.querySelectorAll('[data-testid="preflight-condition"]')];
const row = (name) =>
  document.querySelector(`[data-testid="preflight-condition"][data-condition="${name}"]`);

// ── The mapping ─────────────────────────────────────────────────────────────────────────

describe('DeployPreflightPanel — a fully-passed summary', () => {
  it('renders one row per condition, each marked passed', () => {
    renderPanel(allPassed());

    expect(rows()).toHaveLength(3);
    expect(rows().map((r) => r.dataset.status)).toEqual(['passed', 'passed', 'passed']);
    expect(screen.getByTestId('preflight-panel').dataset.deployable).toBe('true');
    expect(screen.getByTestId('preflight-verdict').textContent).toContain('unblocked');
  });

  it('renders the detail the server sent, and nothing it did not', () => {
    renderPanel(allPassed());

    expect(row('exchange_account').textContent).toContain('kraken');
    expect(row('version_ready').textContent).not.toContain('kraken');
  });
});

describe('DeployPreflightPanel — a partially-failed summary', () => {
  it('names the failed condition with the server’s own message and code', () => {
    renderPanel(partiallyFailed());

    const failed = row('account_balance');
    expect(failed.dataset.status).toBe('failed');
    expect(failed.textContent).toContain(FAILURE_MESSAGE);
    expect(failed.textContent).toContain('INSUFFICIENT_BALANCE');
  });

  it('draws pending as its own state, not as a weaker pass', () => {
    renderPanel(partiallyFailed());

    const pending = row('symbol_available');
    expect(pending.dataset.status).toBe('pending');
    expect(pending.textContent).toContain('PENDING');
    // The server said what blocked it, so that is what is shown — not a paraphrase.
    expect(pending.textContent).toContain('blocked by account_balance');
    // Nothing about this row may read as a satisfied check.
    expect(pending.textContent).not.toContain('PASSED');
    expect(pending.textContent).not.toContain('✓');
  });

  it('says a pending condition was not evaluated when the server gave no reason', () => {
    renderPanel({
      deployable: false,
      conditions: [{ name: 'symbol_available', status: CONDITION_PENDING }],
    });

    expect(row('symbol_available').textContent).toContain(PENDING_FALLBACK_NOTE);
  });

  it('reports the counts and keeps the verdict blocked', () => {
    renderPanel(partiallyFailed());

    const verdict = screen.getByTestId('preflight-verdict').textContent;
    expect(verdict).toContain('1 of 3 passed');
    expect(verdict).toContain('1 failed');
    expect(verdict).toContain('1 not evaluated');
    expect(verdict).toContain('blocked');
    expect(screen.getByTestId('preflight-panel').dataset.deployable).toBe('false');
  });
});

describe('DeployPreflightPanel — what it refuses to hide', () => {
  it('renders a condition name it has never heard of', () => {
    // A client-side allow-list would drop this row, hiding a mandatory check from the
    // author it is blocking.
    renderPanel({
      deployable: false,
      conditions: [{ name: 'brand_new_mandatory_gate', status: CONDITION_FAILED, message: 'Nope.' }],
    });

    const unknown = row('brand_new_mandatory_gate');
    expect(unknown).not.toBeNull();
    expect(unknown.textContent).toContain('Brand new mandatory gate');
    expect(unknown.textContent).toContain('Nope.');
  });

  it('renders a status it cannot read as unreadable rather than as passed', () => {
    renderPanel({ deployable: true, conditions: [{ name: 'version_ready', status: 'OK' }] });

    expect(row('version_ready').dataset.status).toBe('unreadable');
    expect(screen.getByTestId('preflight-panel').dataset.deployable).toBe('false');
  });

  it('renders an empty summary as nothing verified', () => {
    renderPanel({ deployable: true, conditions: [] });

    expect(rows()).toHaveLength(0);
    expect(screen.getByTestId('preflight-unavailable').textContent).toBe(UNAVAILABLE_NOTE);
    expect(screen.getByTestId('preflight-verdict').textContent).toContain('blocked');
  });

  it('flags a summary whose own flag contradicts its conditions', () => {
    renderPanel({
      deployable: true,
      conditions: [{ name: 'version_ready', status: CONDITION_FAILED, message: 'Version is DRAFT.' }],
    });

    expect(screen.getByTestId('preflight-disagreement')).toBeTruthy();
    expect(screen.getByTestId('preflight-panel').dataset.deployable).toBe('false');
  });
});

describe('DeployPreflightPanel — staying current (Requirement 13.6)', () => {
  it('re-asks on demand', () => {
    const refresh = vi.fn();
    renderPanel(allPassed(), { refresh });

    fireEvent.click(screen.getByTestId('preflight-refresh'));
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('says when the summary was last answered', () => {
    renderPanel(allPassed());
    expect(screen.getByTestId('preflight-checked-at').textContent).toBe(
      `checked ${new Date(CHECKED_AT).toLocaleTimeString()}`,
    );
  });

  it('does not claim a verdict before the first answer has arrived', () => {
    render(<DeployPreflightPanel conditions={[]} isLoading checkedAt={null} />);

    expect(screen.getByTestId('preflight-checked-at').textContent).toBe('checking...');
    expect(screen.getByTestId('preflight-unavailable').textContent).toContain(
      'Running the deployment checks',
    );
    expect(screen.getByTestId('preflight-verdict').textContent).toContain('blocked');
  });

  it('lists the deployment the verdicts are about (Requirement 13.3)', () => {
    renderPanel(allPassed(), {
      summary: [
        ['Version', 'v1.2'],
        ['Capital', '$10,000'],
      ],
    });

    const summary = screen.getByTestId('preflight-summary');
    expect(summary.textContent).toContain('v1.2');
    expect(summary.textContent).toContain('$10,000');
  });
});

describe('ordering and labelling', () => {
  it('renders in the gate’s evaluation order, with unknown names kept and shown last', () => {
    const ordered = orderConditions([
      { name: 'risk_config' },
      { name: 'not_in_the_known_order' },
      { name: 'deploy_prerequisites' },
      { name: 'also_unknown' },
    ]).map((c) => c.name);

    expect(ordered).toEqual([
      'deploy_prerequisites',
      'risk_config',
      'not_in_the_known_order',
      'also_unknown',
    ]);
  });

  it('labels a condition from its own name, with no lookup table', () => {
    expect(conditionLabel('timeframe_supported')).toBe('Timeframe supported');
    expect(conditionLabel('')).toBe('Unnamed check');
    expect(conditionLabel(null)).toBe('Unnamed check');
  });
});
