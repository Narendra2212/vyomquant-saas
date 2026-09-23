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
  SECURITY_LOGS: 'security-logs',
  WIZARD: 'wizard',
  PROFILE: 'profile',
  BILLING: 'billing',
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
 * Security Logs (retail-ui-simplification Requirement 4.2) — /app/security-logs
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * THE TABLE IS SIX COLUMNS AND THE BACKEND HAS FIVE, WHICH IS WHY THIS SECTION EXISTS
 * ----------------------------------------------------------------------------------
 * `GET /api/security/logs` is `supabase.table('security_logs').select('*')`, so the fields
 * available are exactly the table's, declared by `migrations/006_reconcile_production_
 * database.sql:327`:
 *
 *     id UUID PK · user_id TEXT NOT NULL · event_type VARCHAR(100) NOT NULL
 *     ip_address VARCHAR(45) · user_agent TEXT · details JSONB · created_at TIMESTAMPTZ
 *
 * **There is no `location` column and no `status` column.** The page rendered both, and it
 * filled them with constants:
 *
 *     loc:    l.location || l.loc   || "Secure Session"
 *     status: (l.status            || "success").toLowerCase()
 *
 * The second is the most consequential substitution found anywhere in this pass. `status` is
 * unreported for every row, so the fallback fires for every row, so **every entry in a
 * security audit log was badged green and read `SUCCESS`** — including a failed login, if one
 * were logged. And because `failed_attempts` counted `status === 'failed'`, the page also
 * published *Failed Attempts: 0* as a measurement of a field that cannot carry a failure.
 * That is §6's hazard at its worst: not a wrong number but a REASSURING one, on the one screen
 * a trader opens to find out whether someone else has been in their account.
 *
 * Three more of the same shape, on the same rows:
 *
 *     ip_address  || "127.0.0.1"                 a localhost address, for an audit record
 *     user_agent  || "Browser / Desktop Client"
 *     created_at  ? new Date(created_at) : NEW DATE()   the time the page was OPENED
 *
 * The last one is the timestamp hazard exactly: a record whose `created_at` did not arrive was
 * stamped with the moment the trader loaded the page, which is indistinguishable from a real
 * reading and is the field the whole log is ordered by.
 *
 * AND THREE OF THE FOUR SUMMARY FIGURES WERE NOT MEASUREMENTS AT ALL
 * -----------------------------------------------------------------
 *     api_calls_24h:   normalized.length * 12    invented; no endpoint reports API call counts
 *     failed_attempts: see `status` above        structurally always 0
 *     active_sessions: 1                         a literal
 *
 * Each is declared `UNAVAILABLE` here with the reason a trader reads instead. The fourth,
 * *Logins (30d)*, IS computable — but not as labelled: the read is `limit=100` most recent
 * records with no date window at all, so "30d" was a claim the request does not support. It is
 * declared `DERIVED` with its real derivation and its real scope, and its label says so.
 *
 * WHAT IS NOT FIXED HERE, RECORDED RATHER THAN SMUGGLED
 * ---------------------------------------------------
 * `routers/user.py:174` answers `[]` when the request has no Supabase client, which collapses
 * "you have no security events" into "we could not read them" before the response leaves the
 * server. Requirement 16.7 puts `backend_app/` out of this spec's reach, so the page cannot
 * tell the two apart and this note is where that limit is written down.
 */
const SECURITY_LOGS_READ = 'userApi.getSecurityLogs';
const SECURITY_LOGS_ENDPOINT = 'GET /api/security/logs?limit=100';
const SECURITY_LOGS_TABLE = 'migrations/006_reconcile_production_database.sql:327 declares the '
  + '`security_logs` table: id, user_id, event_type NOT NULL, ip_address, user_agent, details, '
  + 'created_at. The route is `select("*")`, so these seven are the whole field set.';

const SECURITY_LOGS_FIELDS = [
  entry({
    page: PAGES.SECURITY_LOGS,
    field: 'eventType',
    label: 'Event',
    requirement: '4.2',
    read: SECURITY_LOGS_READ,
    endpoint: SECURITY_LOGS_ENDPOINT,
    path: '[].event_type',
    note: `NOT NULL in the table, so no marker is reachable. ${SECURITY_LOGS_TABLE} The page `
      + 'also read `l.event` and `l.action` before it; neither column exists.',
  }),
  entry({
    page: PAGES.SECURITY_LOGS,
    field: 'ipAddress',
    label: 'IP address',
    requirement: '4.2',
    read: SECURITY_LOGS_READ,
    endpoint: SECURITY_LOGS_ENDPOINT,
    path: '[].ip_address',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No IP address was recorded for this event, so there is no address to show. It is '
      + 'not 127.0.0.1 and it is not your own address — it was simply not captured.',
    note: 'Nullable. The page substituted `127.0.0.1`, which on an audit record reads as "this '
      + 'happened on your own machine" — a claim about the origin of an event nobody recorded '
      + 'the origin of.',
  }),
  entry({
    page: PAGES.SECURITY_LOGS,
    field: 'userAgent',
    label: 'Device / client',
    requirement: '4.2',
    read: SECURITY_LOGS_READ,
    endpoint: SECURITY_LOGS_ENDPOINT,
    path: '[].user_agent',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No device or client was recorded for this event, so there is nothing to identify '
      + 'what made the request.',
    note: 'Nullable. The page substituted `Browser / Desktop Client`, which names a device '
      + 'class on the strength of nothing.',
  }),
  entry({
    page: PAGES.SECURITY_LOGS,
    field: 'recordedAt',
    label: 'Timestamp',
    requirement: '4.2',
    read: SECURITY_LOGS_READ,
    endpoint: SECURITY_LOGS_ENDPOINT,
    path: '[].created_at',
    absence: ABSENCE.UNMEASURABLE,
    reason: 'No time was recorded for this event, so there is no timestamp to show. The list is '
      + 'ordered by this field, so an event without one may not be in the position you expect.',
    note: 'Defaults to NOW() on insert but is nullable, and the whole list is ordered by it. '
      + '**It must never render as the current time**: the page substituted `new Date()` — the '
      + 'moment the page was opened — which is a plausible timestamp on an audit record and is '
      + 'therefore the same class of defect as `exchange_api_latency_ms` rendering "0 ms".',
  }),
  entry({
    page: PAGES.SECURITY_LOGS,
    field: 'location',
    label: 'Location',
    requirement: '4.2',
    read: SECURITY_LOGS_READ,
    endpoint: SECURITY_LOGS_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Sign-in locations are not recorded, so this column cannot be filled for any event. '
      + 'The IP address beside it is the nearest thing the log holds.',
    note: `The \`security_logs\` table has no location column at all. ${SECURITY_LOGS_TABLE} `
      + 'The page filled the column with the constant "Secure Session" for every row — which '
      + 'is not a location, and reads as a reassurance.',
  }),
  entry({
    page: PAGES.SECURITY_LOGS,
    field: 'outcome',
    label: 'Status',
    requirement: '4.2',
    read: SECURITY_LOGS_READ,
    endpoint: SECURITY_LOGS_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'This log records that an event happened, not whether it succeeded, so a failed '
      + 'sign-in cannot be told apart from a successful one here.',
    note: 'The table has no status column. **This is the worst substitution in the tree**: the '
      + 'page defaulted it to "success" and rendered a green chip reading SUCCESS on every row '
      + 'of a security audit log. A failed attempt would have been badged as a success, and the '
      + 'figure below counted the same absent field to publish "Failed Attempts: 0".',
  }),
  entry({
    page: PAGES.SECURITY_LOGS,
    field: 'loginEventCount',
    label: 'Login events (last 100 records)',
    requirement: '4.2',
    read: SECURITY_LOGS_READ,
    endpoint: SECURITY_LOGS_ENDPOINT,
    inputs: ['[].event_type'],
    verdict: VERDICT.DERIVED,
    derivation: 'The number of returned records whose `event_type` contains "login" or "auth", '
      + 'case-insensitively, over the records this read returned — which is the 100 most recent, '
      + 'not a date window.',
    tooltip: 'Counted over the 100 most recent records this page reads, not over 30 days: the '
      + 'request carries a record limit and no date range.',
    note: 'The page labelled this "Logins (30d)" and there is no 30-day window anywhere in the '
      + 'request — `getSecurityLogs(100)` is `?limit=100`. It also substituted the TOTAL record '
      + 'count (floored at 1) whenever the match count was zero, so an account with no login '
      + 'events showed a login figure. A genuine 0 now renders 0.',
  }),
  entry({
    page: PAGES.SECURITY_LOGS,
    field: 'apiCallCount24h',
    label: 'API calls (24h)',
    requirement: '4.2',
    read: SECURITY_LOGS_READ,
    endpoint: SECURITY_LOGS_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'API call volume is not recorded anywhere, so there is no 24-hour figure to show. '
      + 'The events below are what the account does have a record of.',
    note: 'The page computed `normalized.length * 12` — the number of returned log records '
      + 'multiplied by twelve. No endpoint reports an API call count and no constant relates one '
      + 'to the other; the figure was manufactured, and it grew with the record limit.',
  }),
  entry({
    page: PAGES.SECURITY_LOGS,
    field: 'failedAttemptCount',
    label: 'Failed attempts',
    requirement: '4.2',
    read: SECURITY_LOGS_READ,
    endpoint: SECURITY_LOGS_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'This log does not record whether an event succeeded or failed, so failed attempts '
      + 'cannot be counted from it. A zero here would mean "not recorded", not "none".',
    note: 'Counted rows where `status === "failed" || status === "error"` on a field the table '
      + 'does not have and the page defaulted to "success", so the count was structurally always '
      + '0 — the single most reassuring figure a security page can publish without evidence.',
  }),
  entry({
    page: PAGES.SECURITY_LOGS,
    field: 'activeSessionCount',
    label: 'Active sessions',
    requirement: '4.2',
    read: SECURITY_LOGS_READ,
    endpoint: SECURITY_LOGS_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Concurrent sessions are not tracked, so this count is not available. A sign-in '
      + 'event below is the record of a session starting.',
    note: 'The page rendered the literal `1`. Whatever the account\'s real session count is, `1` '
      + 'is the answer that says "only you are signed in", which is the claim a trader opens '
      + 'this page to check.',
  }),
];

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * Wizard (retail-ui-simplification Requirement 4.2) — /wizard
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Not one of §7's pages. It is here for the reason `RISK_SETTINGS` is: the page renders
 * figures, the figures were being invented, and an invention is only reviewable once the
 * real source path is written down beside it. This is the FIRST-RUN surface — one of the
 * first screens a new retail account sees — which makes it the worst place in the tree for
 * a plausible number, and it carried three separate kinds.
 *
 * WHAT WAS BEING INVENTED, MEASURED BEFORE THE MIGRATION
 * -----------------------------------------------------
 * **1. Two figures that were literals.** Step 2 runs a real demo backtest — the
 * `ReferenceError` that used to swallow it is long fixed and `api.strategies.backtest`
 * answers a `BacktestResult` — and the page then DISCARDED the response and rendered:
 *
 *     Total Return  +12.4%          Win Rate  68.2%
 *
 * Hardcoded, to one decimal place, under a green tick reading *Backtest Complete!*. The
 * `catch` also set the status to `complete`, so a backtest that failed outright published
 * the same two numbers. `total_return_pct` and `win_rate_pct` are real keys on the real
 * response — `src/api/modules/strategies.js`'s `BacktestResult` typedef declares both —
 * so the figures a new trader reads are now the run's own, and a run that reported neither
 * renders the marker with its reason instead of a result nobody computed.
 *
 * **2. A price read off two fields that do not exist.** The plan cards computed
 * `const priceINR = p.inr || 0` and `const priceUSD = p.usd || 0`. `GET /api/billing/plans`
 * answers `PricingService.get_localized_plans`, whose per-plan object carries
 * `localized_price`, `currency`, `currency_symbol`, `base_price`, `base_currency`,
 * `checkout_price` and `checkout_currency` — **and no `inr` key and no `usd` key at all.**
 * Both reads were therefore `undefined || 0`, i.e. structurally always `0`, which made
 * `priceINR === 0` structurally always true: **every plan on the page rendered the price
 * as "Free" with a "Start Free" button, Pro and Enterprise included**, while the button's
 * handler went on to open a real paid checkout for them. `pages/Billing.jsx:404` reads
 * `plan.localized_price` and `:406` reads `plan.currency_symbol`, so the right paths were
 * already in the tree one page away. This is the entry that exists for them.
 *
 * **3. A green tick with no read behind it.** The *Security Alerts* row of step 1 was
 * declared `ok: true` — a literal — so a new account was told *Notify on new device logins*
 * was on, with a tick, whatever the truth. Nothing in the backend reports a notification
 * preference of any kind, so this entry is ❌ and the row states that rather than claiming
 * it. Requirement 16.2 keeps the row and its *Manage* control; Requirement 19.5 forbids
 * filling it with a plausible value, and those two are only compatible if the row explains
 * itself.
 *
 * WHY THE TWO SECURITY STATES ARE ⚠️ DERIVED AND NOT ✅ AVAILABLE
 * -------------------------------------------------------------
 * Because `null` means something different on each of their source fields, and collapsing
 * that is how a genuine "not verified" becomes "not available" — the inverse of the
 * substitution this module exists to prevent, and just as wrong.
 *
 * Supabase answers `user.email_confirmed_at: null` for an account whose email is genuinely
 * UNCONFIRMED. That null is a reading, not an absence (Requirement 19.1), so the figure is
 * the PRESENCE of the timestamp rather than the timestamp: `Boolean(user.email_confirmed_at)`
 * is `false` and renders as *Not verified*. The marker is reachable only when there is no
 * session to read at all — the page is routed at `/wizard`, outside the shell, so a signed-out
 * visitor can open it — and that is what the reason says.
 *
 * `getAuthenticatorAssuranceLevel` is the same shape one level along: `currentLevel` is
 * `'aal1'` for an account with no second factor, which is a reading. The derivation below is
 * the page's own existing expression, carried across unchanged.
 *
 * WHAT IS DELIBERATELY NOT DECLARED HERE
 * -------------------------------------
 * `plans[].features` is a LIST, and `design/reported.js`'s `isReadableValue` refuses arrays
 * by design — a `Reported<T>` is a scalar. The page guards it with `Array.isArray` instead,
 * the way `pages/SecurityLogs.jsx` guards its rows, and the absence of four feature lines is
 * visible as the absence of four feature lines. Same for `plans[].recommended`, which selects
 * a treatment rather than reporting a figure.
 */
const WIZARD_SECURITY_READ = 'supabase.auth.getUser';
const WIZARD_SECURITY_ENDPOINT = 'GET /auth/v1/user (Supabase)';
const WIZARD_MFA_READ = 'supabase.auth.mfa.getAuthenticatorAssuranceLevel';
const WIZARD_MFA_ENDPOINT = 'GET /auth/v1/factors (Supabase AAL)';
const WIZARD_BACKTEST_READ = 'strategiesApi.backtest';
const WIZARD_BACKTEST_ENDPOINT = 'POST /api/strategies/backtest';
const WIZARD_PLANS_READ = 'billingApi.getPlans';
const WIZARD_PLANS_ENDPOINT = 'GET /api/billing/plans';
const STRATEGIES_API_MODULE = 'src/api/modules/strategies.js';
const BILLING_API_MODULE = 'src/api/modules/billing.js';

const WIZARD_FIELDS = [
  entry({
    page: PAGES.WIZARD,
    field: 'emailVerified',
    label: 'Email verified',
    requirement: '4.2',
    read: WIZARD_SECURITY_READ,
    endpoint: WIZARD_SECURITY_ENDPOINT,
    inputs: ['user.email_confirmed_at'],
    verdict: VERDICT.DERIVED,
    absence: ABSENCE.UNMEASURABLE,
    derivation: 'Boolean(user.email_confirmed_at) — the PRESENCE of the confirmation '
      + 'timestamp, not the timestamp. A null timestamp is a genuine "not verified" and '
      + 'renders as one; only a read that produced no session at all is an absence.',
    reason: 'Your sign-in could not be read, so whether this account\'s email has been '
      + 'confirmed is unknown here. Verify opens your profile, which shows the current state '
      + 'and can send a new confirmation.',
  }),
  entry({
    page: PAGES.WIZARD,
    field: 'mfaEnabled',
    label: 'Two-factor authentication',
    requirement: '4.2',
    read: WIZARD_MFA_READ,
    endpoint: WIZARD_MFA_ENDPOINT,
    inputs: ['currentLevel', 'nextLevel'],
    verdict: VERDICT.DERIVED,
    absence: ABSENCE.UNMEASURABLE,
    derivation: "currentLevel === 'aal2' || nextLevel === 'aal2' — the page's own existing "
      + "expression, unchanged. `'aal1'` is a reading and means no second factor is enrolled.",
    reason: 'Your assurance level could not be read, so whether a second factor is enrolled '
      + 'is unknown here. Enable opens the authenticator setup, which reports the current '
      + 'state before it changes anything.',
  }),
  entry({
    page: PAGES.WIZARD,
    field: 'securityAlertsEnabled',
    label: 'Security alerts',
    requirement: '4.2',
    read: WIZARD_SECURITY_READ,
    endpoint: WIZARD_SECURITY_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Nothing records whether new-device login alerts are switched on for this '
      + 'account, so this cannot be confirmed here. Manage opens your security log, which is '
      + 'where a sign-in from a new device is recorded either way.',
    note: 'The row was declared `ok: true` — a literal, with no read behind it — so a new '
      + 'account was shown a green tick against a notification setting nobody had checked. '
      + 'There is no notification-preference store and no endpoint that reports one.',
  }),
  entry({
    page: PAGES.WIZARD,
    field: 'backtestTotalReturnPct',
    label: 'Total return',
    requirement: '4.2',
    read: WIZARD_BACKTEST_READ,
    endpoint: WIZARD_BACKTEST_ENDPOINT,
    path: 'total_return_pct',
    absence: ABSENCE.UNREPORTED,
    documentedIn: STRATEGIES_API_MODULE,
    reason: 'This demo run did not report a total return, so there is no figure to show for '
      + 'it. Nothing in your account is affected either way: the demo runs on historical '
      + 'BTC/USDT data and places no orders.',
    tooltip: 'What a MACD crossover would have returned on BTC/USDT over the period this '
      + 'demo run covered. A historical result, not a forecast.',
    note: 'The page rendered the literal `+12.4%` and discarded the response that carries '
      + 'this key. A genuine `0` is a reading — a strategy that returned nothing returned '
      + 'nothing — and renders as `0.0%`.',
  }),
  entry({
    page: PAGES.WIZARD,
    field: 'backtestWinRatePct',
    label: 'Win rate',
    requirement: '4.2',
    read: WIZARD_BACKTEST_READ,
    endpoint: WIZARD_BACKTEST_ENDPOINT,
    path: 'win_rate_pct',
    absence: ABSENCE.UNREPORTED,
    documentedIn: STRATEGIES_API_MODULE,
    reason: 'This demo run did not report a win rate, so there is no figure to show for it. '
      + 'A run that placed no trades has no win rate to report.',
    tooltip: 'The share of this demo run\'s trades that closed in profit. A historical '
      + 'result, not a forecast.',
    note: 'The page rendered the literal `68.2%`. A genuine `0` is a reading and renders as '
      + '`0.0%`; the substitution made a losing run and a run nobody measured identical.',
  }),
  entry({
    page: PAGES.WIZARD,
    field: 'planName',
    label: 'Plan',
    requirement: '4.2',
    read: WIZARD_PLANS_READ,
    endpoint: WIZARD_PLANS_ENDPOINT,
    path: 'plans[].name',
    documentedIn: BILLING_API_MODULE,
    note: '`SubscriptionEngine`\'s plan record always carries a name, so no marker is '
      + 'expected. The page still reads it through `design/reported.fromNullable`, so a '
      + 'response that somehow arrives without one renders the marker rather than a blank '
      + 'card heading — the declaration says the marker is not expected, the render path '
      + 'makes it reachable anyway.',
  }),
  entry({
    page: PAGES.WIZARD,
    field: 'planPrice',
    label: 'Monthly price',
    requirement: '4.2',
    read: WIZARD_PLANS_READ,
    endpoint: WIZARD_PLANS_ENDPOINT,
    path: 'plans[].localized_price',
    inputs: ['plans[].currency_symbol', 'plans[].currency'],
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'This plan\'s price could not be read, so it is not shown here rather than '
      + 'guessed. Selecting the plan opens checkout, which prices it server-side before '
      + 'anything is charged.',
    note: 'The page read `p.inr` and `p.usd`, and the plans response carries NEITHER — so '
      + 'both were `undefined || 0`, every plan advertised itself as "Free" with a "Start '
      + 'Free" button, and the paid ones then opened a paid checkout. A genuine `0` IS the '
      + 'free tier and still renders as "Free"; that is the reading the substitution was '
      + 'impersonating. The currency comes from the same object: `currency_symbol` for the '
      + 'display glyph and `currency` as the code when no glyph was sent. A figure with '
      + 'neither is not a price and renders the marker.',
  }),
];

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * Profile (retail-ui-simplification Requirement 4.2) — /app/profile
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Not one of §7's pages. It is here for the reason `RISK_SETTINGS` and `WIZARD` are: the
 * page reports figures about the trader's own account, almost none of them were read from
 * where the page claimed, and an invention is only reviewable once the real source path is
 * written down beside it. `pages/Profile.jsx` is the largest single file in the migration
 * group (999 lines) and it carried MORE substituted figures than any other page in this
 * spec — more than `Wizard.jsx`, which held the previous record at three kinds.
 *
 * THE SIX READS, AND WHICH ONE IS NOT WHAT IT LOOKS LIKE
 * -----------------------------------------------------
 *   GET /api/user/profile             the `profiles` row. Real, per-account.
 *   GET /api/billing/plan             an alias for `/api/billing/entitlements`. Real.
 *   GET /api/security/logs            `security_logs` for this user. Real.
 *   GET /api/notifications/settings   `notification_settings` for this user. Real.
 *   GET /api/referral/stats           `ReferralStatsResponse`. Real.
 *   GET /api/stats                    **NOT THIS ACCOUNT'S.** See below.
 *
 * **`GET /api/stats` IS PLATFORM-WIDE AND UNAUTHENTICATED.** `backend_app/main.py:938`
 * declares it with no `Depends(get_current_user)` and calls
 * `get_dashboard_data(user={"id": "public"})` — a literal sentinel id. So the four figures
 * the *Automation Account Context* card reported as the trader's own fleet were the
 * platform's aggregate, and would read the same for a brand-new account with nothing
 * deployed. Two further properties of that response matter here and are recorded on the
 * entries rather than left for the next reader to rediscover:
 *
 *   * `total_pnl` is `overview["today_pnl"]` — **today's** P&L, published under a key
 *     spelling "total". The card's label said *TOTAL PNL*, so the figure was wrong about
 *     its own period as well as about whose account it described.
 *   * the handler's `except` arm returns a 200 carrying `0` for all five counts. A failed
 *     aggregation is therefore indistinguishable on the wire from a genuinely idle
 *     platform, which is §6's hazard one layer down. Requirement 16.7 puts `backend_app/`
 *     out of reach; the page cannot tell them apart and these entries say so.
 *
 * WHAT WAS BEING INVENTED, MEASURED BEFORE THE MIGRATION
 * -----------------------------------------------------
 * **1. A security card that told every account its second factor was on.** The *MFA
 * AUTHENTICATION* tile rendered a green dot and the word *Configured* as static JSX, with
 * no read behind it at all. `/api/user/profile` returns the `profiles` row and nothing in
 * it reports an enrolled factor; the page never called
 * `getAuthenticatorAssuranceLevel` (which `pages/Wizard.jsx` does). An account with no
 * second factor read *Configured*, in green, on the panel a trader opens to check exactly
 * that. This is the worst single substitution found in this spec.
 *
 * **2. A referral code manufactured from the account's UUID.** The code rendered
 * `referral.referral_code || profile?.id?.substring(0, 8).toUpperCase() || '...'` and the
 * copy button copied the same expression. A trader whose referral read came back without a
 * code was handed the first eight characters of their own primary key, in a field whose
 * whole purpose is to be shared with other people, and nothing on screen said it was not
 * their referral code. The signup link had the matching placeholder, `'https://...'`.
 *
 * **3. A subscription status that overrode the server's own.**
 * `billing?.subscription_status === 'active' || Boolean(billing?.autoRenew)
 * || Boolean(billing?.plan && billing.plan !== 'free')` — `autoRenew` is not a key on that
 * response (the lifecycle keys are `subscription_status`, `renewal_date` and
 * `cancel_at_period_end`), and the third clause means **any non-free plan reported
 * *Active* whatever the server said**, so a `past_due` or `canceled` Pro subscription
 * rendered Active with a green dot.
 *
 * **4. A plan name, and a renewal cycle, and an account status, that were literals.**
 * `planDisplay` fell back to `billing?.name` — also not a key — and then to the string
 * `'Free Tier'`, so a paid account whose billing read failed was told it was on the free
 * tier; the same value drove the `TIER:` badge in the page header. The renewal line fell
 * back to *Standard 30-day Cycle*, a billing-cycle claim with nothing behind it. And an
 * *ACCOUNT ACTIVE* pill with a green dot, and a *PROTECTED* pill with a tick, were both
 * static JSX.
 *
 * **5. A trading environment nothing reports.** The third automation tile rendered
 * *Live + Paper* and, beneath it, a green dot reading *Isolated*. `design/semantic.js:236`
 * is the rule this breaks from both directions at once: defaulting to LIVE is alarmist,
 * defaulting to PAPER is dangerous, and this claimed both plus an isolation guarantee.
 *
 * **6. Six `|| 0` and two `?? 0`-shaped substitutions on counts and money.**
 * `stats?.active_bots || 0`, `stats?.total_strategies || 0`, `stats?.total_trades || 0`,
 * `(stats?.total_pnl || 0).toFixed(2)`, `referral.total_referrals || 0`,
 * `referral.active_referrals || 0`, `(referral.pending_earnings || 0).toFixed(2)`,
 * `(referral.lifetime_earnings || 0).toFixed(2)`. A genuine `0` is a reading and still
 * renders `0`; what these did was make a failed read publish the same figure.
 *
 * **7. Three literals on a security audit record.** `ip_address || 'ip' || '127.0.0.1'`
 * put a localhost address on an audit row, `created_at ? … : 'Active Session'` answered
 * *when were you last here* with a reassurance, and each row's time fell back to *Recent*.
 * `pages/SecurityLogs.jsx` carried the same three and task 7.4 removed them there; the
 * same table was being read twice and only one reader had been fixed.
 *
 * WHAT IS DELIBERATELY NOT DECLARED HERE
 * -------------------------------------
 * `features` is a LIST, and `design/reported.js`'s `isReadableValue` refuses arrays by
 * design — a `Reported<T>` is a scalar. The page guards it with `Array.isArray` the way
 * `pages/SecurityLogs.jsx` guards its rows, and the absence of the capability chips is
 * visible as the absence of the capability chips.
 *
 * The seven notification switch POSITIONS are not declared either, and that is the same
 * decision `pages/RiskSettings.jsx` recorded for its three range controls: a switch must
 * have a position before the read lands, and the positions the page uses are
 * `NotificationSettingsRequest`'s own field defaults
 * (`backend_app/core/models/pydantic_models.py:521`–`:536`), not numbers chosen on the
 * page. A control's position and a reported figure are different things, and only the
 * second belongs in this declaration.
 */
const PROFILE_READ = 'userApi.getProfile';
const PROFILE_ENDPOINT = 'GET /api/user/profile';
const PROFILE_BILLING_READ = 'userApi.getBillingPlan';
const PROFILE_BILLING_ENDPOINT = 'GET /api/billing/plan';
const PROFILE_STATS_READ = 'userApi.getStats';
const PROFILE_STATS_ENDPOINT = 'GET /api/stats';
const PROFILE_SECURITY_READ = 'userApi.getSecurityLogs';
const PROFILE_SECURITY_ENDPOINT = 'GET /api/security/logs?limit=20';
const PROFILE_REFERRAL_READ = 'referralApi.getStats';
const PROFILE_REFERRAL_ENDPOINT = 'GET /api/referral/stats';
const USER_API_MODULE = 'src/api/modules/user.js';
const REFERRAL_API_MODULE = 'src/api/modules/referral.js';

/** The one sentence that is true of all four `/api/stats` figures, spelled once. */
const PLATFORM_STATS_BASIS =
  'This figure comes from the platform-wide statistics endpoint, which reports across all '
  + 'accounts rather than yours. Your own fleet and its results are on the dashboard and '
  + 'the strategies page.';

const PROFILE_FIELDS = [
  entry({
    page: PAGES.PROFILE,
    field: 'accountName',
    label: 'Account name',
    requirement: '4.2',
    read: PROFILE_READ,
    endpoint: PROFILE_ENDPOINT,
    inputs: ['display_name', 'username'],
    verdict: VERDICT.DERIVED,
    absence: ABSENCE.UNREPORTED,
    documentedIn: USER_API_MODULE,
    derivation: 'display_name when the profile carries one, otherwise username — the '
      + "page's own existing expression, minus its third arm. Both columns are optional on "
      + '`profiles`, so an account that has set neither has no name to show.',
    reason: 'You have not set a display name or a username yet, so there is no account '
      + 'name to show. Edit profile is where both are set.',
    note: 'The third arm was the literal `"Trader"`, which read as a name the account had '
      + 'been given rather than as the absence of one.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'username',
    label: 'Username',
    requirement: '4.2',
    read: PROFILE_READ,
    endpoint: PROFILE_ENDPOINT,
    path: 'username',
    absence: ABSENCE.UNREPORTED,
    documentedIn: USER_API_MODULE,
    reason: 'This account has no username set, so there is no handle to show. Edit '
      + 'profile is where one is chosen.',
    note: 'The page rendered `"unconfigured"` as an @-handle, which looks like a handle.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'email',
    label: 'Email address',
    requirement: '4.2',
    read: PROFILE_READ,
    endpoint: PROFILE_ENDPOINT,
    path: 'email',
    absence: ABSENCE.UNREPORTED,
    documentedIn: USER_API_MODULE,
    reason: 'Your profile record did not carry an email address. Your sign-in address is '
      + 'the one on your authentication record; contact support if this stays blank.',
    note: 'The page rendered the literal `"No email"` — a claim about the account rather '
      + 'than about the read, on the address every alert is dispatched to.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'emailVerified',
    label: 'Email verification',
    requirement: '4.2',
    read: PROFILE_READ,
    endpoint: PROFILE_ENDPOINT,
    inputs: ['email_confirmed_at'],
    verdict: VERDICT.DERIVED,
    absence: ABSENCE.UNMEASURABLE,
    derivation: 'Boolean(email_confirmed_at) — the PRESENCE of the confirmation timestamp, '
      + 'not the timestamp. A null timestamp is a genuine "not verified" and renders as '
      + 'one; only a profile that could not be read at all is an absence. The same '
      + 'argument as the `wizard/emailVerified` entry above.',
    reason: 'Your profile could not be read, so whether this address has been confirmed is '
      + 'unknown here.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'telegramHandle',
    label: 'Telegram dispatch',
    requirement: '4.2',
    read: PROFILE_READ,
    endpoint: PROFILE_ENDPOINT,
    inputs: ['telegram_id'],
    verdict: VERDICT.DERIVED,
    absence: ABSENCE.UNMEASURABLE,
    documentedIn: USER_API_MODULE,
    derivation: 'telegram_id, normalised to one leading @. A null is a genuine "not '
      + 'configured" and renders as those words, exactly as the email case above: '
      + 'collapsing it into the marker would turn a real state into an absence.',
    reason: 'Your profile could not be read, so whether Telegram dispatch is configured is '
      + 'unknown here.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'accountId',
    label: 'Account identifier',
    requirement: '4.2',
    read: PROFILE_READ,
    endpoint: PROFILE_ENDPOINT,
    path: 'id',
    absence: ABSENCE.UNREPORTED,
    documentedIn: USER_API_MODULE,
    reason: 'Your profile record did not carry an account identifier. Support needs this '
      + 'value, so quote the email address on the account instead.',
    note: '`profiles.id` is the primary key, so the marker is not expected. The page '
      + 'rendered `"Loading..."` in its place, which outlived the load.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'memberSince',
    label: 'Member since',
    requirement: '4.2',
    read: PROFILE_READ,
    endpoint: PROFILE_ENDPOINT,
    path: 'created_at',
    absence: ABSENCE.UNREPORTED,
    documentedIn: USER_API_MODULE,
    reason: 'Your profile record did not carry a creation date, so how long this account '
      + 'has existed is not shown here.',
    note: 'This is the one figure on the page the previous version did NOT substitute — it '
      + 'rendered the chip only when the timestamp was present. The entry exists so the '
      + 'path and the reason are written down with the rest, and so the chip now says why '
      + 'it is blank instead of vanishing.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'accountRole',
    label: 'Account role',
    requirement: '4.2',
    read: PROFILE_READ,
    endpoint: PROFILE_ENDPOINT,
    path: 'role',
    absence: ABSENCE.UNREPORTED,
    documentedIn: USER_API_MODULE,
    reason: 'This account\'s role was not reported. It does not affect what you can trade; '
      + 'it is the label support uses.',
    note: '`routers/user.py:46` fills `role` from the auth token when the profiles row has '
      + 'none, so the marker is not expected. The page rendered the literal `"USER"`.',
  }),

  // ── GET /api/billing/plan ─────────────────────────────────────────────────
  entry({
    page: PAGES.PROFILE,
    field: 'planName',
    label: 'Current tier',
    requirement: '4.2',
    read: PROFILE_BILLING_READ,
    endpoint: PROFILE_BILLING_ENDPOINT,
    path: 'plan',
    absence: ABSENCE.UNREPORTED,
    reason: 'Your plan could not be read, so it is not shown here rather than guessed. '
      + 'Manage subscription opens billing, which reads it again.',
    note: 'The page fell back to `billing?.name` — not a key on this response — and then '
      + 'to the literal `"Free Tier"`, so a PAID account whose billing read failed was '
      + 'told it was on the free tier, in the header badge as well as on the card. A '
      + 'genuine free plan still reads as the free tier; that is the reading the '
      + 'substitution was impersonating.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'subscriptionStatus',
    label: 'Subscription status',
    requirement: '4.2',
    read: PROFILE_BILLING_READ,
    endpoint: PROFILE_BILLING_ENDPOINT,
    path: 'subscription_status',
    absence: ABSENCE.UNREPORTED,
    reason: 'Your subscription status could not be read. Manage subscription opens '
      + 'billing, which is the authority on whether this plan is currently paid.',
    note: 'The page ORed the server\'s own status with `billing?.autoRenew` — not a key on '
      + 'this response — and with `plan !== "free"`, so ANY non-free plan reported '
      + '"Active" whatever the server said. A `past_due` or `canceled` subscription '
      + 'rendered Active with a green dot. The status is now the server\'s, verbatim.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'renewalDate',
    label: 'Renews',
    requirement: '4.2',
    read: PROFILE_BILLING_READ,
    endpoint: PROFILE_BILLING_ENDPOINT,
    path: 'renewal_date',
    absence: ABSENCE.UNREPORTED,
    reason: 'No next billing date is recorded against this account, so there is no renewal '
      + 'date to show. A plan with no scheduled renewal is not billed again until one is.',
    note: '`renewal_date` is `profiles.next_billing_date` and is null for an account with '
      + 'no scheduled renewal. The page rendered *Standard 30-day Cycle* instead — a '
      + 'billing-cycle claim with no read behind it.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'accountStatus',
    label: 'Account status',
    requirement: '4.2',
    read: PROFILE_READ,
    endpoint: PROFILE_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Nothing reports an account-level standing separately from your subscription, '
      + 'so this cannot be confirmed here. Your tier and its status are on the '
      + 'subscription panel and are what govern access.',
    note: 'The page header carried an *ACCOUNT ACTIVE* pill with a green dot as static '
      + 'JSX. Being able to open the page is not evidence that the account is in good '
      + 'standing, which is what that pill claimed.',
  }),

  // ── Security posture, and GET /api/security/logs ──────────────────────────
  entry({
    page: PAGES.PROFILE,
    field: 'mfaConfigured',
    label: 'Multi-factor authentication',
    requirement: '4.2',
    read: PROFILE_READ,
    endpoint: PROFILE_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Whether a second factor is enrolled is not reported by your profile, so it '
      + 'cannot be confirmed here. Manage MFA opens the authenticator page, which reads '
      + 'your current assurance level before it changes anything.',
    note: 'THE WORST SUBSTITUTION IN THIS SPEC. The tile rendered a green dot and the word '
      + '*Configured* as static JSX, with no read behind it, so an account with NO second '
      + 'factor was told its second factor was on — on the panel a trader opens to check '
      + 'exactly that. `supabase.auth.mfa.getAuthenticatorAssuranceLevel` is what answers '
      + 'this and `pages/Wizard.jsx` already calls it; this page never did. Requirement '
      + '16.2 keeps the tile and its Manage MFA control, Requirement 19.5 forbids filling '
      + 'it with a plausible value, and those two are only compatible if the tile explains '
      + 'itself.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'securityPosture',
    label: 'Security posture',
    requirement: '4.2',
    read: PROFILE_READ,
    endpoint: PROFILE_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Nothing scores this account\'s security posture, so there is no overall '
      + 'verdict to show. The events below are the record of what has actually happened.',
    note: 'The panel carried a *PROTECTED* pill with a tick, in green, as static JSX. It '
      + 'is the same class of claim as the MFA tile above and it sat directly beside it, '
      + 'so the two reinforced each other.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'lastAccessAt',
    label: 'Last access',
    requirement: '4.2',
    read: PROFILE_SECURITY_READ,
    endpoint: PROFILE_SECURITY_ENDPOINT,
    path: 'logs[].created_at',
    absence: ABSENCE.RETENTION,
    reason: 'No access event is recorded against this account yet, so there is no last '
      + 'access time to show. Sign-in and access events appear here once recorded.',
    note: 'The page rendered *Active Session* when the timestamp was missing — answering '
      + '"when were you last here" with a reassurance. `pages/SecurityLogs.jsx` carried '
      + 'the same fallback and task 7.4 removed it there; the same table is read twice.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'lastAccessIp',
    label: 'Last access IP',
    requirement: '4.2',
    read: PROFILE_SECURITY_READ,
    endpoint: PROFILE_SECURITY_ENDPOINT,
    path: 'logs[].ip_address',
    absence: ABSENCE.UNREPORTED,
    reason: 'No address was recorded against your most recent access event, so there is '
      + 'none to show. The address column is nullable on the audit record.',
    note: 'The page rendered `"127.0.0.1"` — a localhost address on an audit record, which '
      + 'reads as a claim that the session came from this machine.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'securityEventType',
    label: 'Event',
    requirement: '4.2',
    read: PROFILE_SECURITY_READ,
    endpoint: PROFILE_SECURITY_ENDPOINT,
    path: 'logs[].event_type',
    absence: ABSENCE.UNREPORTED,
    reason: 'This record did not name the event it describes. Review all logs opens the '
      + 'full audit trail, which carries every field the record holds.',
    note: '`security_logs.event_type` is `NOT NULL`, so the marker is not expected. The '
      + 'page read `log.event` and `log.action` before falling back to the literal '
      + '`"Security Event"`, and neither key exists on that table.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'securityEventAt',
    label: 'Recorded',
    requirement: '4.2',
    read: PROFILE_SECURITY_READ,
    endpoint: PROFILE_SECURITY_ENDPOINT,
    path: 'logs[].created_at',
    absence: ABSENCE.UNREPORTED,
    reason: 'This record did not carry a time, so there is none to show beside it.',
    note: 'The page rendered the literal `"Recent"`, which reads as a statement about when '
      + 'the event happened.',
  }),

  // ── GET /api/stats — the platform-wide endpoint ───────────────────────────
  entry({
    page: PAGES.PROFILE,
    field: 'platformActiveBotCount',
    label: 'Active bots (platform)',
    requirement: '4.2',
    read: PROFILE_STATS_READ,
    endpoint: PROFILE_STATS_ENDPOINT,
    path: 'active_bots',
    absence: ABSENCE.UNREPORTED,
    documentedIn: USER_API_MODULE,
    reason: 'The platform statistics read did not report a running-bot count, so there is '
      + 'no figure to show. Your own deployments are on the strategies page.',
    tooltip: PLATFORM_STATS_BASIS,
    note: 'The page read `stats?.active_bots || 0` and labelled it ACTIVE BOTS on a card '
      + "titled *Automation Account Context*, so the platform's count was presented as the "
      + "trader's own fleet. `main.py:938` declares this endpoint with no "
      + '`Depends(get_current_user)` and calls the aggregation service with the literal '
      + 'user id `"public"`. A genuine `0` is a reading and renders `0`.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'platformStrategyCount',
    label: 'Strategies (platform)',
    requirement: '4.2',
    read: PROFILE_STATS_READ,
    endpoint: PROFILE_STATS_ENDPOINT,
    path: 'total_strategies',
    absence: ABSENCE.UNREPORTED,
    documentedIn: USER_API_MODULE,
    reason: 'The platform statistics read did not report a strategy count, so there is no '
      + 'figure to show. Your own strategies are on the strategies page.',
    tooltip: PLATFORM_STATS_BASIS,
    note: 'Read as `stats?.total_strategies || 0` and labelled *Total Strategies* inside '
      + 'an account panel.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'platformTradeCount',
    label: 'Trades (platform)',
    requirement: '4.2',
    read: PROFILE_STATS_READ,
    endpoint: PROFILE_STATS_ENDPOINT,
    path: 'total_trades',
    absence: ABSENCE.UNREPORTED,
    documentedIn: USER_API_MODULE,
    reason: 'The platform statistics read did not report a trade count, so there is no '
      + 'figure to show. Your own fills are in trade history.',
    tooltip: PLATFORM_STATS_BASIS,
    note: 'Read as `stats?.total_trades || 0` and labelled *Trades* inside an account '
      + 'panel.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'platformTodayPnl',
    label: "Today's P&L (platform)",
    requirement: '4.2',
    read: PROFILE_STATS_READ,
    endpoint: PROFILE_STATS_ENDPOINT,
    path: 'total_pnl',
    absence: ABSENCE.UNREPORTED,
    documentedIn: USER_API_MODULE,
    reason: 'The platform statistics read did not report a P&L figure, so there is none to '
      + 'show. Your own realised and unrealised P&L are on the portfolio page.',
    tooltip: 'Today\'s P&L across all accounts on the platform, not yours. The response '
      + 'key is spelled `total_pnl` but `main.py:948` fills it from the aggregation '
      + "service's `today_pnl`, so the period is one day.",
    note: 'THE FIGURE WAS WRONG ABOUT ITS OWN PERIOD AS WELL AS WHOSE IT WAS. The page '
      + 'rendered `$${(stats?.total_pnl || 0).toFixed(2)}` under the label *TOTAL PNL*, '
      + 'and coloured it green or red from the same substituted value — so a failed read '
      + 'published `$0.00` in profit green. A genuine `0` is a reading and renders '
      + '`$0.00` without the substitution behind it.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'tradingEnvironment',
    label: 'Trading environment',
    requirement: '4.2',
    read: PROFILE_READ,
    endpoint: PROFILE_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'Which environments this account can trade in is not reported here, so it is '
      + 'not stated. Each deployment carries its own environment, and the badge on that '
      + 'screen is the one that governs whether real orders are placed.',
    note: 'The tile rendered *Live + Paper* and, beneath it, a green dot reading '
      + '*Isolated*. `design/semantic.js:236` is the rule this breaks from both '
      + 'directions at once — defaulting to LIVE is alarmist and defaulting to PAPER is '
      + 'dangerous — and it also asserted an isolation guarantee nothing measures.',
  }),

  // ── GET /api/referral/stats ───────────────────────────────────────────────
  entry({
    page: PAGES.PROFILE,
    field: 'referralCode',
    label: 'Referral code',
    requirement: '4.2',
    read: PROFILE_REFERRAL_READ,
    endpoint: PROFILE_REFERRAL_ENDPOINT,
    path: 'referral_code',
    absence: ABSENCE.UNREPORTED,
    documentedIn: REFERRAL_API_MODULE,
    reason: 'No referral code has been issued to this account yet, so there is none to '
      + 'share. One is created the first time the referral programme is used.',
    note: 'The page rendered `profile?.id?.substring(0, 8).toUpperCase()` when the code '
      + 'was missing — the first eight characters of the account\'s own primary key — and '
      + 'the copy button copied the same expression, in a field whose entire purpose is '
      + 'to be handed to other people. The third arm was the literal `"..."`.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'referralLink',
    label: 'Signup link',
    requirement: '4.2',
    read: PROFILE_REFERRAL_READ,
    endpoint: PROFILE_REFERRAL_ENDPOINT,
    path: 'referral_link',
    absence: ABSENCE.UNREPORTED,
    documentedIn: REFERRAL_API_MODULE,
    reason: 'No signup link has been issued to this account yet, so there is none to copy. '
      + 'It is created alongside your referral code.',
    note: 'The page rendered the placeholder `"https://..."`, which a copy button beside '
      + 'it would happily have copied.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'totalReferralCount',
    label: 'Total',
    requirement: '4.2',
    read: PROFILE_REFERRAL_READ,
    endpoint: PROFILE_REFERRAL_ENDPOINT,
    path: 'total_referrals',
    absence: ABSENCE.UNREPORTED,
    documentedIn: REFERRAL_API_MODULE,
    reason: 'The referral read did not report a total, so there is no count to show. A '
      + 'genuine zero means nobody has signed up through your link yet.',
    note: 'Read as `referral.total_referrals || 0`, which made "nobody yet" and "we could '
      + 'not read this" the same figure.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'activeReferralCount',
    label: 'Active',
    requirement: '4.2',
    read: PROFILE_REFERRAL_READ,
    endpoint: PROFILE_REFERRAL_ENDPOINT,
    path: 'active_referrals',
    absence: ABSENCE.UNREPORTED,
    documentedIn: REFERRAL_API_MODULE,
    reason: 'The referral read did not report how many of your referrals hold a live '
      + 'subscription, so there is no count to show.',
    note: 'Read as `referral.active_referrals || 0`.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'pendingEarningsUsd',
    label: 'Pending',
    requirement: '4.2',
    read: PROFILE_REFERRAL_READ,
    endpoint: PROFILE_REFERRAL_ENDPOINT,
    path: 'pending_earnings',
    absence: ABSENCE.UNREPORTED,
    documentedIn: REFERRAL_API_MODULE,
    reason: 'Your pending commission balance could not be read, so it is not shown rather '
      + 'than guessed. A genuine zero means nothing is currently awaiting approval.',
    note: 'Read as `(referral.pending_earnings || 0).toFixed(2)`, so a failed read '
      + 'published `$0.00` — a balance. This is `pages/Billing.jsx`\'s hazard on a '
      + 'smaller figure: a money field that substitutes zero states an amount owed.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'lifetimeEarningsUsd',
    label: 'Lifetime',
    requirement: '4.2',
    read: PROFILE_REFERRAL_READ,
    endpoint: PROFILE_REFERRAL_ENDPOINT,
    path: 'lifetime_earnings',
    absence: ABSENCE.UNREPORTED,
    documentedIn: REFERRAL_API_MODULE,
    reason: 'Your lifetime commission total could not be read, so it is not shown rather '
      + 'than guessed. A genuine zero means nothing has been earned yet.',
    note: 'Read as `(referral.lifetime_earnings || 0).toFixed(2)`.',
  }),
  entry({
    page: PAGES.PROFILE,
    field: 'commissionRatePct',
    label: 'Commission rate',
    requirement: '4.2',
    read: PROFILE_REFERRAL_READ,
    endpoint: PROFILE_REFERRAL_ENDPOINT,
    verdict: VERDICT.UNAVAILABLE,
    absence: ABSENCE.UNREPORTED,
    reason: 'The commission rate is not reported with your referral figures, so it is not '
      + 'stated here. Each commission below carries the amount it actually earned.',
    note: 'The panel carried a *20% RECURRING* pill as static JSX. '
      + '`ReferralStatsResponse` reports counts, balances and history and no rate, so the '
      + 'number was a marketing claim rendered as a reading.',
  }),
];

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * Billing (retail-ui-simplification Requirement 4.2) — /app/billing
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Not one of §7's pages. It is here for the reason `PROFILE` and `WIZARD` are, with one
 * thing on top: **every figure on it is money, a period, or an entitlement the trader has
 * paid for.** design.md §6 is written about this class of field and names this page as the
 * one where collapsing a reported zero into an absence "would be most plausible and most
 * expensive". A ₹0 invoice line and an invoice line nobody could read are different facts,
 * and before this declaration they rendered as the same four characters.
 *
 * THE FOUR READS, AND WHAT EACH ONE REALLY ANSWERS
 * ----------------------------------------------
 *   GET /api/billing/plans           `PricingService.get_localized_plans`. PUBLIC — no
 *                                    `Depends(get_current_user)` (`routers/billing.py:829`).
 *                                    It answers the CATALOGUE plus the viewer's pricing
 *                                    context (country, currency, symbol, FX). It says
 *                                    nothing about what this account is subscribed to.
 *   GET /api/billing/entitlements    `get_user_entitlements` plus three `profiles` columns.
 *                                    This is the only read that knows the account's plan.
 *   GET /api/billing/invoices        `billing_invoices` rows, newest first.
 *   GET /api/billing/payment-methods the stored cards.
 *
 * The split matters because the page merged them: a plan NAME was capitalised out of
 * `entitlements.plan` while the PRICE beside it came from the catalogue, so a failed
 * entitlements read and a free account produced the same header.
 *
 * WHAT WAS SUBSTITUTED — TEN KINDS, ON A MONEY SURFACE
 * --------------------------------------------------
 * Each one is an entry below with its real source path or its explicit unavailability.
 * The short version, so a reviewer knows what the diff is removing:
 *
 *   1. `currentPlan?.name || "Free"` — the same shape `pages/Profile.jsx` carried as
 *      `'Free Tier'`, one page along, and this is the page Profile's *Manage subscription*
 *      control sends the trader to in order to find out. A PAID account whose entitlements
 *      read failed was told it was on the free plan.
 *   2. `subscription_status` defaulted to `"active"` THREE times over — the initial state,
 *      `data.subscription_status || "active"`, and `…?.toUpperCase() || "ACTIVE"` at the
 *      render. So a failed read rendered a green *ACTIVE* pill, and `past_due` was
 *      indistinguishable from "we could not read it".
 *   3. Because 1 and 2 also fed `isFreePlan` and `isPaymentFailed`, a failed read TOOK THE
 *      CONTROLS AWAY as well: no *Cancel*, no *Resume*, no *Manage billing*, and no
 *      payment-failure banner for an account that was actually past due.
 *   4. *Cancels at period end* — a period claim rendered when `renewal_date` was null.
 *   5. Eight `|| 0`s over `usage.*` and `quotas.*`. **The free plan's real quotas for bots,
 *      ML trainings and marketplace publishing are all literally `0`**
 *      (`subscription_engine.py:86`–`:90`), so on this page the fabricated zero and the
 *      genuine zero were the same pixels for the same trader — §6's hazard exactly.
 *   6. `formatPlanPrice` returned `` `${currencySymbol}0` `` for a missing plan and used
 *      `localized_price !== undefined ? … : 0` for a missing price, so an unreadable price
 *      advertised itself as free. `pages/Wizard.jsx` had the same defect through different
 *      keys and task 7.6 removed it there; the two pages priced the same catalogue.
 *   7. `decimals` was recomputed in the page — `currency === "JPY" || currency === "KRW" ?
 *      0 : 2` — when `plans[].decimals` is a real key on the same object (Requirement
 *      16.4 forbids deriving client-side what the server reports).
 *   8. `` `Billed as $${p.checkout_price || p.base_price} ${p.checkout_currency}` `` — a
 *      HARDCODED `$` in front of an amount the same line labels as another currency, e.g.
 *      `$2499 INR`. `plans[].checkout_currency_symbol` is a real key. The `|| p.base_price`
 *      arm also swapped in a USD figure for a genuine `0`.
 *   9. The invoice amount: `inv.currency === "INR" ? ₹amtINR : $amtUSD`, so **every invoice
 *      in any currency other than INR rendered with a dollar sign** whatever it was
 *      denominated in — and both arms were `|| 0`, so an unreadable amount rendered as
 *      `$0`, a settled invoice for nothing.
 *  10. The pricing context was defaulted to `"USD"` / `"$"` / `"US"` / `"United States"` /
 *      `"ip"`, so a failed catalogue read rendered *United States (USD)* under an
 *      *Auto-detected* chip — a geolocation claim for a lookup that never happened.
 *
 * WHAT IS DELIBERATELY NOT DECLARED HERE
 * -------------------------------------
 * `plans[].features` is a LIST and `design/reported.js`'s `isReadableValue` refuses arrays;
 * `plans[].recommended` selects a treatment rather than reporting a figure. Both are
 * `Wizard.jsx`'s precedent, recorded there for the same reasons. The 21-entry
 * `supportedCurrencies` fallback is a CONTROL'S OPTION SET — reference data for a
 * `<button>` list, superseded by the response's own `supported_currencies` — and a
 * control's options and a reported figure are different things, which is the distinction
 * `pages/Profile.jsx` recorded for `NotificationSettingsRequest`'s switch defaults.
 * `usage` / `quotas` percentage bars carry no figure the two declared Metrics beside them
 * do not: the bar renders only when BOTH are available, because a 0%-wide bar for an
 * unread quota is the same fabrication as a `0`.
 */
const BILLING_PLANS_READ = 'billingApi.getPlans';
const BILLING_PLANS_ENDPOINT = 'GET /api/billing/plans';
const BILLING_ENTITLEMENTS_READ = 'billingApi.getEntitlements';
const BILLING_ENTITLEMENTS_ENDPOINT = 'GET /api/billing/entitlements';
const BILLING_INVOICES_READ = 'billingApi.getInvoices';
const BILLING_INVOICES_ENDPOINT = 'GET /api/billing/invoices';
const BILLING_METHODS_READ = 'billingApi.getPaymentMethods';
const BILLING_METHODS_ENDPOINT = 'GET /api/billing/payment-methods';

/** Every quota figure's absence says the same thing, because it has the same cause. */
const QUOTA_UNREAD = 'Your entitlements could not be read, so this allowance is not shown '
  + 'rather than guessed. Refresh reads them again.';

const BILLING_FIELDS = [
  // ── GET /api/billing/entitlements — the account's own subscription ────────
  entry({
    page: PAGES.BILLING,
    field: 'planName',
    label: 'Current plan',
    requirement: '4.2',
    read: BILLING_ENTITLEMENTS_READ,
    endpoint: BILLING_ENTITLEMENTS_ENDPOINT,
    path: 'plan',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'Your current plan could not be read, so it is not shown here rather than '
      + 'guessed. The plans below are the catalogue, not what you hold; Refresh reads your '
      + 'own subscription again.',
    note: 'Read as `currentPlan?.name || "Free"`, over a `currentPlan` the page only ever '
      + 'built inside `if (entitlementsRes.status === "fulfilled" && data.plan)` — so a 500, '
      + 'a rejected promise and a body without a plan all rendered the word "Free" on a '
      + 'paid account. `pages/Profile.jsx` carried the same substitution as "Free Tier" and '
      + 'its *Manage subscription* control points HERE, so a trader checking the claim was '
      + 'shown it twice. The display name is the plan id capitalised, which matches '
      + '`SubscriptionEngine`\'s own names ("free" → "Free", "pro" → "Pro"); a genuinely '
      + 'free account still reads Free, and that is the reading the fallback impersonated.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'subscriptionStatus',
    label: 'Subscription status',
    requirement: '4.2',
    read: BILLING_ENTITLEMENTS_READ,
    endpoint: BILLING_ENTITLEMENTS_ENDPOINT,
    path: 'subscription_status',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'Your subscription status could not be read. Manage billing opens the payment '
      + 'provider\'s portal, which is the authority on whether this plan is currently paid.',
    note: 'DEFAULTED TO "active" THREE TIMES: `useState("active")`, '
      + '`data.subscription_status || "active"`, and `…?.toUpperCase()?.replace("_", " ") '
      + '|| "ACTIVE"` at the chip. A failed read therefore rendered a green ACTIVE pill, and '
      + 'the same value fed `isPaymentFailed`, so an account that really was `past_due` got '
      + 'no payment-failure banner either. `profiles.subscription_status` is the server\'s '
      + 'own column and is now rendered verbatim through `ds/StatusBadge`.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'renewalDate',
    label: 'Renews',
    requirement: '4.2',
    read: BILLING_ENTITLEMENTS_READ,
    endpoint: BILLING_ENTITLEMENTS_ENDPOINT,
    path: 'renewal_date',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'No next billing date is recorded against this account, so there is no renewal '
      + 'date to show. A plan with no scheduled renewal is not billed again until one is set.',
    note: '`profiles.next_billing_date`, null for an account with no scheduled renewal. The '
      + 'cancellation line read `renewalDate ? fmtDate(renewalDate) : "at period end"`, so a '
      + 'missing date became a claim about WHEN access ends — and `fmtDate` itself answered '
      + '"N/A", a marker with no reason, which Requirement 19.3 is the other half of.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'cancelAtPeriodEnd',
    label: 'Cancellation scheduled',
    requirement: '4.2',
    read: BILLING_ENTITLEMENTS_READ,
    endpoint: BILLING_ENTITLEMENTS_ENDPOINT,
    path: 'cancel_at_period_end',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'Whether a cancellation is already scheduled could not be read, so neither '
      + 'Cancel nor Resume is offered on a guess. Manage billing opens the provider\'s '
      + 'portal, which reports the current schedule.',
    note: '`bool(profiles.cancel_at_period_end)`, so `false` is a reading and means the '
      + 'plan renews. It selects between the Cancel and Resume controls, which is why an '
      + 'absence has to be a state rather than a default: `|| false` offered Cancel to an '
      + 'account that had already cancelled.',
  }),

  // ── GET /api/billing/entitlements — the four quota pairs ──────────────────
  //
  // Eight entries and not four, because `usage.x` and `quotas.x` are two readings from two
  // objects and either can be absent on its own. The free plan's real `bots`,
  // `ml_trainings` and `marketplace_published` quotas are `0`, so on this page a genuine
  // zero allowance and an unread one were the same glyph — design.md §6's hazard on the
  // field where it is least visible. `-1` is the server's "unlimited" and renders `∞`.
  entry({
    page: PAGES.BILLING,
    field: 'strategiesUsed',
    label: 'Strategies used',
    requirement: '4.2',
    read: BILLING_ENTITLEMENTS_READ,
    endpoint: BILLING_ENTITLEMENTS_ENDPOINT,
    path: 'usage.strategies',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: QUOTA_UNREAD,
    note: 'Read as `usage.strategies || 0`. A genuine `0` means none saved yet.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'strategiesQuota',
    label: 'Strategies included',
    requirement: '4.2',
    read: BILLING_ENTITLEMENTS_READ,
    endpoint: BILLING_ENTITLEMENTS_ENDPOINT,
    path: 'quotas.strategies',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: QUOTA_UNREAD,
    note: 'Read as `quotas.strategies || 0`, which rendered the denominator of a paid '
      + 'allowance as zero whenever the read failed. `-1` is unlimited.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'botsUsed',
    label: 'Live bots used',
    requirement: '4.2',
    read: BILLING_ENTITLEMENTS_READ,
    endpoint: BILLING_ENTITLEMENTS_ENDPOINT,
    path: 'usage.bots',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: QUOTA_UNREAD,
    note: 'Read as `usage.bots || 0`. A genuine `0` means nothing is deployed.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'botsQuota',
    label: 'Live bots included',
    requirement: '4.2',
    read: BILLING_ENTITLEMENTS_READ,
    endpoint: BILLING_ENTITLEMENTS_ENDPOINT,
    path: 'quotas.bots',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: QUOTA_UNREAD,
    note: '**THE CLEAREST CASE OF §6 ON THIS PAGE.** The free plan\'s bot quota is really '
      + '`0` (`subscription_engine.py:87`), so `quotas.bots || 0` rendered the same "0" for '
      + '"your plan includes no live bots" and for "we could not read your plan" — to the '
      + 'same trader, on the same tile, beside a Subscribe button.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'mlTrainingsUsed',
    label: 'ML models used',
    requirement: '4.2',
    read: BILLING_ENTITLEMENTS_READ,
    endpoint: BILLING_ENTITLEMENTS_ENDPOINT,
    path: 'usage.ml_trainings',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: QUOTA_UNREAD,
    note: 'Read as `usage.ml_trainings || 0`. Reset monthly '
      + '(`subscription_engine.py:383`), so a genuine `0` is the normal state early in a '
      + 'billing period.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'mlTrainingsQuota',
    label: 'ML models included',
    requirement: '4.2',
    read: BILLING_ENTITLEMENTS_READ,
    endpoint: BILLING_ENTITLEMENTS_ENDPOINT,
    path: 'quotas.ml_trainings',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: QUOTA_UNREAD,
    note: 'Read as `quotas.ml_trainings || 0`. Free and Starter really are `0`, so the '
      + 'substitution was invisible on exactly the two plans a retail trader starts on.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'marketplacePublishedUsed',
    label: 'Marketplace listings used',
    requirement: '4.2',
    read: BILLING_ENTITLEMENTS_READ,
    endpoint: BILLING_ENTITLEMENTS_ENDPOINT,
    path: 'usage.marketplace_published',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: QUOTA_UNREAD,
    note: 'Read as `usage.marketplace_published || 0`.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'marketplacePublishedQuota',
    label: 'Marketplace listings included',
    requirement: '4.2',
    read: BILLING_ENTITLEMENTS_READ,
    endpoint: BILLING_ENTITLEMENTS_ENDPOINT,
    path: 'quotas.marketplace_published',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: QUOTA_UNREAD,
    note: 'Read as `quotas.marketplace_published || 0`. Enterprise is `-1`, the server\'s '
      + 'unlimited sentinel, which renders `∞` and is a reading like any other.',
  }),

  // ── GET /api/billing/plans — the viewer's pricing context ─────────────────
  //
  // This read is PUBLIC and says nothing about the account. Everything in this group
  // describes the money the CATALOGUE is quoted in, which is why a fabricated default here
  // is a claim about a lookup rather than about a subscription.
  entry({
    page: PAGES.BILLING,
    field: 'displayCurrency',
    label: 'Display currency',
    requirement: '4.2',
    read: BILLING_PLANS_READ,
    endpoint: BILLING_PLANS_ENDPOINT,
    path: 'currency',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'The currency this catalogue is priced in could not be read, so no denomination '
      + 'is stated rather than assumed. Every price below states its own currency.',
    note: 'Defaulted to `"USD"` in `useState`, so a failed catalogue read named a currency '
      + 'nobody resolved — and the page then quoted prices "in" it. The trader\'s explicit '
      + 'pick from the currency control overrides it, because that is a choice and not a '
      + 'reading.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'displayCurrencySymbol',
    label: 'Currency symbol',
    requirement: '4.2',
    read: BILLING_PLANS_READ,
    endpoint: BILLING_PLANS_ENDPOINT,
    path: 'currency_symbol',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'No currency glyph was reported for this catalogue, so the currency code is '
      + 'shown instead of a symbol that might belong to a different currency.',
    note: 'Defaulted to `"$"`, which is the single most consequential one-character '
      + 'substitution on the page: `FXService.get_currency_symbol` is what answers this, and '
      + 'a `$` in front of a rupee amount misstates the figure by roughly two orders of '
      + 'magnitude. The code is the fallback because a code cannot be misread as a symbol.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'billingRegion',
    label: 'Billing region',
    requirement: '4.2',
    read: BILLING_PLANS_READ,
    endpoint: BILLING_PLANS_ENDPOINT,
    path: 'country_name',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'Your billing region was not reported, so none is stated. It affects which '
      + 'currency prices are quoted in, not what you can trade.',
    note: 'Defaulted to `"United States"` beside a `"US"` code that nothing read. '
      + '`CountryDetector` resolves this from the request; a page that answers it from a '
      + 'literal is telling a trader where the server thinks they are without asking.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'currencySource',
    label: 'Currency source',
    requirement: '4.2',
    read: BILLING_PLANS_READ,
    endpoint: BILLING_PLANS_ENDPOINT,
    path: 'currency_source',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'Whether this currency was detected for you or saved as your preference was not '
      + 'reported, so neither is claimed. The currency control changes it either way.',
    note: 'Defaulted to `"ip"`, which the page rendered as an *Auto-detected* chip — so a '
      + 'failed read published the result of a geolocation that never ran. `"ip"` and '
      + 'anything else are both readings; the absence is not.',
  }),

  // ── GET /api/billing/plans — one catalogue card ───────────────────────────
  entry({
    page: PAGES.BILLING,
    field: 'cataloguePlanName',
    label: 'Plan',
    requirement: '4.2',
    read: BILLING_PLANS_READ,
    endpoint: BILLING_PLANS_ENDPOINT,
    path: 'plans[].name',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'This plan\'s name could not be read, so the card is not headed with a guess. '
      + 'Refresh reads the catalogue again.',
    note: '`SubscriptionEngine`\'s plan record always carries a name, so the marker is not '
      + 'expected — the render path makes it reachable anyway, because a card headed with '
      + '`undefined` beside a Subscribe button is worse than a card that says it could not '
      + 'read its own name.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'cataloguePlanDescription',
    label: 'Plan summary',
    requirement: '4.2',
    read: BILLING_PLANS_READ,
    endpoint: BILLING_PLANS_ENDPOINT,
    path: 'plans[].description',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'No summary was reported for this plan. The capabilities listed on the card are '
      + 'the plan\'s own and are unaffected.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'cataloguePlanPrice',
    label: 'Monthly price',
    requirement: '4.2',
    read: BILLING_PLANS_READ,
    endpoint: BILLING_PLANS_ENDPOINT,
    path: 'plans[].localized_price',
    inputs: ['plans[].currency', 'plans[].currency_symbol', 'plans[].decimals'],
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'This plan\'s price could not be read, so it is not shown here rather than '
      + 'guessed. Subscribe opens checkout, which prices the plan server-side and states '
      + 'the amount before anything is charged.',
    note: '`formatPlanPrice` returned `` `${currencySymbol}0` `` for a missing plan and '
      + 'substituted `0` for a missing `localized_price`, so **an unreadable price '
      + 'advertised itself as free** — the same defect `pages/Wizard.jsx` carried through '
      + '`p.inr`/`p.usd` and task 7.6 removed, on the same catalogue. A genuine `0` IS the '
      + 'free tier and still renders as a zero price. `decimals` comes from this object '
      + 'too: the page recomputed it as `currency === "JPY" || currency === "KRW" ? 0 : 2`, '
      + 'which is a client-side derivation of a money format the server reports '
      + '(Requirement 16.4) and which rounded every other zero-decimal currency to two.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'cataloguePlanCheckoutPrice',
    label: 'Charged at checkout',
    requirement: '4.2',
    read: BILLING_PLANS_READ,
    endpoint: BILLING_PLANS_ENDPOINT,
    path: 'plans[].checkout_price',
    inputs: [
      'plans[].checkout_currency',
      'plans[].checkout_currency_symbol',
      'plans[].is_direct_checkout',
    ],
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'The amount the payment provider will charge was not reported, so it is not '
      + 'stated here. Checkout states the charge in its own currency before you confirm.',
    note: 'Rendered as `` `Billed as $${p.checkout_price || p.base_price} '
      + '${p.checkout_currency}` `` — **a hardcoded dollar sign in front of an amount the '
      + 'same sentence labels as another currency.** `checkout_currency_symbol` is a real '
      + 'key on this object. The `|| p.base_price` arm was a second substitution on top: '
      + '`base_price` is USD by declaration (`pricing_service.py:186`), so a genuine `0` '
      + 'checkout amount was replaced with a figure in a different currency entirely.',
  }),

  // ── GET /api/billing/invoices ─────────────────────────────────────────────
  entry({
    page: PAGES.BILLING,
    field: 'invoiceReference',
    label: 'Invoice',
    requirement: '4.2',
    read: BILLING_INVOICES_READ,
    endpoint: BILLING_INVOICES_ENDPOINT,
    path: '[].id',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'This invoice carries no reference, so there is nothing to quote for it. The '
      + 'payment provider\'s portal lists the same invoices with their own references.',
    note: 'Rendered as `String(inv.id).slice(0, 8) + "..."` under `cursor: pointer` with no '
      + '`onClick` and no keyboard path — a truncated identifier dressed as a link. The '
      + 'truncation stays (design.md §2.5 mechanism 4 permits it for an IDENTIFIER) with '
      + 'the full reference on the element\'s `title`; the false affordance does not.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'invoiceDate',
    label: 'Date',
    requirement: '4.2',
    read: BILLING_INVOICES_READ,
    endpoint: BILLING_INVOICES_ENDPOINT,
    path: '[].date',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'This invoice carries no date, so none is shown. The rows are ordered newest '
      + 'first by the server whether or not each one states its date.',
    note: '`billing_invoices.created_at`. `fmtDate` answered the literal `"N/A"` for a '
      + 'missing value, which is a marker with no reason — the half Requirement 19.3 calls '
      + 'load-bearing.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'invoiceAmount',
    label: 'Amount',
    requirement: '4.2',
    read: BILLING_INVOICES_READ,
    endpoint: BILLING_INVOICES_ENDPOINT,
    inputs: ['[].currency', '[].amtINR', '[].amtUSD'],
    verdict: VERDICT.DERIVED,
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    derivation: 'The row carries TWO amounts and its own denomination, so the amount is '
      + 'selected by the currency rather than read from one path: `currency === "INR"` → '
      + '`amtINR`, `currency === "USD"` → `amtUSD`. **Any other currency has no matching '
      + 'column and is therefore an absence, not a dollar figure.**',
    reason: 'This invoice\'s amount is recorded in a currency this page has no column for, '
      + 'or was not reported at all, so no figure is shown rather than one in the wrong '
      + 'denomination. Manage billing opens the provider\'s portal, which holds the '
      + 'original invoice.',
    note: '`inv.currency === "INR" ? ₹${amtINR || 0} : $${amtUSD || 0}`. Two faults in one '
      + 'expression: the else arm put a **dollar sign in front of every non-INR invoice '
      + 'whatever it was denominated in**, and both arms substituted `0`, so an unreadable '
      + 'amount rendered as a settled invoice for nothing. `routers/billing.py:1085` fills '
      + 'both columns from `amount_usd` / `amount_inr` with a server-side `0` default of '
      + 'its own, which is a backend concern and out of scope here (Requirement 16.7) — '
      + 'what is in scope is that the page added a second one.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'invoiceStatus',
    label: 'Status',
    requirement: '4.2',
    read: BILLING_INVOICES_READ,
    endpoint: BILLING_INVOICES_ENDPOINT,
    path: '[].status',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'This invoice reports no status, so neither paid nor unpaid is claimed for it. '
      + 'The provider\'s portal is the authority on whether it settled.',
    note: 'The server defaults the column to `"unknown"`, which `design/semantic.js` '
      + 'already resolves to the neutral group — so an unknown status is a reading and is '
      + 'rendered as one rather than being coloured as a failure.',
  }),

  // ── GET /api/billing/payment-methods ──────────────────────────────────────
  entry({
    page: PAGES.BILLING,
    field: 'cardBrand',
    label: 'Card',
    requirement: '4.2',
    read: BILLING_METHODS_READ,
    endpoint: BILLING_METHODS_ENDPOINT,
    path: '[].brand',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'The card network on this payment method was not reported. Manage via the '
      + 'provider portal shows the stored method in full.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'cardLast4',
    label: 'Card ending',
    requirement: '4.2',
    read: BILLING_METHODS_READ,
    endpoint: BILLING_METHODS_ENDPOINT,
    path: '[].last4',
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    reason: 'The last four digits of this card were not reported, so the method cannot be '
      + 'identified here. It is unchanged either way, and the provider portal shows it.',
    note: 'The last four digits are the only part of a card number this application ever '
      + 'holds or renders; nothing on this page has access to the rest.',
  }),
  entry({
    page: PAGES.BILLING,
    field: 'cardExpiry',
    label: 'Expires',
    requirement: '4.2',
    read: BILLING_METHODS_READ,
    endpoint: BILLING_METHODS_ENDPOINT,
    inputs: ['[].expiry_month', '[].expiry_year'],
    verdict: VERDICT.DERIVED,
    absence: ABSENCE.UNREPORTED,
    documentedIn: BILLING_API_MODULE,
    derivation: '`${expiry_month}/${expiry_year}`, the page\'s own existing expression. '
      + 'BOTH parts are required: a month with no year, or a year with no month, is not an '
      + 'expiry date and is an absence rather than half a figure.',
    reason: 'This card\'s expiry date was not reported, so none is shown. A card that has '
      + 'expired is declined at the next charge whether or not the date is displayed here.',
    note: 'Rendered as `Expires {expiry_month}/{expiry_year}`, which put the literal '
      + '"Expires undefined/undefined" on screen when either part was missing.',
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
  ...SECURITY_LOGS_FIELDS,
  ...WIZARD_FIELDS,
  ...PROFILE_FIELDS,
  ...BILLING_FIELDS,
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
