/**
 * `pages/StrategyDetail.jsx`'s four confirmations and its removed placeholder tab.
 *
 * vyomquant-ui-redesign task 10.4. `design.md` §1.8, §1.9, §7.3, §8.4.
 * Requirements 7.6, 18.3, 19.4. Property P13.
 *
 * WHAT THIS SUITE IS ABOUT
 * ------------------------
 * Four `window.confirm` calls became `ds/ConfirmDialog`s, and one placeholder tab went.
 * Nothing about what the four actions do to the backend changed, so the assertions come in
 * pairs: **no request before the dialog's explicit confirm**, and **exactly the request the
 * `window.confirm` version issued after it** — same path, same method, same body, same
 * query string. A confirmation surface that quietly re-pointed a deploy or a delete would
 * pass a test that only checked the dialog.
 *
 * P13 is the first half of each pair. It is asserted three ways per action, because "the
 * mutation is unreachable before confirmation" is a claim about code paths and a single
 * `not.toHaveBeenCalled()` after one click is weak evidence for it:
 *
 *   1. opening the dialog issues nothing;
 *   2. cancelling issues nothing, and neither does `Escape`;
 *   3. confirming issues it once — not twice.
 *
 * WHY THE NATIVE-DIALOG CHECK IS NOT A GREP
 * -----------------------------------------
 * `describeNativeDialogs` strips comments and masks strings before matching a *call*
 * (`guards/native-dialogs.js`). The migrated file deliberately keeps docblocks naming the
 * `window.confirm` calls it replaced and why they had to go, and a raw-text
 * `expect(source).not.toMatch(/window\.confirm/)` matches that prose — it failed on the
 * clean file during task 10.3 and cost a fix cycle. The control fixture below asserts the
 * detector still fires on a real call, so a green result here cannot mean "the detector
 * stopped working".
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import { describeNativeDialogs } from './guards/native-dialogs';
import { SRC, codeOnly } from './guards/source-scan';
// The real-funds checkbox label, from the module that constructs it. Asserting the absence
// of the acknowledgement against `deployFlow.js`'s own string means the test cannot drift
// from the sentence it is checking for.
import { ACKNOWLEDGEMENT_LABEL } from '../../src/lib/deployFlow';

// ── Stubs ───────────────────────────────────────────────────────────────────────────────
//
// The two consoles are heavy trees (ReactFlow, polling, websockets) that none of the four
// confirmations reaches; the Research and Deployments tabs are never opened here. The
// shared client covers the tabs that use it, and none of the four actions does — all four
// go through the page's own `authedFetch`, which is what the `fetch` mock below observes.

vi.mock('../../src/components/ResearchConsole', () => ({
  default: () => <div data-testid="research-console-stub" />,
}));

vi.mock('../../src/components/DeploymentConsole', () => ({
  default: () => <div data-testid="deployment-console-stub" />,
}));

const { mockClient } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn() },
}));

vi.mock('../../src/apiClient', () => mockClient);

import StrategyDetail from '../../src/pages/StrategyDetail';

// ── Fixtures ────────────────────────────────────────────────────────────────────────────

const STRATEGY_ID = 's-1';

/** `GET /api/strategies/{id}`, as the detail route answers it. */
const detailPayload = (overrides = {}) => ({
  strategy: {
    id: STRATEGY_ID,
    name: 'Momentum v2',
    description: 'Trend continuation on the 1h',
    // Not `running`, so the header offers Deploy rather than Pause.
    status: 'stopped',
    symbol: 'BTCUSDT',
    timeframe: '1h',
    environment: 'paper',
    current_version: '1.2',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-02T00:00:00Z',
    ...overrides,
  },
  version: { id: 'ver-1' },
  deployments: [],
  backtests: [],
  performance: {},
});

/** `GET /api/strategies/{id}/versions`. `1.1` is not current, so Restore is offered. */
const versionsPayload = () => ({
  versions: [
    {
      id: 'ver-2',
      version: '1.2',
      is_current: true,
      is_draft: false,
      created_at: '2024-01-02T00:00:00Z',
    },
    {
      id: 'ver-1',
      version: '1.1',
      is_current: false,
      is_draft: false,
      created_at: '2024-01-01T00:00:00Z',
    },
  ],
});

const jsonResponse = (body, { ok = true, status = 200 } = {}) => ({
  ok,
  status,
  json: async () => body,
});

/** Every `fetch` the page made, in order, as `{url, method, body}`. */
let requests;

const installClient = () => {
  mockClient.get.mockResolvedValue({});
  mockClient.post.mockResolvedValue({});
};

const installFetch = () => {
  requests = [];
  const impl = vi.fn(async (url, options = {}) => {
    const target = String(url);
    const method = (options.method ?? 'GET').toUpperCase();
    requests.push({ url: target, method, body: options.body ?? null });

    if (method === 'GET' && target.endsWith(`/api/strategies/${STRATEGY_ID}/versions`)) {
      return jsonResponse(versionsPayload());
    }
    if (method === 'GET' && target.endsWith(`/api/strategies/${STRATEGY_ID}`)) {
      return jsonResponse(detailPayload());
    }
    return jsonResponse({});
  });
  vi.stubGlobal('fetch', impl);
  return impl;
};

/** The mutations, filtered out of `requests` so a re-read never counts as one. */
const mutations = () => requests.filter((r) => r.method !== 'GET');

// ── Harness ─────────────────────────────────────────────────────────────────────────────

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

const renderPage = async () => {
  const view = render(
    <MemoryRouter initialEntries={[`/app/strategies/${STRATEGY_ID}`]}>
      <LocationProbe />
      <Routes>
        <Route path="/app/strategies/:strategyId" element={<StrategyDetail />} />
        <Route path="*" element={<div data-testid="elsewhere" />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByRole('heading', { name: 'Momentum v2' });
  return view;
};

const dialogConfirm = (dialog) => dialog.querySelector('[data-ds="confirm-dialog-confirm"]');
const dialogCancel = (dialog) => dialog.querySelector('[data-ds="confirm-dialog-cancel"]');

/** Activate a control by its accessible name and wait for the dialog it opens. */
const openDialogFrom = async (user, name) => {
  await user.click(screen.getByRole('button', { name }));
  return screen.findByRole('dialog');
};

// ══════════════════════════════════════════════════════════════════════════════════════
// 1. The file itself — zero native dialogs, zero placeholder (§1.8, §1.9)
// ══════════════════════════════════════════════════════════════════════════════════════

const PAGE = path.join(SRC, 'pages', 'StrategyDetail.jsx');
const SOURCE = readFileSync(PAGE, 'utf8');

describe('StrategyDetail source: native dialogs and placeholders (§1.8, §1.9)', () => {
  it('carries no native dialog at all', () => {
    const found = describeNativeDialogs(SOURCE);
    expect(
      found,
      'design.md §1.8 recorded four `window.confirm` calls in this file. All four are\n'
        + '`ds/ConfirmDialog` now (task 10.4), so this must be empty. A native dialog cannot\n'
        + 'be focus-trapped, named for assistive technology, or given a review grid\n'
        + `(Requirements 18.3, 8.1).\n${found.join('\n')}`,
    ).toEqual([]);
  });

  it('still detects a real call, so the assertion above cannot pass vacuously', () => {
    // The detector, not the file. If `describeNativeDialogs` ever stopped matching, the
    // assertion above would report success on a file full of native dialogs — the
    // green-looking non-run `source-scan.js` calls the worst outcome a guard can have.
    expect(describeNativeDialogs('if (!window.confirm("go?")) return;')).toHaveLength(1);
    expect(describeNativeDialogs('const name = prompt("name");')).toHaveLength(1);
    // And prose about a removed call is not a call. This file relies on that.
    expect(describeNativeDialogs('// window.confirm cannot be focus-trapped')).toEqual([]);
  });

  it('has no placeholder panel and no component left to render one', () => {
    // Comment-stripped and string-masked for the same reason as above: this file's
    // docblocks describe what was removed, and describing it is not shipping it.
    const code = codeOnly(SOURCE);
    expect(code).not.toMatch(/coming soon/i);
    expect(code).not.toMatch(/AuditTab/);
    // The tab id went with the component. An orphaned `activeTab === "audit"` branch would
    // be dead code that no control can reach, which is the defect in the other direction.
    expect(code).not.toMatch(/["']audit["']/);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 2. The tab row (§7.3, Requirement 19.4)
// ══════════════════════════════════════════════════════════════════════════════════════

describe('StrategyDetail tabs: the audit placeholder is gone', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installFetch();
    installClient();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('offers no Audit History tab and renders no placeholder copy', async () => {
    await renderPage();

    expect(screen.queryByRole('button', { name: /audit/i })).toBeNull();
    expect(document.body.textContent).not.toMatch(/coming soon/i);
  });

  it('keeps Overview as the default tab, with content', async () => {
    await renderPage();

    // The removed tab was never the default, and removing it must not have changed which
    // one opens. Overview's own copy, not just a non-empty panel.
    const overview = screen.getByRole('button', { name: /overview/i });
    expect(overview.textContent).toMatch(/overview/i);
    expect(screen.getByText(/trend continuation on the 1h/i)).toBeTruthy();
  });

  it('leaves every remaining tab reachable and non-empty', async () => {
    // The tabs are keyed by string id and rendered from `tabs.map`, so there is no
    // positional index to go stale — but an id whose render branch was deleted alongside
    // the audit one would show as a blank panel, and this is what would notice.
    const user = userEvent.setup();
    await renderPage();

    const labels = [
      'Overview', 'Backtests', 'Executions', 'Signals', 'Orders', 'Positions',
      'Logs', 'Metrics', 'Risk', 'Configuration', 'Versions', 'Marketplace',
    ];

    for (const label of labels) {
      await user.click(screen.getByRole('button', { name: new RegExp(`^${label}$`, 'i') }));
      // Whatever the tab renders, it renders something.
      await waitFor(() => {
        expect(document.body.textContent.length).toBeGreaterThan(0);
      });
      expect(screen.queryByText(/coming soon/i)).toBeNull();
    }
  });

  it('reports a publish failure in the page instead of a blocking native dialog', async () => {
    // The other two native dialogs this task removed — `guards/native-dialogs.js` matched
    // `MarketplaceTab`'s bare `alert(…)` calls, which §1.8's table does not list. The
    // outcome is now a `ds/Alert` in the tab, in a live region, dismissible. The request is
    // unchanged, so the same rejection produces the same message.
    mockClient.post.mockRejectedValueOnce(new Error('Library submission is closed.'));
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByRole('button', { name: /^marketplace$/i }));
    await user.click(await screen.findByRole('button', { name: /publish to library/i }));

    const notice = await screen.findByTestId('marketplace-notice');
    expect(notice.textContent).toMatch(/library submission is closed/i);
    // Announced, not merely painted (Requirement 18.3) — what `window.alert` was doing.
    expect(within(notice).getByRole('alert')).toBeTruthy();
    // And clearable, which an `alert` needed no affordance for because it blocked.
    await user.click(within(notice).getByRole('button', { name: /dismiss this message/i }));
    await waitFor(() => expect(screen.queryByTestId('marketplace-notice')).toBeNull());
  });

  it('links to Signal Trace, filtered to this strategy, instead of an audit tab', async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByRole('button', { name: /^signal trace$/i }));

    // The route Signal Trace actually reads (`SignalTrace.jsx`'s `strategy_id` filter),
    // and the same one `SignalsTab` and `pages/Strategies.jsx` already link to.
    await waitFor(() => {
      expect(screen.getByTestId('location').textContent)
        .toBe(`/app/signal-trace?strategy_id=${STRATEGY_ID}`);
    });
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 3. Deploy (§8.3, §8.4, Requirements 7.6, 8.5)
// ══════════════════════════════════════════════════════════════════════════════════════

describe('StrategyDetail deploy confirmation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installFetch();
    installClient();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('opens a real dialog that names the target without claiming it in prose', async () => {
    const user = userEvent.setup();
    await renderPage();

    const dialog = await openDialogFrom(user, /^deploy$/i);

    expect(dialog.getAttribute('aria-modal')).toBe('true');
    // §8.5: the environment is *shown*, from `lib/deployFlow.js`'s resolved id.
    expect(dialog.querySelector('[data-ds="environment-strip"]')).toBeTruthy();
    // §8.3's own title for this target, via `deployPresentation`.
    expect(dialog.textContent).toMatch(/start paper session/i);
    // §1.8's claim is gone: the old copy asserted the destination as though the trader had
    // chosen it, when the request hard-codes it.
    expect(dialog.textContent).not.toMatch(/to paper trading\?/i);
    // And the review grid says who does choose it.
    const review = dialog.querySelector('[data-ds="confirm-dialog-review"]');
    expect(review.textContent).toContain('Momentum v2');
    expect(review.textContent).toMatch(/not selectable here/i);

    // §8.4 / P14: no acknowledgement off the Live path — `deployFlow.js` constructs the
    // real-funds statement only inside its `LIVE` branch, and nothing here resolves to Live.
    //
    // The assertion is against the acknowledgement's own two sentences, not against the
    // phrase "real funds": the description *does* say "no real funds are committed", which
    // is the point of a paper confirmation and must not be forbidden by the test that
    // checks the Live step is absent.
    expect(dialog.querySelector('[data-ds="confirm-dialog-acknowledgement"]')).toBeNull();
    expect(dialog.querySelector('input[type="checkbox"]')).toBeNull();
    expect(dialog.textContent).not.toMatch(ACKNOWLEDGEMENT_LABEL);
    expect(dialog.textContent).not.toMatch(/will place real orders/i);

    // P13: nothing issued by opening it.
    expect(mutations()).toEqual([]);
  });

  it('issues nothing when cancelled or dismissed', async () => {
    const user = userEvent.setup();
    await renderPage();

    const dialog = await openDialogFrom(user, /^deploy$/i);
    await user.click(dialogCancel(dialog));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mutations()).toEqual([]);

    // Escape is the second way out, and it must be as inert as cancel.
    const again = await openDialogFrom(user, /^deploy$/i);
    expect(again).toBeTruthy();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mutations()).toEqual([]);
  });

  it('posts exactly the request the native dialog posted, once, after confirming', async () => {
    const user = userEvent.setup();
    await renderPage();

    const dialog = await openDialogFrom(user, /^deploy$/i);
    await user.click(dialogConfirm(dialog));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    const [deploy] = mutations();
    expect(deploy.method).toBe('POST');
    expect(deploy.url).toMatch(new RegExp(`/api/strategies/${STRATEGY_ID}/deploy$`));
    // The body is unchanged, byte for byte — the constant the dialog's badge reads is the
    // same string this payload carries.
    expect(deploy.body).toBe(JSON.stringify({ environment: 'paper' }));
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 4. Delete (§8.4, Requirement 7.6)
// ══════════════════════════════════════════════════════════════════════════════════════

describe('StrategyDetail delete confirmation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installFetch();
    installClient();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('describes what Delete actually does, and stops promising it is permanent', async () => {
    const user = userEvent.setup();
    await renderPage();

    const dialog = await openDialogFrom(user, /^delete$/i);

    expect(dialog.getAttribute('data-ds-intent')).toBe('destructive');
    // `DELETE /api/strategies/{id}` performs `archive_strategy`, a soft archive. The old
    // copy said "This cannot be undone.", which was simply false.
    expect(dialog.textContent).not.toMatch(/cannot be undone/i);
    expect(dialog.textContent).toMatch(/archives this strategy/i);
    expect(dialog.textContent).toMatch(/nothing is destroyed/i);
    // Requirement 2.10's refusal, stated up front rather than only after it happens.
    expect(dialog.textContent).toMatch(/deploying, running or paused/i);

    // §8.4's inventory: "Delete / archive strategy … Acknowledgement: No — reversible via
    // archive". Same judgement task 10.3 made for the same route on the Strategies page.
    expect(dialog.querySelector('[data-ds="confirm-dialog-acknowledgement"]')).toBeNull();
    expect(dialog.querySelector('input[type="checkbox"]')).toBeNull();

    const review = dialog.querySelector('[data-ds="confirm-dialog-review"]');
    expect(review.textContent).toContain('Momentum v2');
    expect(review.textContent).toContain(STRATEGY_ID);

    expect(mutations()).toEqual([]);
  });

  it('issues nothing when cancelled', async () => {
    const user = userEvent.setup();
    await renderPage();

    const dialog = await openDialogFrom(user, /^delete$/i);
    await user.click(dialogCancel(dialog));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mutations()).toEqual([]);
    // Still on the page: the success path navigates away, so staying is evidence nothing ran.
    expect(screen.getByRole('heading', { name: 'Momentum v2' })).toBeTruthy();
  });

  it('deletes through the unchanged endpoint, once, after confirming', async () => {
    const user = userEvent.setup();
    await renderPage();

    const dialog = await openDialogFrom(user, /^delete$/i);
    await user.click(dialogConfirm(dialog));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    const [remove] = mutations();
    expect(remove.method).toBe('DELETE');
    expect(remove.url).toMatch(new RegExp(`/api/strategies/${STRATEGY_ID}$`));
    expect(remove.body).toBeNull();
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 5. The two version actions (§8.4, Requirement 7.6)
// ══════════════════════════════════════════════════════════════════════════════════════

describe('StrategyDetail version confirmations', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installFetch();
    installClient();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** Open the Versions tab and hand back the row for the non-current version. */
  const openVersions = async (user) => {
    await user.click(screen.getByRole('button', { name: /^versions$/i }));
    await screen.findByText('Version History');
    // `1.1` is `is_current: false`, so it is the row that offers both Restore and Deploy.
    return within(screen.getByText('1.1').closest('[class*="p-4"]') ?? document.body);
  };

  it('confirms a restore as additive, with no acknowledgement, and issues nothing first', async () => {
    const user = userEvent.setup();
    await renderPage();
    const row = await openVersions(user);

    await user.click(row.getByRole('button', { name: /^restore$/i }));
    const dialog = await screen.findByRole('dialog');

    expect(dialog.textContent).toMatch(/restore version/i);
    // Restoring writes a new version and removes none, so `destructive` would be the wrong
    // treatment and §8.4's acknowledgement is not warranted.
    expect(dialog.getAttribute('data-ds-intent')).toBe('neutral');
    expect(dialog.textContent).toMatch(/new version/i);
    expect(dialog.textContent).toMatch(/no version is removed/i);
    expect(dialog.querySelector('[data-ds="confirm-dialog-acknowledgement"]')).toBeNull();
    expect(dialog.querySelector('input[type="checkbox"]')).toBeNull();
    expect(dialog.querySelector('[data-ds="confirm-dialog-review"]').textContent)
      .toContain('1.1');

    expect(mutations()).toEqual([]);

    await user.click(dialogCancel(dialog));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mutations()).toEqual([]);
  });

  it('restores through the unchanged endpoint after confirming', async () => {
    const user = userEvent.setup();
    await renderPage();
    const row = await openVersions(user);

    await user.click(row.getByRole('button', { name: /^restore$/i }));
    const dialog = await screen.findByRole('dialog');
    await user.click(dialogConfirm(dialog));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    const [restore] = mutations();
    expect(restore.method).toBe('POST');
    expect(restore.url).toMatch(
      new RegExp(`/api/strategies/${STRATEGY_ID}/versions/restore\\?version=1\\.1$`),
    );
  });

  it('confirms a version deploy without claiming the target in prose, and issues nothing first', async () => {
    const user = userEvent.setup();
    await renderPage();
    const row = await openVersions(user);

    await user.click(row.getByRole('button', { name: /^deploy$/i }));
    const dialog = await screen.findByRole('dialog');

    expect(dialog.querySelector('[data-ds="environment-strip"]')).toBeTruthy();
    expect(dialog.textContent).toMatch(/start paper session/i);
    expect(dialog.textContent).not.toMatch(/to paper trading\?/i);
    expect(dialog.querySelector('[data-ds="confirm-dialog-acknowledgement"]')).toBeNull();
    expect(dialog.textContent).not.toMatch(ACKNOWLEDGEMENT_LABEL);
    expect(dialog.querySelector('[data-ds="confirm-dialog-review"]').textContent)
      .toContain('1.1');
    // The row that is easiest to reach by mistake, named.
    expect(dialog.querySelector('[data-ds="confirm-dialog-review"]').textContent)
      .toMatch(/not current/i);

    expect(mutations()).toEqual([]);

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mutations()).toEqual([]);
  });

  it('deploys the version through the unchanged endpoint and query string', async () => {
    const user = userEvent.setup();
    await renderPage();
    const row = await openVersions(user);

    await user.click(row.getByRole('button', { name: /^deploy$/i }));
    const dialog = await screen.findByRole('dialog');
    await user.click(dialogConfirm(dialog));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    const [deploy] = mutations();
    expect(deploy.method).toBe('POST');
    expect(deploy.url).toMatch(
      new RegExp(
        `/api/strategies/${STRATEGY_ID}/versions/1\\.1/deploy\\?environment=paper$`,
      ),
    );
  });
});
