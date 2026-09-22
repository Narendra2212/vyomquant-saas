/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/design/pageFields.js — per-page field availability, as data
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 13.3. design.md §7's per-page tables, §10.1's stage table,
 * §18's `Reported<T>`. Requirements 14.5, 19.2, 19.3. Property P5.
 *
 * §7 audits every column and figure the requirements name against the backend that
 * actually serves it, and gives each one a verdict: ✅ available, ⚠️ derived, 🔶 backend
 * change, ❌ not available. That audit lives in prose, which means a page can drift from it
 * silently — the way `Portfolio.jsx` drifted into a hardcoded `100000/0` summary and
 * `Strategies.jsx` into `row.health ?? "healthy"` for a field `GET /api/strategies` does
 * not report at all. This module is the same audit as data, so the drift is assertable.
 *
 * WHAT AN ENTRY IS FOR
 * --------------------
 * One entry is one figure on one page: where its value comes from, whether the server can
 * report nothing for it, and — when it can — the sentence a trader reads instead of the
 * figure. That last part is the whole point of Requirement 19.3: "not available" with no
 * reason tells a trader exactly as much as a blank cell.
 *
 * HOW A PAGE CONSUMES IT
 * ----------------------
 * **Not by importing a renderer from here.** This module holds no React, no formatting and
 * no colour; it is read by view-model builders and by tests. A page's builder walks the
 * entries for its own page, reads each `path` off the response it already fetched, and
 * hands the result to `design/reported.fromNullable` with the entry's own `reason`:
 *
 *     import { fromNullable } from '../design/reported';
 *     import { PAGE_FIELDS_BY_PAGE } from '../design/pageFields';
 *
 *     // `read` walks a dotted path; a page helper, not this module's business.
 *     const model = {};
 *     for (const entry of PAGE_FIELDS_BY_PAGE.dashboard) {
 *       model[entry.field] = entry.verdict === VERDICT.AVAILABLE
 *         ? fromNullable(read(body, entry.path), entry.reason)
 *         : unavailable(entry.reason);          // ⚠️ derived → the page's own derivation
 *     }
 *
 * `ds/Metric` then takes `model.currentDrawdown` — a `Reported<T>` — and renders either the
 * figure or the not-available marker carrying `reason`. A page that forgets an entry has no
 * value to render at all, rather than a plausible `0`.
 *
 * **Nothing is wired in this change.** M7 and M8 rebuild the pages and consume this then.
 * §14.3 is explicit that each page is restructured once, with correct availability states
 * already declared, rather than restructured and then corrected — so building the
 * declaration and adopting it in the same change is precisely the sequence to avoid.
 *
 * DATA, NOT FUNCTIONS
 * -------------------
 * Every export below is a frozen value. Property 5 (task 13.4) has to iterate every entry
 * and assert that a failed read renders no figure and no zero; it can only do that if the
 * declaration is inert and enumerable rather than a set of accessor calls whose behaviour
 * depends on when they are called.
 *
 * WHAT IS *NOT* DECLARED HERE, AND WHY
 * ------------------------------------
 * §7.3 (Strategy Detail), §7.8 (Paper Trading) and §7.9 (Marketplace) have no per-field
 * verdict table. §7.8's figures already travel `pages/paperTradingFormat.js`, which has its
 * own eight-state panel model and per-figure simulated labelling; §7.9's table is the 7→4
 * subscription-state collapse, which is a mapping and not an availability question. Adding
 * entries for them would mean naming source paths §7 never audited, and a guessed path is
 * the fabrication this module exists to prevent. The pages that own them (tasks 25.x, 26.x)
 * verify their reads against real responses.
 */

/**
 * §7's verdict vocabulary, minus 🔶.
 *
 * 🔶 "backend change" is absent because all six registered changes (BC-1…BC-6) landed in
 * task 12, so no field is waiting on one. Every 🔶 row in §7 is now `available` and carries
 * its `backendChange` tag, which is what makes the register auditable after the fact
 * (Requirement 19.2).
 */
export const VERDICT = Object.freeze({
  /** ✅ A real backend field carries the meaning the requirement asks for. */
  AVAILABLE: 'available',
  /** ⚠️ Computable in the frontend from real fields. The `derivation` states how. */
  DERIVED: 'derived',
  /** ❌ Nothing reports it. `reason` is rendered on the not-available marker. */
  UNAVAILABLE: 'unavailable',
});

/**
 * Why a value can be missing. The axis that separates "wait for it" from "it is not coming".
 *
 * This is the distinction task 13.3 turns on. A page renders the same marker in every case,
 * but a release note, an operator and a future task need to tell them apart:
 *
 *   * `never`             — the server always reports a value. No marker is reachable.
 *   * `unmeasurable`      — the server reports `null` when it genuinely cannot measure or
 *                           read the figure. Permanent: no backend change removes it,
 *                           because the absence is a fact about the account or the venue,
 *                           not about the code. Exchange API latency and a spot position's
 *                           liquidation price are the two §7 names.
 *   * `unreported`        — nothing anywhere in the backend carries it. Permanent for the
 *                           same reason: §16 registered no change, so a page must render a
 *                           marker rather than wait.
 *   * `retention`         — the record existed and has aged out of a store's retention
 *                           window (`dag_nodes`, ~1h). Permanent per signal, and the reason
 *                           has to say so, or an older trace looks broken.
 *   * `pending-migration` — the ONLY non-permanent case. The response field exists and its
 *                           producer is deployed; the value is `null` until an operator
 *                           applies the named migration by hand. Distinguished from the four
 *                           above because it resolves without a code change, and a release
 *                           note has to say which lever an operator pulls.
 */
export const ABSENCE = Object.freeze({
  NEVER: 'never',
  UNMEASURABLE: 'unmeasurable',
  UNREPORTED: 'unreported',
  RETENTION: 'retention',
  PENDING_MIGRATION: 'pending-migration',
});

/**
 * The absence kinds no backend change will resolve.
 *
 * Everything not in this set (i.e. `pending-migration`) is a deployment step away from
 * carrying a value, and the entry names the migration.
 */
export const PERMANENT_ABSENCES = Object.freeze([
  ABSENCE.UNMEASURABLE,
  ABSENCE.UNREPORTED,
  ABSENCE.RETENTION,
]);

/**
 * The pages with a per-field verdict table in §7 / §10.1, plus `download`.
 *
 * `DOWNLOAD` is not one of §7's pages and carries no figure. It is here because
 * production-launch-hardening task 4.3 withdrew four advertised installers, and the thing
 * being withdrawn is an artifact rather than a number — see the DOWNLOAD section below for
 * why that belongs in this declaration rather than in a flag beside the page.
 */
export const PAGES = Object.freeze({
  DASHBOARD: 'dashboard',
  STRATEGIES: 'strategies',
  BACKTESTER: 'backtester',
  LIVE_TRADING: 'live-trading',
  PORTFOLIO: 'portfolio',
  TRADE_HISTORY: 'trade-history',
  SIGNAL_TRACE: 'signal-trace',
  DOWNLOAD: 'download',
  RISK_SETTINGS: 'risk-settings',
});

/** Migration 015, named so a warning and this declaration spell it the same way. */
export const LAST_SIGNAL_MIGRATION = 'backend_app/migrations/015_strategy_last_signal_at.sql';

/**
 * BC-1's deprecated predecessor. Declared so a test can assert no entry reads it.
 *
 * `risk.current_drawdown_pct` publishes `today_return_pct`, so a profitable day renders as
 * a positive "drawdown" — the figure a trader reads to decide whether to cut size. BC-1
 * left it in place for its deprecation window rather than repointing existing consumers
 * (Requirement 19.1), which means the wrong field is still on the wire and the only thing
 * keeping it off a page is this declaration plus the test that checks it.
 */
export const DEPRECATED_PATHS = Object.freeze(['risk.current_drawdown_pct']);

/*
 * ── The entry shape ───────────────────────────────────────────────────────
 *
 * A local builder, not an export: it exists so sixty declarations do not each repeat
 * sixteen keys, and so a key added below cannot be missing from an older entry. What leaves
 * this module is frozen data only.
 *
 *   page             The `PAGES` value. One entry belongs to one page's view model.
 *   field            The camelCase key the page's view model uses. Unique within a page.
 *   label            The copy §7 names for the figure. Rendered; not derived from `field`.
 *   requirement      The requirement that NAMES this field, as a string ('3.1'). One only:
 *                    the one that puts it on this page.
 *   read             The frontend call that fetches it, so a builder knows which response
 *                    `path` is relative to.
 *   endpoint         The HTTP method and path `read` reaches.
 *   path             The dotted path on THAT response, `[]` marking a list element.
 *                    Non-null exactly when `verdict === 'available'` — a derived or
 *                    unavailable figure is by definition not at a path.
 *   inputs           Additional real paths the figure needs: the extra keys an available
 *                    figure reads, or the operands a derivation consumes.
 *   verdict          `VERDICT`.
 *   absence          `ABSENCE`. Why the value can be missing.
 *   reason           The sentence rendered INSTEAD of the figure. Non-empty whenever the
 *                    marker is reachable — i.e. always, except `absence: 'never'`.
 *   backendChange    'BC-1'…'BC-6' when §16 registered a change for this field, so the
 *                    register stays auditable after the changes landed (Requirement 19.2).
 *   pendingMigration The migration an operator must apply before the field carries a value.
 *                    Set only with `absence: 'pending-migration'`.
 *   derivation       How a ⚠️ figure is computed from `inputs`. Set only when derived.
 *   tooltip          Copy that must accompany the figure, because the label alone would
 *                    misstate what it is.
 *   documentedIn     The frontend module whose JSDoc documents this response shape, when
 *                    one does. A test reads the file and looks for the leaf field name.
 *   note             Anything a page would otherwise have to rediscover from the backend.
 */
const entry = ({
  page,
  field,
  label,
  requirement,
  read,
  endpoint,
  path = null,
  inputs = [],
  verdict = VERDICT.AVAILABLE,
  absence = ABSENCE.NEVER,
  reason = null,
  backendChange = null,
  pendingMigration = null,
  derivation = null,
  tooltip = null,
  documentedIn = null,
  note = null,
}) => Object.freeze({
  page,
  field,
  label,
  requirement,
  read,
  endpoint,
  path,
  inputs: Object.freeze([...inputs]),
  verdict,
  absence,
  reason,
  backendChange,
  pendingMigration,
  derivation,
  tooltip,
  documentedIn,
  note,
});

/* The two reads every dashboard-backed page shares (§7.1, §7.5, §7.6). */
const DASHBOARD_READ = 'dashboardApi.getDashboard';
const DASHBOARD_ENDPOINT = 'GET /api/dashboard';
const DASHBOARD_MODULE = 'src/api/modules/dashboard.js';

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * §7.1 Dashboard (Requirement 3) — /app/dashboard
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * One aggregated read that 503s on failure, so there is one failure state for the page and
 * no panel falls back to a cached value (Requirement 14.5). `/api/dashboard/overview` is
 * not read from anywhere in the frontend — task 13.2 removed the method (§1.6, §7.1).
 */
const DASHBOARD_FIELDS = [
  entry({
    page: PAGES.DASHBOARD,
    field: 'portfolioValue',
    label: 'Portfolio value',
    requirement: '3.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'overview.total_value',
    inputs: ['overview.total_equity', 'overview.currency'],
    documentedIn: DASHBOARD_MODULE,
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'todayPnl',
    label: "Today's P&L",
    requirement: '3.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'overview.today_pnl',
    documentedIn: DASHBOARD_MODULE,
    note: '`today_realized_pnl + unrealized_pnl`, computed server-side. Do not recompute it '
      + 'from the two parts: two definitions of one figure drift.',
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'totalPnl',
    label: 'Total P&L',
    requirement: '3.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'overview.cumulative_pnl',
    documentedIn: DASHBOARD_MODULE,
    note: 'Lifetime realised PLUS the mark-to-market on positions still open. It is not a '
      + 'realised figure and must not be labelled one — see the portfolio page\'s '
      + '`lifetimeRealizedPnl`, which reads BC-5\'s `overview.realized_pnl`.',
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'currentDrawdown',
    label: 'Current drawdown',
    requirement: '3.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'risk.current_drawdown_pct_v2',
    verdict: VERDICT.AVAILABLE,
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No drawdown can be measured yet — that needs an equity history with at least '
      + 'two readable points and a positive peak.',
    backendChange: 'BC-1',
    documentedIn: DASHBOARD_MODULE,
    note: 'Peak-to-trough as a percentage of the peak, over the equity series this same '
      + 'response carries. `null` — never 0.0 — when the series is absent, one point long, '
      + 'partly unreadable, or peaks at or below zero. `0.0` means the account is AT its '
      + 'peak, which is a reading and renders as a figure. The deprecated '
      + '`risk.current_drawdown_pct` beside it publishes `today_return_pct` and must not be '
      + 'read by any page.',
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'activeStrategyCount',
    label: 'Active strategies',
    requirement: '3.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'strategies.active',
    inputs: ['strategies.total', 'strategies.paused', 'strategies.items[].status'],
    documentedIn: DASHBOARD_MODULE,
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'openPositions',
    label: 'Open positions',
    requirement: '3.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'positions',
    inputs: ['degraded'],
    documentedIn: DASHBOARD_MODULE,
    note: 'A NormalizedPosition list: `symbol side contracts entry_price mark_price notional '
      + 'leverage unrealized_pnl unrealized_pnl_pct liquidation_price margin margin_type '
      + 'exchange_id environment timestamp`. `[]` means BOTH "no open positions" and "the '
      + 'read failed" — `degraded` is the only thing that tells them apart, so it must be '
      + 'consulted before an empty state renders (BC-2, Requirement 14.5).',
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'positionsDegraded',
    label: 'Positions read state',
    requirement: '14.5',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'degraded',
    backendChange: 'BC-2',
    documentedIn: DASHBOARD_MODULE,
    note: '`null` when every read succeeded — that is a healthy reading, NOT an absent value, '
      + 'and it must not render a not-available marker. Otherwise `{positions: "unreadable", '
      + 'environment, reason}`, and the page renders `Panel state="error"` with the server\'s '
      + 'own `reason` verbatim rather than an empty table or a client-composed sentence.',
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'openPositionsCount',
    label: 'Open position count',
    requirement: '3.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'risk.open_positions_count',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'The open positions could not be read, so they could not be counted.',
    backendChange: 'BC-2',
    documentedIn: DASHBOARD_MODULE,
    note: '`null` when unreadable and never `0`: a count of zero is the safest-looking figure '
      + 'a broken positions read could publish. `risk.risk_score` is `null` alongside it.',
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'recentSignals',
    label: 'Recent signals',
    requirement: '3.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'recent_activity.signals',
    documentedIn: DASHBOARD_MODULE,
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'recentOrders',
    label: 'Recent orders',
    requirement: '3.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'executions',
    inputs: ['recent_activity.executions'],
    documentedIn: DASHBOARD_MODULE,
    note: 'The top-level `executions` and `recent_activity.executions` are the same list. '
      + 'Read one of them, not both.',
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'exchangeHealth',
    label: 'Exchange health',
    requirement: '3.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'exchange.exchanges',
    inputs: ['exchange.connected_exchanges', 'exchange.can_trade', 'exchange.total_exchanges'],
    documentedIn: DASHBOARD_MODULE,
    note: 'Each item is `{exchange_id, status, latency_ms, last_sync}` built from the caller\'s '
      + '`exchange_keys` rows. TWO of those four are constants in the aggregation service — '
      + '`status` is always "connected" and `latency_ms` is always 35 — so neither is a '
      + 'measurement. Render the venue and `last_sync`; take latency from '
      + '`health.exchange_api_latency_ms`, which is nullable and honest.',
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'exchangeApiLatencyMs',
    label: 'Exchange API latency',
    requirement: '3.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'health.exchange_api_latency_ms',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'Exchange API latency has not been measured.',
    documentedIn: DASHBOARD_MODULE,
    note: 'Genuinely `null` when unmeasured, with `health.exchange_api_latency_status` reading '
      + '"unavailable" beside it. It must never render as "0 ms" — a zero-latency exchange '
      + 'call is not a thing, so the figure would be read as a claim about a working '
      + 'connection (Requirement 14.5).',
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'orderStateSync',
    label: 'Order-state sync',
    requirement: '3.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'health.order_state_sync_status',
    documentedIn: DASHBOARD_MODULE,
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'equityCurve',
    label: 'Equity curve',
    requirement: '3.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'equity_curve',
    documentedIn: DASHBOARD_MODULE,
    note: 'Ascending time order as read. Do not re-sort: the drawdown of a resorted series is '
      + 'the drawdown of a different series, and `currentDrawdown` is computed from this one.',
  }),
  entry({
    page: PAGES.DASHBOARD,
    field: 'alertCondition',
    label: 'Alert strip',
    requirement: '3.3',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    verdict: VERDICT.DERIVED,
    inputs: [
      'exchange.exchanges[].status',
      'strategies.items[].status',
      'executions[].status',
    ],
    derivation: 'The disjunction over three real field sets: any `exchange.exchanges[].status` '
      + 'not connected, OR any `strategies.items[].status` in an error or stopped-on-error '
      + 'state, OR any `executions[].status` failed or rejected. No fourth input, and no '
      + 'client-invented severity — the strip names which of the three fired.',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No exchange, strategy or execution state was reported, so no alert condition '
      + 'could be evaluated.',
    tooltip: 'Derived from reported exchange, strategy and execution states.',
    documentedIn: DASHBOARD_MODULE,
    note: '`exchange.exchanges[].status` is a constant "connected" in the aggregation service '
      + 'today, so the exchange arm of the disjunction cannot fire from this read. The other '
      + 'two arms carry real, varying values. The strip must therefore not claim "all '
      + 'exchanges connected" as a positive assertion — it reports the conditions it found.',
  }),
];

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * §7.2 Strategies (Requirement 4.1) — /app/strategies
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `GET /api/strategies` answers `{strategies, total, include_archived, archived_total}`. Each
 * row is `routers/strategies._LIST_COLUMNS` narrowed to the columns the table actually has —
 * `id name description symbol timeframe status is_active deployed_exchange created_at
 * updated_at buy_logic tags version` — plus the lifted `_dag_*` keys, `is_archived`,
 * `archived_at`, and BC-3's / BC-4's two timestamps.
 *
 * THREE OF §7.2's SOURCE NAMES ARE NOT ON THAT ROW. `row.current_version` is spelled
 * `version`, `row.exchange_status` is spelled `deployed_exchange`, and `row.most_recent_
 * deployment`, `row.pnl`, `row.win_rate`, `row.max_dd` and `row.health` are on no strategy
 * projection at all. Each is recorded below as it really is, because a page pointed at a key
 * the response does not carry renders `undefined` — or, as `Strategies.jsx` did with
 * `row.health ?? "healthy"`, a cheerful default for a field nothing reports.
 *
 * Requirement 4.2/4.3's actions are not declared here: they are rendered by mapping
 * `entry.allowed_actions` from `api.library.myStrategies()` through `ACTION_CATALOG`, which
 * §7.2 keeps verbatim and properties P6/P7 cover. They are a capability list, not a figure.
 */
const STRATEGIES_READ = 'strategiesApi.list';
const STRATEGIES_ENDPOINT = 'GET /api/strategies';
const STRATEGIES_MODULE = 'src/api/modules/strategies.js';

const STRATEGIES_FIELDS = [
  entry({
    page: PAGES.STRATEGIES,
    field: 'name',
    label: 'Name',
    requirement: '4.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    path: 'strategies[].name',
    documentedIn: STRATEGIES_MODULE,
  }),
  entry({
    page: PAGES.STRATEGIES,
    field: 'status',
    label: 'Status',
    requirement: '4.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    path: 'strategies[].status',
    inputs: ['strategies[].is_archived'],
    documentedIn: STRATEGIES_MODULE,
    note: 'Rendered through `StrategyStatus`. An archived row is history, not an actionable '
      + 'status, so `is_archived` is read beside it.',
  }),
  entry({
    page: PAGES.STRATEGIES,
    field: 'version',
    label: 'Version',
    requirement: '4.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    path: 'strategies[].version',
    documentedIn: STRATEGIES_MODULE,
    note: '§7.2 names this `row.current_version`. The list projection spells it `version`; '
      + 'there is no `current_version` key on the response.',
  }),
  entry({
    page: PAGES.STRATEGIES,
    field: 'market',
    label: 'Market / exchange',
    requirement: '4.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    path: 'strategies[].symbol',
    inputs: ['strategies[].timeframe', 'strategies[].deployed_exchange'],
    documentedIn: STRATEGIES_MODULE,
    note: '§7.2 names `row.exchange_status` for the venue. The projection carries '
      + '`deployed_exchange` — the venue a deployment targeted — and no per-row exchange '
      + 'health. Exchange health is a page-level figure from `GET /api/dashboard`.',
  }),
  entry({
    page: PAGES.STRATEGIES,
    field: 'deploymentState',
    label: 'Deployment state',
    requirement: '4.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    path: 'strategies[].is_active',
    inputs: ['strategies[].status'],
    documentedIn: STRATEGIES_MODULE,
    note: '§7.2 names `row.is_running` and `row.most_recent_deployment`. Neither is on the '
      + 'list projection: `is_active` is the row\'s own deployment flag, and the deployment '
      + 'RECORD is a separate read (`GET /api/strategies/{id}/deployments`) the list page does '
      + 'not make. So this column reports whether the strategy is marked active, and does not '
      + 'claim a live worker.',
  }),
  entry({
    page: PAGES.STRATEGIES,
    field: 'performance',
    label: 'Performance summary',
    requirement: '4.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'The strategies list does not report per-strategy performance. Open the strategy '
      + 'to see its backtest and deployment results.',
    documentedIn: STRATEGIES_MODULE,
    note: '§7.2 names `row.pnl`, `row.win_rate` and `row.max_dd` as ✅. None of the three is in '
      + '`_LIST_COLUMNS`, and no other key on the response carries them; §16 registered no '
      + 'change for them, so this is permanent until a listing widening is registered. The '
      + 'figures exist per BACKTEST (`GET /api/backtests`) and are declared on the backtester '
      + 'page. A zero here would read as a strategy that has never made money.',
  }),
  entry({
    page: PAGES.STRATEGIES,
    field: 'riskState',
    label: 'Risk state',
    requirement: '4.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Undetermined: this strategy has no deployment record and no backtest outcome on '
      + 'the listing, and those are the only two permitted sources.',
    derivation: '`lib/strategyHealth.computeStrategyHealth(row)` — Requirement 1.9\'s two '
      + 'permitted sources, `most_recent_deployment.status` then `most_recent_backtest.outcome`, '
      + 'and `undetermined` when neither decides.',
    documentedIn: STRATEGIES_MODULE,
    note: 'Declared unavailable rather than derived because both of the derivation\'s inputs '
      + 'are absent from the list projection, so it returns `undetermined` for EVERY row — '
      + 'which §7.2 itself says renders as not-available. `computeStrategyHealth` stays the '
      + 'only computation of it; the defect to guard against is the `row.health ?? "healthy"` '
      + 'default it replaced, for a `health` field the endpoint does not report.',
  }),
  entry({
    page: PAGES.STRATEGIES,
    field: 'lastSignalAt',
    label: 'Last signal',
    requirement: '4.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    path: 'strategies[].last_signal_at',
    absence: ABSENCE.PENDING_MIGRATION,
    reason: 'No signal time is recorded for this strategy yet.',
    backendChange: 'BC-3',
    pendingMigration: LAST_SIGNAL_MIGRATION,
    documentedIn: STRATEGIES_MODULE,
    note: 'The key is ALWAYS present and is `null` in three cases a page cannot tell apart, '
      + 'which is why the reason above claims nothing about the cause: the strategy has never '
      + 'signalled; or migration 015 has not been applied by hand yet, so the column does not '
      + 'exist and the producer (`backend/strategy_last_signal.py`) has nothing to write to. '
      + 'The backend logs a warning naming 015 in that case. THIS IS THE ONE ENTRY THAT IS '
      + 'NOT PERMANENTLY UNAVAILABLE — no code change is pending, an operator applying 015 '
      + 'is.',
  }),
  entry({
    page: PAGES.STRATEGIES,
    field: 'lastExecutionAt',
    label: 'Last execution',
    requirement: '4.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    path: 'strategies[].last_execution_at',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This strategy has no recorded execution.',
    backendChange: 'BC-4',
    documentedIn: STRATEGIES_MODULE,
    note: '`MAX(created_at)` over `execution_records` grouped by `strategy_id`, read once per '
      + 'page. Always present, `null` when the strategy has never executed and also when that '
      + 'one grouped read failed — the router logs a warning and reports `null` for every row '
      + 'rather than a guessed timestamp.',
  }),
  entry({
    page: PAGES.STRATEGIES,
    field: 'updatedAt',
    label: 'Updated',
    requirement: '4.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    path: 'strategies[].updated_at',
    documentedIn: STRATEGIES_MODULE,
  }),
  entry({
    page: PAGES.STRATEGIES,
    field: 'environment',
    label: 'Environment',
    requirement: '4.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'The strategies list does not report an execution environment. A strategy reaches '
      + 'paper or live through a deployment, and the deployment record is a separate read this '
      + 'page does not make.',
    documentedIn: STRATEGIES_MODULE,
    note: 'Not one of Requirement 4.1\'s ten columns — it is declared because §7.2\'s layout '
      + 'sketch draws an `[Environment ▾]` control in the filter row, and `_LIST_COLUMNS` '
      + 'carries no `environment` key for it to filter on. `Strategies.jsx` used to write '
      + '`row.environment ?? "paper"`, which labelled every strategy a paper strategy, and a '
      + 'select over that default would have been a control that either matches every row or '
      + 'none. So the control is NOT RENDERED and this reason is shown in its place — '
      + 'Requirement 19.3\'s rule applied to a control rather than to a figure. `deployed_'
      + 'exchange` is the nearest real key and it names a venue, not an environment.',
  }),
];

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * §7.4 Backtester (Requirement 6) — /app/backtest, /app/backtester
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `strategiesApi.executeBacktest` answers a `BacktestExecuteResponse`, and
 * `mapBacktestExecutionToUI` flattens its `results` object onto the shape the page renders —
 * renaming nothing and defaulting nothing. Paths below are on the raw response, so a
 * builder can read them before or after that mapping.
 *
 * Every metric is declared as absent-able even though a completed run reports all of them.
 * A run that failed part-way answers what it managed to compute, and §7.4 is explicit that
 * a failure renders the tier-1 figures NOT AT ALL rather than as zeros (Requirement 6.6).
 * Declaring them `never` would be the claim that a figure is always there to render.
 */
const BACKTEST_READ = 'strategiesApi.executeBacktest → mapBacktestExecutionToUI';
const BACKTEST_ENDPOINT = 'POST /api/strategy-operations/strategies/{strategyId}/backtests/execute';
const BACKTEST_MODULE = 'src/api/modules/strategies.js';

const BACKTESTER_FIELDS = [
  entry({
    page: PAGES.BACKTESTER,
    field: 'totalReturn',
    label: 'Total return',
    requirement: '6.2',
    read: BACKTEST_READ,
    endpoint: BACKTEST_ENDPOINT,
    path: 'results.total_return_pct',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This run did not report a total return.',
    documentedIn: BACKTEST_MODULE,
  }),
  entry({
    page: PAGES.BACKTESTER,
    field: 'netPnl',
    label: 'Net P&L',
    requirement: '6.2',
    read: BACKTEST_READ,
    endpoint: BACKTEST_ENDPOINT,
    path: 'results.total_pnl',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This run did not report a net P&L.',
    documentedIn: BACKTEST_MODULE,
  }),
  entry({
    page: PAGES.BACKTESTER,
    field: 'maxDrawdown',
    label: 'Max drawdown',
    requirement: '6.2',
    read: BACKTEST_READ,
    endpoint: BACKTEST_ENDPOINT,
    path: 'results.max_drawdown_pct',
    inputs: ['results.max_drawdown'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This run did not report a maximum drawdown.',
    documentedIn: BACKTEST_MODULE,
    note: 'The WORST historical decline of the run, which is a different quantity from the '
      + 'dashboard\'s `currentDrawdown` (the decline still outstanding now). The two are '
      + 'deliberately not shared.',
  }),
  entry({
    page: PAGES.BACKTESTER,
    field: 'sharpe',
    label: 'Sharpe',
    requirement: '6.2',
    read: BACKTEST_READ,
    endpoint: BACKTEST_ENDPOINT,
    path: 'results.sharpe_ratio',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This run did not report a Sharpe ratio.',
    documentedIn: BACKTEST_MODULE,
  }),
  entry({
    page: PAGES.BACKTESTER,
    field: 'winRate',
    label: 'Win rate',
    requirement: '6.2',
    read: BACKTEST_READ,
    endpoint: BACKTEST_ENDPOINT,
    path: 'results.win_rate_pct',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This run did not report a win rate.',
    documentedIn: BACKTEST_MODULE,
    note: 'A run with zero trades has no win rate. `0%` would read as "every trade lost".',
  }),
  entry({
    page: PAGES.BACKTESTER,
    field: 'tradeCount',
    label: 'Trades',
    requirement: '6.2',
    read: BACKTEST_READ,
    endpoint: BACKTEST_ENDPOINT,
    path: 'results.total_trades',
    inputs: ['results.trades_count'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This run did not report a trade count.',
    documentedIn: BACKTEST_MODULE,
    note: 'A reported `0` IS a reading here — a strategy that never triggered is a real '
      + 'outcome — and it is what makes the other five figures unavailable rather than zero.',
  }),
  entry({
    page: PAGES.BACKTESTER,
    field: 'equityCurve',
    label: 'Equity curve',
    requirement: '6.3',
    read: BACKTEST_READ,
    endpoint: BACKTEST_ENDPOINT,
    path: 'results.charts.equity_curve',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This run produced no equity series.',
    documentedIn: BACKTEST_MODULE,
    note: '`{values, timestamps}`, where `values` are either `{timestamp, equity}` records or '
      + 'bare numbers paired positionally with `timestamps`. '
      + '`equitySeriesFromBacktestResults` reads both forms and invents neither.',
  }),
  entry({
    page: PAGES.BACKTESTER,
    field: 'drawdownCurve',
    label: 'Drawdown curve',
    requirement: '6.3',
    read: BACKTEST_READ,
    endpoint: BACKTEST_ENDPOINT,
    verdict: VERDICT.DERIVED,
    inputs: ['results.charts.equity_curve'],
    derivation: 'Per point of the equity series: the running peak up to that point minus that '
      + 'point, as a percentage of the peak. Computed in `lib/` with a unit test, over a real '
      + 'series — the engine returns no drawdown series of its own.',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No drawdown curve can be drawn without an equity series.',
    tooltip: 'Derived from the equity curve: the decline from each running peak.',
    documentedIn: BACKTEST_MODULE,
  }),
  entry({
    page: PAGES.BACKTESTER,
    field: 'tradeList',
    label: 'Trades',
    requirement: '6.4',
    read: BACKTEST_READ,
    endpoint: BACKTEST_ENDPOINT,
    path: 'results.trades',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This run reported no trade list.',
    documentedIn: BACKTEST_MODULE,
    note: 'An empty list and an absent list are different: the first means the strategy never '
      + 'triggered (`EmptyState`), the second that the run did not report trades at all.',
  }),
  entry({
    page: PAGES.BACKTESTER,
    field: 'fees',
    label: 'Fees',
    requirement: '6.4',
    read: BACKTEST_READ,
    endpoint: BACKTEST_ENDPOINT,
    path: 'results.total_fees_paid',
    inputs: ['results.total_fees'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This run did not report fees.',
    documentedIn: BACKTEST_MODULE,
  }),
];

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * §7.5 Live Trading (Requirement 7) — /app/live-trading
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * §7.5's table sources five of its rows from `deployments.items[]` on the dashboard
 * response. THERE IS NO `deployments` SECTION ON `GET /api/dashboard`: the payload carries
 * `environment overview positions executions subscription usage strategies marketplace risk
 * notifications referrals health exchange recent_activity equity_curve degraded generated_at`,
 * and `usage.deployments` is a plan COUNT, not a list. The real per-deployment read is
 * `GET /api/strategies/{id}/deployments`, which is per strategy and carries
 * `{deployment_id, status, environment, worker, started_at, health}` — no exchange, no
 * account, no version, no market. Each affected row below says which of those it is.
 *
 * This page is the one where per-deployment attribution matters most and where the backend
 * offers least, so the ❌ that Requirement 14.5 turns on lives here: realised P&L is
 * account-wide, and labelling it per-deployment would be exactly the fabrication.
 */
const DEPLOYMENTS_READ = 'strategiesApi.listDeployments';
const DEPLOYMENTS_ENDPOINT = 'GET /api/strategies/{strategyId}/deployments';

const LIVE_TRADING_FIELDS = [
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'connectionState',
    label: 'Connection',
    requirement: '7.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'exchange.exchanges[].status',
    inputs: ['exchange.can_trade', 'exchange.connected_exchanges'],
    documentedIn: DASHBOARD_MODULE,
    note: 'The socket state from `useConnectionStatus()` is a fact about THIS BROWSER\'s '
      + 'connection and is reported separately — it is not evidence the exchange is reachable. '
      + 'And `exchange.exchanges[].status` is a constant "connected" in the aggregation '
      + 'service, so it confirms a key is configured, not that a venue answered.',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'exchange',
    label: 'Exchange',
    requirement: '7.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'exchange.exchanges[].exchange_id',
    inputs: ['positions[].exchange_id'],
    documentedIn: DASHBOARD_MODULE,
    note: '§7.5 names `deployments.items[].exchange_id`, which does not exist. The venue is '
      + 'reported per configured key and per open position; neither is per deployment.',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'account',
    label: 'Account',
    requirement: '7.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'No read on this page reports which exchange account is trading. Check the '
      + 'account in Exchange Manager.',
    documentedIn: DASHBOARD_MODULE,
    note: '§7.5 sketches a masked account label ("…4821"). Nothing reports one: '
      + '`exchange.exchanges[]` carries `{exchange_id, status, latency_ms, last_sync}` — the '
      + 'venue, not the account — and §16 registered no change for it. A masked number '
      + 'assembled client-side would be an invented identifier for a real-money account.',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'deployment',
    label: 'Deployment',
    requirement: '7.1',
    read: DEPLOYMENTS_READ,
    endpoint: DEPLOYMENTS_ENDPOINT,
    path: 'deployments',
    inputs: ['deployments[].status', 'deployments[].worker', 'deployments[].started_at'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No deployment record was returned for this strategy.',
    documentedIn: STRATEGIES_MODULE,
    note: 'PER STRATEGY, not account-wide: this endpoint answers for one strategy id, so the '
      + 'deployment selector §7.5 draws is the union over the strategies the page already '
      + 'lists. There is no single read that returns every deployment a trader has.',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'strategy',
    label: 'Strategy (version)',
    requirement: '7.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    path: 'strategies[].name',
    inputs: ['strategies[].version', 'strategies[].id'],
    documentedIn: STRATEGIES_MODULE,
    note: 'Joined to the selected deployment by strategy id. The deployment record itself '
      + 'names no strategy or version, so the pair shown is the strategy\'s CURRENT version — '
      + 'which is not necessarily the version the running deployment was bound to. Do not '
      + 'label it "deployed version".',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'market',
    label: 'Market',
    requirement: '7.1',
    read: STRATEGIES_READ,
    endpoint: STRATEGIES_ENDPOINT,
    path: 'strategies[].symbol',
    inputs: ['positions[].symbol'],
    documentedIn: STRATEGIES_MODULE,
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'tradingEnvironment',
    label: 'Environment',
    requirement: '7.4',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'positions[].environment',
    inputs: ['environment', 'deployments[].environment', 'executions[].environment'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'The server did not label this record\'s environment.',
    documentedIn: DASHBOARD_MODULE,
    note: 'Resolved ONLY from a server field (§8.1). An absent label renders "ENVIRONMENT '
      + 'UNCONFIRMED", never a default: defaulting to LIVE is alarmist and defaulting to '
      + 'PAPER is dangerous.',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'position',
    label: 'Position',
    requirement: '7.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'positions[].contracts',
    inputs: ['positions[].side', 'positions[].entry_price', 'positions[].mark_price'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'The open positions could not be read.',
    documentedIn: DASHBOARD_MODULE,
    note: 'Gated on `degraded` exactly as the dashboard\'s `openPositions` is: `[]` alone does '
      + 'not mean flat.',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'unrealisedPnl',
    label: 'Unrealised P&L',
    requirement: '7.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'positions[].unrealized_pnl',
    inputs: ['positions[].unrealized_pnl_pct'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'The position\'s unrealised P&L was not reported.',
    documentedIn: DASHBOARD_MODULE,
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'realisedPnlAccount',
    label: 'Realised P&L (account, today)',
    requirement: '7.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'overview.today_realized_pnl',
    documentedIn: DASHBOARD_MODULE,
    tooltip: 'Every closed trade on this account since 00:00 UTC, across all deployments.',
    note: 'THE LABEL IS PART OF THE DECLARATION. The figure is account-wide and today-only; '
      + 'rendering it beside a selected deployment under the label "Realised P&L" would read '
      + 'as that deployment\'s figure. It is shown as "Realised P&L (account, today)" and the '
      + 'per-deployment figure gets its own not-available marker — see '
      + '`realisedPnlPerDeployment`.',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'realisedPnlPerDeployment',
    label: 'Realised P&L (this deployment)',
    requirement: '7.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Realised P&L is reported for the whole account, not per deployment. The '
      + 'account-wide figure for today is shown above.',
    documentedIn: DASHBOARD_MODULE,
    note: 'The §7 ❌ row with no registered backend change, and the reason this page renders '
      + 'TWO things where §7.5\'s sketch shows one: the account-wide figure under its explicit '
      + 'label, plus this marker. `overview.today_realized_pnl` and BC-5\'s '
      + '`overview.realized_pnl` are both account-wide sums; neither is attributable to a '
      + 'deployment, and attributing one would be the fabrication Requirement 14.5 forbids.',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'exposure',
    label: 'Exposure',
    requirement: '7.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'positions[].notional',
    inputs: ['overview.total_exposure'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'The position\'s notional exposure was not reported.',
    documentedIn: DASHBOARD_MODULE,
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'riskState',
    label: 'Risk',
    requirement: '7.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'risk.risk_level',
    inputs: ['risk.daily_loss_utilized', 'risk.max_daily_loss', 'risk.circuit_breaker_armed'],
    documentedIn: DASHBOARD_MODULE,
    note: 'Account-wide, like realised P&L. `risk.risk_score` is `null` whenever '
      + '`risk.open_positions_count` is (BC-2).',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'liquidationDistance',
    label: 'Liquidation distance',
    requirement: '7.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    verdict: VERDICT.DERIVED,
    inputs: ['positions[].liquidation_price', 'positions[].mark_price'],
    derivation: '`computeLiquidationDistance(mark_price, liquidation_price)` — the existing '
      + 'computation in `Dashboard.jsx`, over two reported prices.',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This position has no liquidation price — spot positions cannot be liquidated.',
    tooltip: 'Distance from the mark price to the reported liquidation price.',
    documentedIn: DASHBOARD_MODULE,
    note: '`liquidation_price` is `null` for every paper position and for every spot position, '
      + 'and for a futures position the venue did not report one. That is a legitimate '
      + 'permanent absence, not a failed read, and the reason has to say which — a blank cell '
      + 'beside a leveraged position reads as "no liquidation risk".',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'latestSignal',
    label: 'Latest signal',
    requirement: '7.3',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'recent_activity.signals',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No signal has been recorded for this account yet.',
    documentedIn: DASHBOARD_MODULE,
    note: 'The newest is `recent_activity.signals[0]`. Account-wide: filter by strategy id '
      + 'before attributing one to the selected deployment, and render the marker rather than '
      + 'the account\'s newest signal when the filter is empty.',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'latestOrder',
    label: 'Latest order',
    requirement: '7.3',
    read: 'ordersApi.getOpenOrders',
    endpoint: 'GET /api/orders/open',
    path: '[]',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'Open orders could not be read for this exchange.',
    documentedIn: 'src/api/modules/orders.js',
    note: 'THE RESPONSE ROOT IS THE ORDER ARRAY ITSELF (the venue\'s own ccxt shape via '
      + '`DataEngine.fetch_open_orders`), not an envelope — hence the `[]` path. Note the '
      + 'route requires an `exchange_id` QUERY PARAMETER, and `ordersApi.getOpenOrders(symbol)` '
      + 'sends only `symbol`, so the call as written today answers 422. Whichever task adopts '
      + 'this field passes the venue.',
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'executionStatus',
    label: 'Execution status',
    requirement: '7.3',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'executions[].status',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No execution has been recorded for this account yet.',
    documentedIn: DASHBOARD_MODULE,
  }),
  entry({
    page: PAGES.LIVE_TRADING,
    field: 'deploymentStopped',
    label: 'Deployment stop / error',
    requirement: '7.5',
    read: 'wsClient.subscribe(\'STRATEGY_STATUS\')',
    endpoint: 'WS STRATEGY_STATUS',
    path: 'status',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No strategy status has been pushed on this connection yet.',
    note: 'A PUSH, which is what makes Requirement 7.5\'s five-second bound hold with no '
      + 'polling. Absence of a frame is not evidence a deployment is healthy, so the tier-1 '
      + 'state stays whatever the last REST read reported.',
  }),
];

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * §7.6 Portfolio (Requirement 10) — /app/portfolio
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * The live positions read is re-pointed from the non-existent `/api/portfolio/positions*` to
 * `GET /api/dashboard` (task 13.1), which is the endpoint that actually serves positions to a
 * trader. `/summary`, `/equity-curve`, `/allocation` and `/heatmap` are kept as they are.
 *
 * `available_balance` is NOT on `/api/portfolio/summary` — that route returns only
 * `total_equity total_pnl pnl_pct total_exposure` — so it is read from the dashboard
 * response, and this declaration says which read each figure belongs to for exactly that
 * reason.
 */
const PORTFOLIO_MODULE = 'src/api/modules/portfolio.js';

const PORTFOLIO_FIELDS = [
  entry({
    page: PAGES.PORTFOLIO,
    field: 'totalValue',
    label: 'Total value',
    requirement: '10.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'overview.total_value',
    inputs: ['overview.total_equity'],
    documentedIn: DASHBOARD_MODULE,
    note: '`GET /api/portfolio/summary.total_equity` reports the same quantity. One read, one '
      + 'figure: taking it from the dashboard keeps this page\'s tier 1 consistent, since '
      + '`available_balance` and the drawdown are only there.',
  }),
  entry({
    page: PAGES.PORTFOLIO,
    field: 'availableBalance',
    label: 'Available',
    requirement: '10.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'overview.available_balance',
    inputs: ['overview.free_balance'],
    documentedIn: DASHBOARD_MODULE,
    note: 'Do NOT read this from `/api/portfolio/summary`: it is not there.',
  }),
  entry({
    page: PAGES.PORTFOLIO,
    field: 'investedCapital',
    label: 'Invested (capital in use)',
    requirement: '10.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    verdict: VERDICT.DERIVED,
    inputs: ['overview.used_balance', 'overview.total_equity', 'overview.free_balance'],
    derivation: '`overview.used_balance`, which the server computes as '
      + '`total_equity − free_balance` (live) or the account\'s locked balance (paper).',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'The balance breakdown was not reported, so capital in use cannot be shown.',
    tooltip: 'Capital in use: your total equity minus your free balance. There is no '
      + '"invested capital" figure on the account — this is derived from the balance '
      + 'breakdown.',
    documentedIn: DASHBOARD_MODULE,
    note: 'THE LABEL AND TOOLTIP ARE PART OF THE DECLARATION (§7.6). No backend field is '
      + 'named "invested capital"; labelling `used_balance` as one without saying so would '
      + 'misrepresent it, because margin locked against a losing position is "in use" without '
      + 'being "invested".',
  }),
  entry({
    page: PAGES.PORTFOLIO,
    field: 'unrealisedPnl',
    label: 'Unrealised P&L',
    requirement: '10.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'overview.unrealized_pnl',
    documentedIn: DASHBOARD_MODULE,
    note: 'Summed from the open positions\' own `unrealized_pnl`.',
  }),
  entry({
    page: PAGES.PORTFOLIO,
    field: 'realisedPnlToday',
    label: 'Realised P&L (today)',
    requirement: '10.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'overview.today_realized_pnl',
    documentedIn: DASHBOARD_MODULE,
    tooltip: 'Closed trades since 00:00 UTC.',
    note: 'Explicitly scoped in the label. It is today\'s window only, and it publishes `0.0` '
      + 'for an unreadable day sum — a pre-existing behaviour BC-5 deliberately left alone. '
      + 'The lifetime figure is `lifetimeRealizedPnl` below.',
  }),
  entry({
    page: PAGES.PORTFOLIO,
    field: 'lifetimeRealizedPnl',
    label: 'Realised P&L (lifetime)',
    requirement: '10.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'overview.realized_pnl',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'Lifetime realised P&L could not be read from the trade ledger.',
    backendChange: 'BC-5',
    documentedIn: DASHBOARD_MODULE,
    note: 'One sum over the fill ledger, distinct from BOTH of its neighbours: '
      + '`today_realized_pnl` is the same quantity in today\'s window only, and '
      + '`cumulative_pnl` is realised PLUS the mark-to-market on open positions, so calling '
      + 'that one "realised" would report unbanked money as banked. `null` — never 0.0 — when '
      + 'the executions read could not produce it.',
  }),
  entry({
    page: PAGES.PORTFOLIO,
    field: 'totalExposure',
    label: 'Total exposure',
    requirement: '10.1',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'overview.total_exposure',
    documentedIn: DASHBOARD_MODULE,
  }),
  entry({
    page: PAGES.PORTFOLIO,
    field: 'currentDrawdown',
    label: 'Current drawdown',
    requirement: '10.2',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'risk.current_drawdown_pct_v2',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No drawdown can be measured yet — that needs an equity history with at least '
      + 'two readable points and a positive peak.',
    backendChange: 'BC-1',
    documentedIn: DASHBOARD_MODULE,
    note: 'IN TIER 1, not below it (Requirement 10.2 reads with 10.1). Same field and same '
      + 'deprecated neighbour as the dashboard\'s `currentDrawdown`.',
  }),
  entry({
    page: PAGES.PORTFOLIO,
    field: 'openPositions',
    label: 'Open positions',
    requirement: '10.3',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'positions',
    inputs: ['degraded'],
    documentedIn: DASHBOARD_MODULE,
    note: 'Paper keeps `api.paper.getPositions()`. The live read is re-pointed here from '
      + '`api.portfolio.getOpenPositions()`/`getPositions()`, which both 404 — task 13.1 '
      + 'removed them. An unreadable read renders `Panel state="error"`, never an empty table '
      + '(Requirement 14.5), which is what `degraded` is read for.',
  }),
  entry({
    page: PAGES.PORTFOLIO,
    field: 'positionLiquidationPrice',
    label: 'Liq. price',
    requirement: '10.3',
    read: DASHBOARD_READ,
    endpoint: DASHBOARD_ENDPOINT,
    path: 'positions[].liquidation_price',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'Not applicable — spot positions have no liquidation price.',
    documentedIn: DASHBOARD_MODULE,
    note: 'A permanent `null` for spot and paper positions, and for any futures position the '
      + 'venue did not report one for. The positions table renders the marker in the cell '
      + 'rather than a dash that could be mistaken for zero.',
  }),
  entry({
    page: PAGES.PORTFOLIO,
    field: 'allocation',
    label: 'Allocation',
    requirement: '10.1',
    read: 'portfolioApi.getAllocation',
    endpoint: 'GET /api/portfolio/allocation',
    path: '[]',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'The allocation breakdown could not be read.',
    documentedIn: PORTFOLIO_MODULE,
    note: 'THE RESPONSE ROOT IS THE ARRAY — rows of `{asset, value_usd, pct}`, no envelope. '
      + 'The route answers `[]` both for an account with no allocation rows and for a query '
      + 'that returned no dataset, and it raises nothing in the second case, so an empty '
      + 'result cannot be read as "no allocation" with confidence.',
  }),
  entry({
    page: PAGES.PORTFOLIO,
    field: 'equityCurve',
    label: 'Equity curve',
    requirement: '10.1',
    read: 'portfolioApi.getEquityCurve',
    endpoint: 'GET /api/portfolio/equity-curve',
    path: '[]',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'The equity history could not be read.',
    documentedIn: PORTFOLIO_MODULE,
    note: 'THE RESPONSE ROOT IS THE ARRAY — rows of `{timestamp, equity}` in ascending time '
      + 'order, no envelope. This route DOES raise 503 `EQUITY_CURVE_FETCH_FAILED` on a read '
      + 'error, so `[]` from it means no history rather than a failure.',
  }),
  entry({
    page: PAGES.PORTFOLIO,
    field: 'heatmap',
    label: 'P&L heatmap',
    requirement: '10.1',
    read: 'portfolioApi.getHeatmap',
    endpoint: 'GET /api/portfolio/heatmap',
    path: '[]',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'The P&L heatmap could not be read.',
    documentedIn: PORTFOLIO_MODULE,
    note: 'THE RESPONSE ROOT IS THE ARRAY — rows of `{date, pnl_usd}`, one per day with a '
      + 'recorded execution, no envelope. A day with no executions has no row; it is not a '
      + 'zero-P&L day.',
  }),
];

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * §7.7 Trade History (Requirement 11.1) — /app/trades
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `GET /api/orders/history` RETURNS A BARE ARRAY — no envelope — of `executions` rows
 * (`SELECT *` from QuestDB), falling back to the venue's own ccxt trade shape when the
 * telemetry read fails and an `exchange_id` was supplied, and to `[]` when it was not.
 *
 * THE TELEMETRY ROW HAS SEVEN COLUMNS. `telemetry_engine` creates `executions` as
 * `(timestamp, user_id, symbol, side, status, amount, price)` and `log_execution` writes
 * exactly those — four tags and two fields. There is no `pnl`, `fee`, `slippage`,
 * `strategy_id`, `exit_price` or `exchange_id` on it. Today's page reads all of them anyway,
 * through alias chains ending in `?? 0` and `?? "Direct"`, which is how a ledger comes to
 * show `$0` fees and a strategy called "Direct" for every live trade. Those columns are
 * declared unavailable below, with the reason, which is the whole point of task 13.3.
 *
 * Verdicts are stated for the LIVE read, because that is the real-money case. Paper reads
 * `api.paper.getTrades(100)`, whose rows genuinely carry `realized_pnl`, `fee` and
 * `strategy_id`; each entry that differs says so, and the page's builder chooses per
 * environment.
 */
const HISTORY_READ = 'ordersApi.getHistory';
const HISTORY_ENDPOINT = 'GET /api/orders/history';
const ORDERS_MODULE = 'src/api/modules/orders.js';

const TRADE_HISTORY_FIELDS = [
  entry({
    page: PAGES.TRADE_HISTORY,
    field: 'time',
    label: 'Time',
    requirement: '11.1',
    read: HISTORY_READ,
    endpoint: HISTORY_ENDPOINT,
    path: '[].timestamp',
    inputs: ['[].executed_at'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This row carries no execution time.',
    documentedIn: ORDERS_MODULE,
    note: 'Paper rows use `executed_at`. Never substitute `new Date()` for a missing '
      + 'timestamp — today\'s page does, which stamps an undated fill with the moment the '
      + 'table rendered.',
  }),
  entry({
    page: PAGES.TRADE_HISTORY,
    field: 'market',
    label: 'Market',
    requirement: '11.1',
    read: HISTORY_READ,
    endpoint: HISTORY_ENDPOINT,
    path: '[].symbol',
    documentedIn: ORDERS_MODULE,
    note: 'Stored with `/` replaced by `_` on the telemetry path; display formatting is the '
      + 'page\'s job, not a second source.',
  }),
  entry({
    page: PAGES.TRADE_HISTORY,
    field: 'side',
    label: 'Side',
    requirement: '11.1',
    read: HISTORY_READ,
    endpoint: HISTORY_ENDPOINT,
    path: '[].side',
    documentedIn: ORDERS_MODULE,
  }),
  entry({
    page: PAGES.TRADE_HISTORY,
    field: 'quantity',
    label: 'Qty',
    requirement: '11.1',
    read: HISTORY_READ,
    endpoint: HISTORY_ENDPOINT,
    path: '[].amount',
    inputs: ['[].quantity'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This row carries no filled quantity.',
    documentedIn: ORDERS_MODULE,
  }),
  entry({
    page: PAGES.TRADE_HISTORY,
    field: 'price',
    label: 'Price',
    requirement: '11.1',
    read: HISTORY_READ,
    endpoint: HISTORY_ENDPOINT,
    path: '[].price',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This row carries no fill price.',
    documentedIn: ORDERS_MODULE,
    note: 'ONE price per row. Today\'s page renders an `Entry` and an `Exit` column and fills '
      + 'both from `row.price` when the aliases miss, so a single fill appears to have entered '
      + 'and exited at the same number. §7.7 drops to one price column.',
  }),
  entry({
    page: PAGES.TRADE_HISTORY,
    field: 'status',
    label: 'Status',
    requirement: '11.1',
    read: HISTORY_READ,
    endpoint: HISTORY_ENDPOINT,
    path: '[].status',
    inputs: ['[].order_lifecycle_state'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This row carries no status.',
    documentedIn: ORDERS_MODULE,
    note: 'The column §7.7 ADDS — Requirement 11.1 names it and today\'s twelve-column table '
      + 'omits it. It is a real telemetry column, written by `log_execution`.',
  }),
  entry({
    page: PAGES.TRADE_HISTORY,
    field: 'pnl',
    label: 'P&L',
    requirement: '11.1',
    read: HISTORY_READ,
    endpoint: HISTORY_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Per-trade P&L is not recorded for live executions. The account totals are on the '
      + 'Portfolio page.',
    documentedIn: ORDERS_MODULE,
    note: 'The `executions` telemetry row has no `pnl` column and `log_execution` writes none, '
      + 'so `row.pnl ?? row.profit_loss ?? row.realized_pnl` resolves to nothing on the live '
      + 'read. PAPER IS DIFFERENT: `api.paper.getTrades()` rows carry a real `realized_pnl`, '
      + 'and the paper column is available. §16 registered no change for the live side.',
  }),
  entry({
    page: PAGES.TRADE_HISTORY,
    field: 'fees',
    label: 'Fees',
    requirement: '11.1',
    read: HISTORY_READ,
    endpoint: HISTORY_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Fees are not recorded for live executions.',
    documentedIn: ORDERS_MODULE,
    note: 'Same seven-column row. Today\'s `toNumber(row.fees ?? row.fee ?? 0)` renders `$0` '
      + 'for every live trade, which is a claim that the venue charged nothing — the exact '
      + 'fabricated zero Requirement 14.5 is about. Paper rows carry `fee`.',
  }),
  entry({
    page: PAGES.TRADE_HISTORY,
    field: 'slippage',
    label: 'Slippage',
    requirement: '11.1',
    read: HISTORY_READ,
    endpoint: HISTORY_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Slippage is not recorded — it needs an intended price to compare the fill '
      + 'against, and no read reports one.',
    documentedIn: ORDERS_MODULE,
    note: 'Neither the telemetry row nor a paper trade row carries `slippage` or `slip`, and '
      + 'the fill price alone cannot produce it. `0%` reads as a perfect fill.',
  }),
  entry({
    page: PAGES.TRADE_HISTORY,
    field: 'strategy',
    label: 'Strategy',
    requirement: '11.1',
    read: HISTORY_READ,
    endpoint: HISTORY_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Live executions are not recorded against a strategy.',
    documentedIn: ORDERS_MODULE,
    note: 'No `strategy_id` on the telemetry row, so today\'s `?? "Direct"` labels every '
      + 'algorithmic fill as a manual one — while `routers/orders.py` refuses manual execution '
      + 'outright, so "Direct" cannot be true of any row. Paper rows carry `strategy_id`.',
  }),
  entry({
    page: PAGES.TRADE_HISTORY,
    field: 'environment',
    label: 'Environment',
    requirement: '11.1',
    read: 'paperApi.getTrades',
    endpoint: 'GET /api/paper/trades',
    path: 'execution_environment',
    inputs: ['is_simulated', 'trades[].execution_environment', 'trades[].is_simulated'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'The server did not label this ledger\'s environment.',
    note: 'On the ENVELOPE beside `trades` and `count`, and repeated on each row by '
      + '`PaperTradingService._paper_provenance`. Read the envelope; neither field is '
      + 'defaulted, and a body carrying neither renders "ENVIRONMENT UNCONFIRMED" (§8.1). The '
      + 'live read reports no environment label at all, which is why this entry names the '
      + 'paper read.',
  }),
];

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * §10.1 Signal Trace (Requirement 9.1) — /app/signal-trace/:signalId
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `GET /api/signal-trace/signals/{id}` answers `{signal, trace, lifecycle_transitions,
 * timeline, lifecycle_state_source, degraded}`. `trace` carries four labelled sections, each
 * tagged with its `source` (`signal_trace_engine` or `signals_row`); `timeline` carries SIX
 * event types since BC-6 appended `POSITION_UPDATED` to the original five.
 *
 * The nine stages are built from the canonical list OUTWARD and the payload attached to
 * them, never the reverse (§10.2). That is what makes Requirement 9.1's "in order" and
 * 9.2's "rather than omitting it" structural: an unknown event type in the payload cannot
 * add or remove a row. So every entry below exists whether or not the payload mentions it,
 * and `absence` is about what the ROW says, not whether the row renders.
 *
 * No frontend API module documents this response — `SignalTrace.jsx` calls `get()` directly —
 * so `documentedIn` is null throughout. Task 21.1 rebuilds the page.
 */
const TRACE_READ = 'get(`/api/signal-trace/signals/${signalId}`)';
const TRACE_ENDPOINT = 'GET /api/signal-trace/signals/{signalId}';

/*
 * The LIST read, which is a different response from the detail read above and is the one
 * §10.3's five-column table is built from. Declared separately for the reason `path` exists
 * at all: a column reading `signals[].symbol` is reading the list envelope, not the detail
 * body, and one `read` covering both would make every path below ambiguous.
 *
 * `signal_service.Signal.to_public_dict()` is the row shape. Two things about it that a
 * column would otherwise get wrong:
 *
 *   * There is NO strategy NAME anywhere on this response — only `strategy_id` and
 *     `strategy_version`. §10.3 draws a "Strategy" column; the honest rendering is the id.
 *   * `order_lifecycle_state` is the canonical column and `status` is the long-standing
 *     `public.signals.status` vocabulary the list has always carried. The Outcome column
 *     renders `status`, because that is the field the row reports its own outcome in;
 *     `order_lifecycle_state` is named as an input rather than rendered beside it, because
 *     two spellings of one fact in one cell reads as two facts.
 */
const LIST_READ = 'get(`/api/signal-trace/signals?${query}`)';
const LIST_ENDPOINT = 'GET /api/signal-trace/signals';

const SIGNAL_TRACE_FIELDS = [
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'listTime',
    label: 'Time',
    requirement: '9.3',
    read: LIST_READ,
    endpoint: LIST_ENDPOINT,
    path: 'signals[].generated_at',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This signal reports no generation time.',
    note: 'The list is sorted by this column descending, server-side, and the endpoint takes '
      + 'no sort parameter — so the table offers no sort headers. A client-side sort would '
      + 'reorder one page of a server-ordered set, which reads as a reordering of the whole.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'listStrategy',
    label: 'Strategy',
    requirement: '9.3',
    read: LIST_READ,
    endpoint: LIST_ENDPOINT,
    path: 'signals[].strategy_id',
    inputs: ['signals[].strategy_version'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This signal reports no strategy.',
    note: 'The id, not a name: `to_public_dict` carries no strategy name and this page issues '
      + 'no second read to resolve one. The version is rendered beside it where reported.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'listMarket',
    label: 'Market',
    requirement: '9.3',
    read: LIST_READ,
    endpoint: LIST_ENDPOINT,
    path: 'signals[].symbol',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This signal reports no market symbol.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'listDecision',
    label: 'Decision',
    requirement: '9.3',
    read: LIST_READ,
    endpoint: LIST_ENDPOINT,
    path: 'signals[].decision',
    inputs: ['signals[].side'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This signal reports no decision.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'listOutcome',
    label: 'Outcome',
    requirement: '9.3',
    read: LIST_READ,
    endpoint: LIST_ENDPOINT,
    path: 'signals[].status',
    inputs: ['signals[].order_lifecycle_state'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This signal reports no outcome.',
    note: 'The legacy `signals.status` vocabulary, rendered verbatim through `statusToken` so '
      + 'a value the frontend has never seen renders calmly rather than as a guess.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'stage1MarketData',
    label: '1 Market data',
    requirement: '9.1',
    read: TRACE_READ,
    endpoint: TRACE_ENDPOINT,
    path: 'signal.market_info',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This signal recorded no market data input.',
    note: 'A required field on `SignalCreateRequest`, so a signal that exists has one.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'stage2Indicators',
    label: '2 Indicators',
    requirement: '9.1',
    read: TRACE_READ,
    endpoint: TRACE_ENDPOINT,
    path: 'signal.indicators',
    inputs: ['trace.dag_nodes'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'This signal recorded no indicator values.',
    note: 'Required on the signal; `trace.dag_nodes` adds the per-node inputs and outputs for '
      + 'the expanded body, and ages out — see `stage4Logic`.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'stage3ModelOutput',
    label: '3 Model',
    requirement: '9.1',
    read: TRACE_READ,
    endpoint: TRACE_ENDPOINT,
    path: 'trace.ml_inference',
    inputs: ['trace.ml_inference.applicable'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No model inference was recorded for this signal.',
    note: '`applicable: false` — NOT `null` — when the strategy version has no ML node. That '
      + 'is a genuine "not applicable to this strategy" state, distinct from "pending", and it '
      + 'is the server saying so rather than the page inferring it from an absence.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'stage4Logic',
    label: '4 Logic',
    requirement: '9.1',
    read: TRACE_READ,
    endpoint: TRACE_ENDPOINT,
    verdict: VERDICT.DERIVED,
    inputs: ['trace.dag_nodes', 'signal.decision'],
    derivation: '`trace.dag_nodes` filtered to LOGIC-category nodes, plus `signal.decision` — '
      + 'real node records, no dedicated logic-evaluation record exists.',
    absence: ABSENCE.RETENTION,
    reason: 'The trace store keeps node-level detail for about an hour, and this signal is '
      + 'older than that, so its logic evaluation is no longer retained.',
    tooltip: 'Reconstructed from the recorded DAG nodes and the signal decision.',
    note: 'An empty `dag_nodes` renders NOT-AVAILABLE WITH THE RETENTION REASON, not pending: '
      + 'pending would claim the evaluation is still coming for a signal that has already '
      + 'executed. Permanent per signal — retention has passed and the record is not coming '
      + 'back.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'stage5Signal',
    label: '5 Signal',
    requirement: '9.1',
    read: TRACE_READ,
    endpoint: TRACE_ENDPOINT,
    path: 'signal.decision',
    inputs: ['timeline[].event=SIGNAL_GENERATED'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No decision was recorded for this signal.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'stage6OrderDecision',
    label: '6 Order decision',
    requirement: '9.1',
    read: TRACE_READ,
    endpoint: TRACE_ENDPOINT,
    path: 'trace.risk_validation',
    inputs: ['timeline[].event=RISK_EVALUATED', 'timeline[].event=ORDER_CREATED'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No risk evaluation or order creation was recorded for this signal.',
    note: '`risk_validation.blocked` is the `blocked` state, which is a RECORD OF A REJECTION '
      + 'and renders the server\'s reason — not an absence.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'stage7Submission',
    label: '7 Submission',
    requirement: '9.1',
    read: TRACE_READ,
    endpoint: TRACE_ENDPOINT,
    path: 'timeline[].event=EXCHANGE_RESPONSE',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No exchange response was recorded for this signal.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'stage8Execution',
    label: '8 Execution',
    requirement: '9.1',
    read: TRACE_READ,
    endpoint: TRACE_ENDPOINT,
    path: 'timeline[].event=EXECUTED',
    inputs: ['trace.execution'],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No execution was recorded for this signal.',
    note: 'Gated on the row\'s `executed_at`. A signal that never executed genuinely has no '
      + 'event here, which is `pending` — not not-available — when no later stage is blocked.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'stage9PositionUpdate',
    label: '9 Position update',
    requirement: '9.1',
    read: TRACE_READ,
    endpoint: TRACE_ENDPOINT,
    path: 'timeline[].event=POSITION_UPDATED',
    inputs: [
      'timeline[].data.symbol',
      'timeline[].data.direction',
      'timeline[].data.quantity_delta',
      'timeline[].data.average_price',
      'timeline[].data.trade_id',
      'timeline[].data.realized_pnl',
      'timeline[].data.not_available',
    ],
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No position change was recorded for this signal.',
    backendChange: 'BC-6',
    note: 'THE STAGE §10.1 REGISTERED AS THE ONE THING THE BACKEND DID NOT TRACK AT ALL. BC-6 '
      + 'now derives a sixth timeline event from the same `executed_at` column `EXECUTED` is '
      + 'gated on, so the two are present or absent TOGETHER and exactly one '
      + '`POSITION_UPDATED` exists per executed signal — a repeated execution write overwrites '
      + 'the column rather than appending an event. The event reports the position CHANGE. '
      + 'Fields the row did not report are named in `data.not_available` rather than '
      + 'defaulted, because a zero fill and an unreported fill are different facts about a '
      + 'position.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'resultingPosition',
    label: 'Resulting position',
    requirement: '9.1',
    read: TRACE_READ,
    endpoint: TRACE_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    inputs: ['timeline[].data.not_available_reason', 'timeline[].data.resulting_position'],
    absence: ABSENCE.UNREPORTED,
    reason: 'The execution update reports the position change only. No record in the '
      + 'signal-trace domain carries the absolute position a signal left behind, so it is '
      + 'reported as not available rather than reconstructed from the change.',
    backendChange: 'BC-6',
    note: 'THE REASON IS SERVER-SUPPLIED. `data.resulting_position` is a PRESENT key that is '
      + '`null` on every current database, it is always listed in `data.not_available`, and '
      + '`data.not_available_reason` carries the sentence above from '
      + '`signal_service.POSITION_RESULTING_UNREPORTED_REASON`. RENDER THE SERVER\'S STRING; '
      + 'the copy here is the fallback for a payload that omits it, and keeping two '
      + 'independent sentences for one fact is how they drift.',
  }),
  entry({
    page: PAGES.SIGNAL_TRACE,
    field: 'traceDegraded',
    label: 'Trace completeness',
    requirement: '9.1',
    read: TRACE_READ,
    endpoint: TRACE_ENDPOINT,
    path: 'degraded',
    inputs: ['lifecycle_state_source', 'trace.dag_nodes.source'],
    note: 'A real server signal, surfaced as a `status.warning` note above the timeline when '
      + 'set: "Part of this trace is reconstructed from the signal record because the trace '
      + 'store no longer retains it." Hiding it would misrepresent the trace\'s completeness. '
      + 'Like the dashboard\'s `degraded`, an unset value is healthy and renders no marker.',
  }),
];

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * /download and the landing download section — production-launch-hardening 4.3
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * THE ONE PAGE HERE THAT DECLARES ARTIFACTS RATHER THAN FIGURES
 * ------------------------------------------------------------
 * Every other entry in this module is a number or a list a backend reports. These four are
 * desktop installers a CDN serves, and they are declared here for the reason the module
 * exists: the page advertised four of them with four hardcoded sizes — 84.2, 78.5, 75.4 and
 * 68.2 MB — and served none. Verified against production during that task's design: **all
 * four URLs return 403.** A size string for a file that does not exist is the same
 * fabrication as a hardcoded balance, on a rendered surface, so it is withdrawn through the
 * same convention: `VERDICT.UNAVAILABLE` with a reason, rendered by `ds/Panel`'s
 * `unavailable` state, which `usePanelState` reaches WITHOUT issuing a request.
 *
 * WHY 403 AND NOT 404, AND WHY IT WILL NOT HEAL ON ITS OWN
 * -------------------------------------------------------
 * CI's `dist/` contains no `releases/` directory, and `aws s3 sync dist/ --delete` therefore
 * *deletes* the `/releases/` prefix from the bucket on every deploy. The real 112 MB Windows
 * installer lives at `algo22-terminal/releases/`, a SIBLING of `public/` that Vite never
 * copies, so it never enters `dist/` and never survives a sync. Publishing was considered and
 * rejected: it means either committing 112 MB to the repository or adding an upload step
 * outside the `dist/` sync, and the mac and linux builds do not exist at all.
 *
 * `absence: 'unreported'` therefore reads, for these four, as "nothing published carries it".
 * It is permanent in this module's sense — no backend change resolves it — and restoring a
 * platform is additive rather than a code change to a page: produce the artifact, upload it,
 * and flip that one entry's `verdict` here. The page reads the verdict; it holds no URL.
 *
 * `field` IS THE PLATFORM KEY THE PAGE ALREADY USES
 * ------------------------------------------------
 * `windows`, `macos`, `linuxAppImage`, `linuxDeb` are `DownloadPage.jsx`'s own tab ids, so
 * the page looks an entry up with the id it already holds and there is no second spelling of
 * a platform to keep in step.
 *
 * `endpoint` NAMES THE PATH THAT 403s, AND `path` IS STILL NULL
 * ------------------------------------------------------------
 * `endpoint` records where the artifact WOULD be served from, which is the measurement this
 * task made and the address a future upload has to satisfy. `path` stays `null` because these
 * are not values on a response — a page that read one would be reading a URL to render, which
 * is precisely the link being withdrawn.
 */
const DOWNLOAD_READ = 'the browser fetching the artifact directly from the CDN';
const RUN_IN_BROWSER = ' Run VyomQuant in the browser instead — the web platform is the '
  + 'supported way to trade today.';

const DOWNLOAD_FIELDS = [
  entry({
    page: PAGES.DOWNLOAD,
    field: 'windows',
    label: 'Windows installer',
    requirement: '1.30',
    read: DOWNLOAD_READ,
    endpoint: 'GET /releases/windows/VyomQuant-Setup-0.1.0.exe',
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'No Windows installer is published for this release, so there is nothing to '
      + 'download here.' + RUN_IN_BROWSER,
    note: 'The one platform whose artifact EXISTS: `algo22-terminal/releases/windows/'
      + 'VyomQuant-Setup-0.1.0.exe`, 112,117,309 bytes. It is a sibling of `public/`, so Vite '
      + 'never copies it into `dist/` and the S3 sync never publishes it. The reason therefore '
      + 'says "not published" and not "not built" — the two are different facts and a trader '
      + 'reading the second would be told something untrue.',
  }),
  entry({
    page: PAGES.DOWNLOAD,
    field: 'macos',
    label: 'macOS DMG',
    requirement: '1.30',
    read: DOWNLOAD_READ,
    endpoint: 'GET /releases/mac/VyomQuant-0.1.0-universal.dmg',
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'No macOS build has been produced for this release, so there is nothing to '
      + 'download here.' + RUN_IN_BROWSER,
    note: 'No universal DMG exists anywhere in the tree. The page previously advertised it at '
      + '78.5 MB with a SHA-256 that is the hash of the empty string.',
  }),
  entry({
    page: PAGES.DOWNLOAD,
    field: 'linuxAppImage',
    label: 'Linux AppImage',
    requirement: '1.30',
    read: DOWNLOAD_READ,
    endpoint: 'GET /releases/linux/VyomQuant-0.1.0.AppImage',
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'No Linux AppImage has been produced for this release, so there is nothing to '
      + 'download here.' + RUN_IN_BROWSER,
    note: 'No AppImage exists. A 64-byte text file named `.AppImage` sat under '
      + '`public/releases/` and was publishable; task 4.2 removed it and added the deploy-time '
      + 'size gate that rejects its return.',
  }),
  entry({
    page: PAGES.DOWNLOAD,
    field: 'linuxDeb',
    label: 'Linux DEB package',
    requirement: '1.30',
    read: DOWNLOAD_READ,
    endpoint: 'GET /releases/linux/vyomquant_0.1.0_amd64.deb',
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'No Debian package has been produced for this release, so there is nothing to '
      + 'download here.' + RUN_IN_BROWSER,
    note: 'No .deb exists. Same provenance as the AppImage: a 67-byte placeholder on a '
      + 'publishable path, removed by task 4.2.',
  }),
];

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * Risk Settings (retail-ui-simplification Requirement 4.2) — /app/risk
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Not one of §7's pages. It is here for the reason `DOWNLOAD` is: the page renders figures,
 * the figures were being substituted, and a substitution is only reviewable once the real
 * source path is written down beside it.
 *
 * WHAT WAS BEING SUBSTITUTED, MEASURED BEFORE THE MIGRATION
 * --------------------------------------------------------
 * Three `?? 0` fallbacks and two `??` defaults, all on a risk surface:
 *
 *     Number(m.margin_ratio ?? 0)      Number(m.free_margin ?? 0)   Number(m.risk_score ?? 0)
 *     Number(s.max_position_size ?? 20)             s.strategy_name ?? `Strategy #${i + 1}`
 *
 * `risk_score ?? 0` is §6.3's hazard in a different field and it is the worst of the five: a
 * risk score of zero is the safest-looking figure a broken margin read can publish, exactly
 * as `exchange_api_latency_ms` rendering `0 ms` reads as a working connection at the moment
 * nothing was measured. `free_margin ?? 0` inverts the same way in the other direction — 0%
 * free margin reads as a fully committed account.
 *
 * WHY THE THREE LIMITS AND THE THREE MARGIN FIGURES ARE `absence: NEVER`
 * ---------------------------------------------------------------------
 * Because the routes say so, and the claim is checkable rather than hopeful.
 * `routers/risk.py:48`'s `get_user_risk_settings_store` seeds every key it returns —
 * `max_daily_loss` 500.0, `max_positions` 10, `max_leverage` 3, `circuit_breaker_armed`,
 * `kill_switches` — so `GET /api/risk/settings` cannot answer 200 without all three limits.
 * `GET /api/risk/margin-health` (`:395`) returns all four keys unconditionally and *raises*
 * when the Paper_Account cannot be read: its own comment records that the refusal travels to
 * the client as a catalogued 503, "there is no `except` here for it to be swallowed by". So
 * a failed margin read is the PANEL's error state, not a per-figure marker, and declaring a
 * per-figure reason for it would be dead copy — which the third assertion in
 * `describe('pageFields: Requirement 19.3 …')` exists to refuse.
 *
 * `absence: NEVER` is not the same as "the page may substitute". The page reads each of the
 * six through `design/reported.fromNullable`, so a response that somehow arrives without one
 * renders `reported.js`'s own `UNREPORTED_REASON` on the marker rather than a zero. The
 * declaration says the marker is not expected; the render path makes it reachable anyway.
 *
 * WHY THE TWO PER-STRATEGY FIELDS ARE NOT
 * --------------------------------------
 * `GET /api/risk/strategy-limits` returns `list(_user_strategy_limits[uid].values())`, and
 * every entry in that store was written by `item.dict(exclude_none=True)` (`:592`). A field
 * the trader never set is therefore ABSENT FROM THE OBJECT rather than null — which is why
 * `max_position_size ?? 20` could publish a 20% allocation nobody configured, and
 * `strategy_name ?? 'Strategy #1'` could name a strategy after its position in a list.
 * Both carry a reason, and both reasons are rendered.
 *
 * The store is also in-memory and per-process, so the list can omit a limit that is really
 * configured — the `REGISTRY_CAVEAT` shape. That is a fact about the collection rather than
 * about a field, so it belongs on the panel's own copy and not in an entry here.
 */
const RISK_CONFIG_READ = 'riskApi.getConfig';
const RISK_CONFIG_ENDPOINT = 'GET /api/risk/settings';
const MARGIN_READ = 'riskApi.getMarginHealth';
const MARGIN_ENDPOINT = 'GET /api/risk/margin-health';
const LIMITS_READ = 'riskApi.getStrategyLimits';
const LIMITS_ENDPOINT = 'GET /api/risk/strategy-limits';
const RISK_MODULE = 'src/api/modules/risk.js';

const RISK_SETTINGS_FIELDS = [
  entry({
    page: PAGES.RISK_SETTINGS,
    field: 'maxDailyLoss',
    label: 'Max daily loss',
    requirement: '4.2',
    read: RISK_CONFIG_READ,
    endpoint: RISK_CONFIG_ENDPOINT,
    path: 'max_daily_loss',
    documentedIn: RISK_MODULE,
    note: 'The `RiskConfig` typedef documents it. The page also holds it as the position of a '
      + 'range control, and that control has to have a position even before the read lands — '
      + 'so the slider keeps the page\'s own starting value while the READOUT beside it renders '
      + 'this path. The two cannot be collapsed: a range input cannot render a marker.',
  }),
  entry({
    page: PAGES.RISK_SETTINGS,
    field: 'maxPositions',
    label: 'Max concurrent positions',
    requirement: '4.2',
    read: RISK_CONFIG_READ,
    endpoint: RISK_CONFIG_ENDPOINT,
    path: 'max_positions',
    documentedIn: RISK_MODULE,
  }),
  entry({
    page: PAGES.RISK_SETTINGS,
    field: 'maxLeverage',
    label: 'Max account leverage',
    requirement: '4.2',
    read: RISK_CONFIG_READ,
    endpoint: RISK_CONFIG_ENDPOINT,
    path: 'max_leverage',
    documentedIn: RISK_MODULE,
  }),
  entry({
    page: PAGES.RISK_SETTINGS,
    field: 'marginRatio',
    label: 'Margin ratio',
    requirement: '4.2',
    read: MARGIN_READ,
    endpoint: MARGIN_ENDPOINT,
    path: 'margin_ratio',
    documentedIn: RISK_MODULE,
    note: 'Locked balance as a percentage of total equity, rounded server-side to 2dp. A '
      + 'genuine `0.0` means nothing is committed and renders as `0.00%`; it is a reading. The '
      + '`Number(… ?? 0)` this page used to apply made the two indistinguishable.',
  }),
  entry({
    page: PAGES.RISK_SETTINGS,
    field: 'freeMargin',
    label: 'Free margin',
    requirement: '4.2',
    read: MARGIN_READ,
    endpoint: MARGIN_ENDPOINT,
    path: 'free_margin',
    documentedIn: RISK_MODULE,
    note: 'Available balance as a percentage of total equity. The route answers `100.0` for a '
      + 'zero-equity account rather than dividing by zero, so a 100% reading is real. `0` is '
      + 'the dangerous substitution here — it reads as an account with nothing left to trade.',
  }),
  entry({
    page: PAGES.RISK_SETTINGS,
    field: 'riskScore',
    label: 'Risk score',
    requirement: '4.2',
    read: MARGIN_READ,
    endpoint: MARGIN_ENDPOINT,
    path: 'risk_score',
    documentedIn: RISK_MODULE,
    note: '`min(100, int(margin_ratio * 0.8 + (100 - free_margin) * 0.2))`, computed server-'
      + 'side. Not a percentage and not derived here — recomputing it in the page would be a '
      + 'second definition of one figure. **It must never render as `0` on the strength of an '
      + 'absent read**: a zero risk score is the safest-looking figure a broken margin read can '
      + 'publish, which is `exchange_api_latency_ms`\'s hazard (§6.3) in another field.',
  }),
  entry({
    page: PAGES.RISK_SETTINGS,
    field: 'strategyAllocationPct',
    label: 'Capital allocation',
    requirement: '4.2',
    read: LIMITS_READ,
    endpoint: LIMITS_ENDPOINT,
    path: 'limits[].max_position_size',
    verdict: VERDICT.AVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'No capital limit is recorded for this strategy, so there is no allocation to show. '
      + 'The account-wide limits above are what apply to it until one is set.',
    note: 'Optional on `StrategyLimitItem` and stored through `dict(exclude_none=True)`, so an '
      + 'unset limit is absent from the object rather than null. The page substituted 20 for '
      + 'it, which published an allocation percentage nobody configured — and then offered a '
      + 'slider positioned at that invented figure.',
  }),
  entry({
    page: PAGES.RISK_SETTINGS,
    field: 'strategyName',
    label: 'Strategy',
    requirement: '4.2',
    read: LIMITS_READ,
    endpoint: LIMITS_ENDPOINT,
    path: 'limits[].strategy_name',
    verdict: VERDICT.AVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'This limit was stored without a strategy name, so the strategy id it was stored '
      + 'against is shown instead of a name.',
    note: 'The page substituted `Strategy #${i + 1}` — a name derived from a position in a '
      + 'list, which changes when another limit is added and identifies nothing. The id IS a '
      + 'real server field, so falling back to it is a different fact rather than a fabricated '
      + 'one, and the reason says which of the two is on screen.',
  }),
];

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * The declaration, and the two indexes over it
 * ═══════════════════════════════════════════════════════════════════════════
 */

/**
 * Every declared field, page by page, in §7's order.
 *
 * The canonical export. Property 5 iterates this: for each entry, a failed read must render
 * no figure and no zero, and every reachable not-available marker must carry a non-empty
 * reason. Both are decidable from the entry alone, which is why this is an array of frozen
 * records rather than a set of getters.
 */
export const PAGE_FIELDS = Object.freeze([
  ...DASHBOARD_FIELDS,
  ...STRATEGIES_FIELDS,
  ...BACKTESTER_FIELDS,
  ...LIVE_TRADING_FIELDS,
  ...PORTFOLIO_FIELDS,
  ...TRADE_HISTORY_FIELDS,
  ...SIGNAL_TRACE_FIELDS,
  ...DOWNLOAD_FIELDS,
  ...RISK_SETTINGS_FIELDS,
]);

/**
 * `'dashboard/currentDrawdown'` — one entry's key, unique across the declaration.
 *
 * The only function this module exports, and it computes nothing about availability: it
 * exists so the key has one spelling rather than one per call site.
 *
 * @param {{page: string, field: string}} pageField
 * @returns {string}
 */
export const pageFieldKey = ({ page, field }) => `${page}/${field}`;

/**
 * The entries of one page, keyed by `PAGES` value.
 *
 * What a page's view-model builder walks. Every `PAGES` value has an array, so a builder
 * cannot silently read `undefined` for a page that has no declarations yet.
 */
export const PAGE_FIELDS_BY_PAGE = Object.freeze(
  Object.fromEntries(
    Object.values(PAGES).map((page) => [
      page,
      Object.freeze(PAGE_FIELDS.filter((f) => f.page === page)),
    ]),
  ),
);

/** Every entry by `page/field`, for a test or a builder that holds one key. */
export const PAGE_FIELD_BY_KEY = Object.freeze(
  Object.fromEntries(PAGE_FIELDS.map((f) => [pageFieldKey(f), f])),
);

/**
 * The six §16 changes, each mapped to the entries that read what it landed.
 *
 * Requirement 19.2 asks for the register; this is the register pointed at the code that
 * consumes it, so a change removed from the backend shows up as an entry reading a path that
 * no longer exists rather than as a page quietly rendering nothing.
 */
export const BACKEND_CHANGE_FIELDS = Object.freeze({
  'BC-1': Object.freeze(PAGE_FIELDS.filter((f) => f.backendChange === 'BC-1')),
  'BC-2': Object.freeze(PAGE_FIELDS.filter((f) => f.backendChange === 'BC-2')),
  'BC-3': Object.freeze(PAGE_FIELDS.filter((f) => f.backendChange === 'BC-3')),
  'BC-4': Object.freeze(PAGE_FIELDS.filter((f) => f.backendChange === 'BC-4')),
  'BC-5': Object.freeze(PAGE_FIELDS.filter((f) => f.backendChange === 'BC-5')),
  'BC-6': Object.freeze(PAGE_FIELDS.filter((f) => f.backendChange === 'BC-6')),
});

/**
 * The ❌ rows: no path, nothing to wait for, a marker is all a page may render.
 *
 * These are the entries a release note has to name, because they are where a figure that
 * reads `0.00` today becomes "—" (§14.2, M6).
 */
export const UNAVAILABLE_FIELDS = Object.freeze(
  PAGE_FIELDS.filter((f) => f.verdict === VERDICT.UNAVAILABLE),
);

/** The ⚠️ rows: computed in the frontend from real inputs, with the derivation stated. */
export const DERIVED_FIELDS = Object.freeze(
  PAGE_FIELDS.filter((f) => f.verdict === VERDICT.DERIVED),
);

/**
 * The entries an operator can resolve without a code change — BC-3's `last_signal_at`, until
 * migration 015 is applied by hand.
 *
 * Separated from `UNAVAILABLE_FIELDS` and from the permanent absences on purpose: this is
 * the one case where "not available" is a deployment state, and a release note that lumps it
 * in with the rest tells an operator there is nothing they can do.
 */
export const PENDING_MIGRATION_FIELDS = Object.freeze(
  PAGE_FIELDS.filter((f) => f.absence === ABSENCE.PENDING_MIGRATION),
);
