/**
 * The Strategies page's archive path (task 16.3).
 *
 * Requirements 2.9, 2.10, 3.1. `DELETE /api/strategies/{id}` is the same path and method
 * the page always called; task 5.1 rewired the route server-side from a hard row delete
 * into a soft archive. What is asserted here is everything that changed on this side:
 *
 * * **The endpoint is unchanged.** One DELETE to `/api/strategies/{id}` through the shared
 *   client, with no new route and no new body.
 * * **The confirmation describes archival.** It names the action, says the records survive,
 *   and does not promise a deletion the backend no longer performs (Requirements 2.8, 2.9,
 *   3.2).
 * * **A 409 is rendered as the specific refusal.** Each blocking deployment is named by
 *   identifier and state (Requirement 3.1), the user is told to stop them (Requirement
 *   2.10), and the list is restored unchanged.
 *
 * The 409 fixture is the body that actually reaches the client, built the way the server
 * builds it: `strategy_archive.ArchiveRejected("STRATEGY_HAS_ACTIVE_DEPLOYMENTS", …,
 * {strategy_id, blocking_deployments, blocking_states})` → the delete handler's
 * `HTTPException(409, detail=e.to_detail())` → `main.py`'s global handler →
 * `core/schemas.create_api_error_response`, which lifts `error`/`message` to the top level,
 * echoes the raw dict under `detail` and collects the rest under `details`. The deployment
 * entries are `strategy_lifecycle.binding_report`, field for field.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { readFileSync } from 'node:fs';
import path from 'node:path';

// The shared native-dialog detector (Requirement 19.4, design.md §1.8). Task 10.11's
// repo-wide `no-native-dialogs` guard reads the same function, so the per-page assertion
// here and the CI guard cannot disagree about what a native dialog is.
import { describeNativeDialogs } from './guards/native-dialogs';

import {
  ARCHIVE_BLOCKED_CODE,
  ARCHIVE_BLOCKED_FALLBACK_MESSAGE,
  ARCHIVE_FAILED_FALLBACK_MESSAGE,
  OWN_STATUS_LABEL,
  OWN_STATUS_SOURCE,
  blockingDeploymentsFrom,
  describeArchiveFailure,
  describeBlockingDeployment,
  normalizeBlockingDeployment,
} from '../../src/lib/strategyArchive';

// ── Fixtures: the shapes the backend really produces ────────────────────────────────────

/** `strategy_lifecycle.binding_report`, as the archive gate emits it. */
const bindingReport = (overrides = {}) => ({
  deployment_id: 'dep-1',
  strategy_id: 's-1',
  version_id: 'ver-1',
  version: '1.2',
  binding_state: 'RUNNING',
  binding_states: ['DEPLOYING', 'RUNNING', 'PAUSED', 'STOPPED', 'FAILED'],
  status_raw: 'running',
  mode: 'live',
  available_actions: ['pause', 'stop'],
  reason: null,
  started_at: '2024-01-01T00:00:00+00:00',
  stopped_at: null,
  source: 'strategy_deployments',
  ...overrides,
});

const BLOCKED_MESSAGE =
  'This strategy has 2 active deployment(s), so it cannot be archived. Stop every one of ' +
  'them first; the strategy, its versions, its backtests, its deployments and its signals ' +
  'are unchanged.';

/**
 * The 409 as `ApiError` carries it: `.status` the code, `.data` the normalised body,
 * `.message` the server's own wording (which is what `apiClient`'s interceptor lifts).
 */
const blockedError = (blocking = [bindingReport(), bindingReport({ deployment_id: 'dep-2', binding_state: 'PAUSED', status_raw: 'paused', mode: 'paper', version: '1.1' })]) => {
  const raw = {
    error: ARCHIVE_BLOCKED_CODE,
    message: BLOCKED_MESSAGE,
    strategy_id: 's-1',
    blocking_deployments: blocking,
    blocking_states: ['DEPLOYING', 'RUNNING', 'PAUSED'],
  };
  return Object.assign(new Error(BLOCKED_MESSAGE), {
    status: 409,
    data: {
      error: raw.error,
      message: raw.message,
      detail: raw,
      status_code: 409,
      timestamp: '2024-01-01T00:00:00+00:00',
      path: '/api/strategies/s-1',
      details: {
        strategy_id: 's-1',
        blocking_deployments: blocking,
        blocking_states: raw.blocking_states,
      },
      solution: null,
    },
  });
};

// ══════════════════════════════════════════════════════════════════════════════════════
// The refusal, read off the response
// ══════════════════════════════════════════════════════════════════════════════════════

describe('describeArchiveFailure', () => {
  it('classifies the 409 as blocked and keeps the server\'s own wording', () => {
    const described = describeArchiveFailure(blockedError());

    expect(described.status).toBe(409);
    expect(described.code).toBe(ARCHIVE_BLOCKED_CODE);
    expect(described.blocked).toBe(true);
    // Requirement 2.10 is already stated by the server; the page must not paraphrase it.
    expect(described.message).toBe(BLOCKED_MESSAGE);
  });

  it('names every blocking deployment by identifier and current state', () => {
    const { blockingDeployments } = describeArchiveFailure(blockedError());

    expect(blockingDeployments).toHaveLength(2);
    expect(blockingDeployments.map((d) => [d.deploymentId, d.state])).toEqual([
      ['dep-1', 'RUNNING'],
      ['dep-2', 'PAUSED'],
    ]);
  });

  it('reads the list whether it arrives under details, detail, or the top level', () => {
    const blocking = [bindingReport()];
    const shapes = [
      { details: { blocking_deployments: blocking } },
      { detail: { blocking_deployments: blocking } },
      { blocking_deployments: blocking },
    ];

    for (const data of shapes) {
      expect(blockingDeploymentsFrom({ status: 409, data })).toHaveLength(1);
    }
  });

  it('reads a raw axios rejection as well as an ApiError', () => {
    const axiosStyle = {
      message: BLOCKED_MESSAGE,
      response: { status: 409, data: blockedError().data },
    };

    const described = describeArchiveFailure(axiosStyle);
    expect(described.status).toBe(409);
    expect(described.blocked).toBe(true);
    expect(described.blockingDeployments).toHaveLength(2);
  });

  it('is not "blocked" for the refusals a user cannot resolve by stopping something', () => {
    const unavailable = Object.assign(new Error('Apply 005a_strategy_archive.sql.'), {
      status: 503,
      data: {
        error: 'STRATEGY_ARCHIVE_UNAVAILABLE',
        message: 'Apply 005a_strategy_archive.sql.',
        details: { strategy_id: 's-1', migration: '005a' },
      },
    });

    const described = describeArchiveFailure(unavailable);
    expect(described.blocked).toBe(false);
    expect(described.blockingDeployments).toEqual([]);
    expect(described.message).toBe('Apply 005a_strategy_archive.sql.');
  });

  it('states the stop-them-first reason even for a 409 that arrived without prose', () => {
    const terse = { status: 409, data: { details: { blocking_deployments: [bindingReport()] } } };

    const described = describeArchiveFailure(terse);
    expect(described.blocked).toBe(true);
    expect(described.message).toBe(ARCHIVE_BLOCKED_FALLBACK_MESSAGE);
    expect(described.message).toMatch(/stop every deployment/i);
  });

  it('never reports an unreadable failure as a success or invents a blocker', () => {
    for (const nothing of [null, undefined, {}, 'boom', 0]) {
      const described = describeArchiveFailure(nothing);
      expect(described.blocked).toBe(false);
      expect(described.blockingDeployments).toEqual([]);
      expect(described.message).toBe(ARCHIVE_FAILED_FALLBACK_MESSAGE);
      expect(described.status).toBe(null);
    }
  });
});

describe('describeBlockingDeployment', () => {
  it('names the deployment, its state and its mode', () => {
    expect(describeBlockingDeployment(normalizeBlockingDeployment(bindingReport()))).toBe(
      'deployment dep-1 — RUNNING (live, v1.2)',
    );
  });

  it('accepts a raw report as well as a normalized one', () => {
    expect(describeBlockingDeployment(bindingReport({ mode: null, version: null }))).toBe(
      'deployment dep-1 — RUNNING',
    );
  });

  it('names the legacy running flag rather than a null deployment id', () => {
    // `load_blocking_deployments` reports `strategies.status = 'running'` as a blocker with
    // no `strategy_deployments` row behind it. "Stop deployment null" is not actionable.
    const own = bindingReport({
      deployment_id: null,
      version_id: null,
      version: null,
      mode: null,
      source: OWN_STATUS_SOURCE,
    });

    const line = describeBlockingDeployment(own);
    expect(line).toContain(OWN_STATUS_LABEL);
    expect(line).not.toContain('null');
    expect(line).toContain('RUNNING');
  });

  it('falls back to the raw status rather than claiming a state it could not read', () => {
    const unreadable = bindingReport({ binding_state: null, status_raw: 'weird-legacy-value' });
    expect(describeBlockingDeployment(unreadable)).toContain('weird-legacy-value');

    const blank = bindingReport({ binding_state: null, status_raw: null });
    expect(describeBlockingDeployment(blank)).toContain('state unreadable');
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// The confirmation (Requirements 2.8, 2.9, 3.2)
//
// `archiveConfirmMessage` — the one pre-joined string `window.confirm` could hold — was
// removed by vyomquant-ui-redesign task 10.3 along with the `window.confirm` call site
// itself. The same three statements are now the `ds/ConfirmDialog` title, `description` and
// review grid, so they are asserted against the rendered dialog in
// 'Strategies page archive action' below and in
// `src/pages/__tests__/Strategies.test.jsx` rather than against a string.
// ══════════════════════════════════════════════════════════════════════════════════════

// ══════════════════════════════════════════════════════════════════════════════════════
// The page
// ══════════════════════════════════════════════════════════════════════════════════════

const { mockStrategies, mockExchange } = vi.hoisted(() => ({
  mockStrategies: { list: vi.fn(), delete: vi.fn() },
  mockExchange: { list: vi.fn() },
}));

vi.mock('../../src/api', () => ({
  endpoints: { strategies: mockStrategies, exchange: mockExchange },
}));

// The builder is a heavy tree the archive path never reaches.
vi.mock('../../src/pages/StrategyBuilder', () => ({
  default: () => <div data-testid="builder-stub" />,
}));

import Strategies from '../../src/pages/Strategies';

const strategyRow = (overrides = {}) => ({
  id: 's-1',
  name: 'Momentum v2',
  symbol: 'BTCUSDT',
  status: 'running',
  timeframe: '1h',
  current_version: '1.2',
  ...overrides,
});

const renderPage = async () => {
  const view = render(
    <MemoryRouter>
      <Strategies />
    </MemoryRouter>,
  );
  await screen.findByText('Momentum v2');
  return view;
};

/** Open the archive confirmation. Task 10.3: this issues nothing on its own. */
const openArchiveDialog = async (user) => {
  await user.click(screen.getByRole('button', { name: /archive momentum v2/i }));
  return screen.findByRole('dialog');
};

/** Open it and take the explicit confirm action. */
const clickArchive = async (user) => {
  const dialog = await openArchiveDialog(user);
  await user.click(dialog.querySelector('[data-ds="confirm-dialog-confirm"]'));
};

/**
 * The strategy's entry in the list, as distinct from its name inside an open dialog.
 *
 * Under `window.confirm` a bare `screen.getByText('Momentum v2')` was unambiguous, because
 * the native dialog put nothing in the DOM — the only copy of the name on the page was the
 * row. Task 10.3's `ConfirmDialog` names the strategy the click was on in its review grid,
 * which is the entire point of a review grid, so while the dialog is open the name is
 * present twice and `getByText` throws on the ambiguity.
 *
 * Excluding the dialog's own copy keeps the claim the assertion was making — the strategy
 * is still listed behind the dialog, so nothing was removed before the user confirmed
 * (Requirement 2.8). Relaxing it to `getAllByText` would have been the smaller edit and the
 * wrong one: the review grid alone would satisfy it even if the row had gone.
 */
const rowOutsideDialog = (dialog) =>
  screen.getAllByText('Momentum v2').find((el) => !dialog.contains(el));

describe('Strategies page archive action', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockStrategies.list.mockResolvedValue([strategyRow()]);
    mockStrategies.delete.mockResolvedValue({
      status: 'archived',
      strategy_id: 's-1',
      archived_at: '2024-01-01T00:00:00+00:00',
    });
    mockExchange.list.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('asks for confirmation in archive language before issuing anything', async () => {
    const user = userEvent.setup();
    await renderPage();

    const dialog = await openArchiveDialog(user);

    // Requirement 7.6 / §8.4: a real dialog, in the destructive treatment.
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(dialog.getAttribute('data-ds-intent')).toBe('destructive');
    // The action, and its consequence in both directions (Requirements 2.8, 2.9, 3.2, 3.5).
    expect(dialog.textContent).toMatch(/archive strategy/i);
    expect(dialog.textContent).toMatch(/removes this strategy from your library list/i);
    expect(dialog.textContent).toMatch(/nothing is deleted/i);
    expect(dialog.textContent).not.toMatch(/delete this strategy/i);
    expect(dialog.textContent).not.toMatch(/will be deleted/i);
    // Requirement 2.10, stated up front rather than only on refusal.
    expect(dialog.textContent).toMatch(/deploying, running or paused/i);
    // The review grid names the strategy the click was on.
    expect(dialog.querySelector('[data-ds="confirm-dialog-review"]').textContent)
      .toContain('Momentum v2');
    // §8.4: archive is reversible, so there is no acknowledgement checkbox.
    expect(dialog.querySelector('[data-ds="confirm-dialog-acknowledgement"]')).toBeNull();
    expect(dialog.querySelector('input[type="checkbox"]')).toBeNull();

    // Requirement 2.8: no request until the user confirms.
    expect(mockStrategies.delete).not.toHaveBeenCalled();
    expect(rowOutsideDialog(dialog)).toBeTruthy();

    // Cancelling issues nothing either, and leaves the list as it was. The dialog is gone by
    // the assertion below, so the name is unambiguous again and `getByText` reads directly.
    await user.click(dialog.querySelector('[data-ds="confirm-dialog-cancel"]'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mockStrategies.delete).not.toHaveBeenCalled();
    expect(screen.getByText('Momentum v2')).toBeTruthy();
  });

  it('archives through the unchanged DELETE /api/strategies/{id} call site', async () => {
    const user = userEvent.setup();
    await renderPage();

    await clickArchive(user);

    await waitFor(() => expect(mockStrategies.delete).toHaveBeenCalledWith('s-1'));
    // Requirement 3.3: an archived strategy leaves the default list.
    await waitFor(() => expect(screen.queryByText('Momentum v2')).toBeNull());
    expect(screen.queryByTestId('archive-error')).toBeNull();
  });

  it('renders a 409 as the specific error naming each blocking deployment', async () => {
    const user = userEvent.setup();
    mockStrategies.delete.mockRejectedValue(blockedError());
    await renderPage();

    await clickArchive(user);

    const alert = await screen.findByTestId('archive-error');
    expect(alert.textContent).toContain('ARCHIVE BLOCKED');
    // Requirement 2.10: the reason, in the server's own words.
    expect(alert.textContent).toContain(BLOCKED_MESSAGE);

    // Requirement 3.1: each blocker, by identifier and current state.
    const items = screen
      .getByTestId('archive-blocking-deployments')
      .querySelectorAll('li');
    expect(Array.from(items).map((li) => li.textContent)).toEqual([
      'deployment dep-1 — RUNNING (live, v1.2)',
      'deployment dep-2 — PAUSED (paper, v1.1)',
    ]);

    // Requirement 3.1: nothing changed — the row is back.
    expect(screen.getByText('Momentum v2')).toBeTruthy();
  });

  it('renders a non-409 refusal without inventing blocking deployments', async () => {
    const user = userEvent.setup();
    mockStrategies.delete.mockRejectedValue(
      Object.assign(new Error('Apply 005a_strategy_archive.sql.'), {
        status: 503,
        data: { error: 'STRATEGY_ARCHIVE_UNAVAILABLE', message: 'Apply 005a_strategy_archive.sql.' },
      }),
    );
    await renderPage();

    await clickArchive(user);

    const alert = await screen.findByTestId('archive-error');
    expect(alert.textContent).toContain('ARCHIVE FAILED');
    expect(alert.textContent).toContain('Apply 005a_strategy_archive.sql.');
    expect(screen.queryByTestId('archive-blocking-deployments')).toBeNull();
    expect(screen.getByText('Momentum v2')).toBeTruthy();
  });

  it('clears the refusal when it is dismissed and when the next archive is attempted', async () => {
    const user = userEvent.setup();
    mockStrategies.delete.mockRejectedValueOnce(blockedError());
    await renderPage();

    await clickArchive(user);
    await screen.findByTestId('archive-error');

    await user.click(screen.getByRole('button', { name: /dismiss archive error/i }));
    expect(screen.queryByTestId('archive-error')).toBeNull();

    mockStrategies.delete.mockResolvedValueOnce({ status: 'archived', strategy_id: 's-1' });
    await clickArchive(user);
    await waitFor(() => expect(screen.queryByText('Momentum v2')).toBeNull());
    expect(screen.queryByTestId('archive-error')).toBeNull();
  });
});

describe('Strategies.jsx archive call site', () => {
  const source = readFileSync(
    path.resolve(__dirname, '../../src/pages/Strategies.jsx'),
    'utf-8',
  );

  it('still calls the unchanged delete endpoint through the shared endpoints module', () => {
    // Task 16.3: "the `DELETE /api/strategies/{id}` call site itself is UNCHANGED" — task
    // 5.1 rewired that route server-side, so a new client route here would be a second
    // opinion about which endpoint archives a strategy.
    expect(source).toContain('endpoints.strategies.delete(id)');
    expect(source).not.toMatch(/strategies\.archive\(/);
    expect(source).not.toMatch(/fetch\([^)]*\/archive/);
  });

  it('takes its refusal reading from the shared module', () => {
    expect(source).toMatch(/from ["']\.\.\/lib\/strategyArchive["']/);
    expect(source).toContain('describeArchiveFailure(err)');
  });

  it('no longer asks the user to confirm a deletion', () => {
    expect(source).not.toMatch(/confirm\(\s*["'][^"']*delete/i);
  });

  it('uses no native browser dialog at all (task 10.3, Requirements 18.3, 19.4)', () => {
    // `window.confirm` cannot be focus-trapped, styled or given a review grid, and
    // `window.prompt` can carry neither a label nor a validation message. Both call sites
    // are now `ds/ConfirmDialog` (design.md §1.8).
    //
    // This asks `findNativeDialogs` rather than grepping the file text, and the difference
    // is not cosmetic. The three assertions this replaced were
    // `expect(source).not.toMatch(/window\.confirm/)` and friends, over raw source — and
    // they failed on the very commit that fixed the defect. Task 10.3 left six docblocks
    // in `Strategies.jsx` recording which native call each dialog replaced and why it had
    // to go; the raw-text rule matched that prose. Zero real calls, red test, and the only
    // ways to green were to delete the documentation of a closed defect or to loosen the
    // rule. The detector strips comments and masks string literals first, then looks for a
    // *call* (or an alias) rather than a bare identifier, so prose is free and the rule is
    // strictly stronger than the one it replaces: `const ask = window.confirm; ask(m)` is
    // caught, and so is a bare `confirm(m)` with no receiver, neither of which the old
    // pattern saw. `tests/unit/guards/native-dialogs.js` carries the full method note; the
    // controls below show the detector still fires on a real call.
    expect(
      describeNativeDialogs(source),
      'Native browser dialogs in Strategies.jsx. Requirement 19.4 and design.md §1.8 '
        + 'forbid them on in-scope pages — route the interaction through '
        + '`components/ds/ConfirmDialog` instead:',
    ).toEqual([]);
    expect(source).toMatch(/from ["']\.\.\/components\/ds\/ConfirmDialog["']/);
  });
});

// ═════════════════════════════════════════════════════════════════════════════════════════

describe('controls: the assertions above can fail', () => {
  it('the native-dialog detector flags a real call, an alias and a bare global', () => {
    // Non-vacuity for the assertion above. A rule that has never been seen to fail is a
    // rule nobody has a reason to trust — and this one is a text scan over a file that is
    // *supposed* to be clean, so a pattern that silently stopped matching would look
    // exactly like success.
    const broken = [
      'const ok = window.confirm("Archive this strategy?");',
      'const next = window.prompt("Enter new strategy name:", current);',
      'if (!ok) alert("cancelled");',
      'const ask = globalThis.confirm;',
    ].join('\n');

    expect(describeNativeDialogs(broken)).toEqual([
      'line 1: window.confirm',
      'line 2: window.prompt',
      'line 3: alert(',
      'line 4: globalThis.confirm',
    ]);
  });

  it('the detector is not simply always-true', () => {
    // The other half of the control: it has to stay silent on the shapes this page
    // actually contains, or the assertion above could only ever be satisfied by an empty
    // file. A mention in a comment or a string is documentation, not a dialog — which is
    // the whole reason the detector exists — and `onConfirm` / `promptUser` are ordinary
    // identifiers that a looser pattern would flag.
    expect(describeNativeDialogs('// `window.confirm` cannot be focus-trapped')).toEqual([]);
    expect(describeNativeDialogs('/* was window.prompt, now a labelled Field */')).toEqual([]);
    expect(describeNativeDialogs("throw new Error('window.confirm is gone');")).toEqual([]);
    expect(describeNativeDialogs('<ConfirmDialog onConfirm={handleConfirmArchive} />')).toEqual([]);
    expect(describeNativeDialogs('promptUser(name); row.confirm(id);')).toEqual([]);
  });
});
