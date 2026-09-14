/**
 * ═══════════════════════════════════════════════════════════════════════════
 * pages/LiveTrading — what is actually running, with real money (`/app/live-trading`)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 20.1 part A: the page shell, the route and tier 1.
 * design.md §7.5, §8.1, §8.2, §11.1. Requirements 1.3, 7.1, 7.4, 14.5, 19.3.
 *
 * Until this task `/app/live-trading` rendered `pages/Dashboard`, so the route existed and
 * answered a different question — the account's capital — than the one Requirement 7.1
 * asks: is this thing actually running, and against what?
 *
 * WHAT THIS PART IS, AND WHAT IT IS NOT
 * -------------------------------------
 * Tier 1 only. §7.5's tier 2 (the position, its P&L, its exposure and risk) and tier 3
 * (the latest signal, the latest order, the execution status) are declared in
 * `design/pageHierarchy.js` and are later parts of task 20.1; nothing here stubs them,
 * because a stub of a money figure is the fabrication Requirement 14.5 is about.
 *
 * TWO READS, ONE FAILURE STATE
 * ----------------------------
 * `GET /api/dashboard` carries the venue keys and the open positions; `GET /api/strategies`
 * carries the strategies and their symbols. Both go through `usePanelState`, which DROPS its
 * payload on failure — see its docblock's three inversions of `usePolling` — so no figure on
 * this page can be a value from a read that has since broken.
 *
 * Two reads and ONE page-level `ds/ErrorState`, rendered INSTEAD of the body. That is not a
 * simplification: tier 1's six figures are one statement about one running thing, and half a
 * statement about a real-money deployment is worse than none. Because the failure is a
 * branch and not a banner, no figure, marker or table exists in the DOM at all while either
 * read is broken (Requirement 14.5) — there is no markup left that could hold the other
 * read's payload under an error indicator. The retry re-issues both.
 *
 * FOUR THINGS THE DECLARATION RECORDS, AND THEY ARE THE POINT OF THE PAGE
 * ----------------------------------------------------------------------
 * `design/pageFields.js` is read, never restated: every label, every source path and every
 * not-available sentence below comes from `PAGE_FIELDS_BY_PAGE[PAGES.LIVE_TRADING]`, and the
 * order comes from `pageHierarchy`'s tier-1 list. Four of the six carry a caveat that this
 * page exists to honour rather than paper over:
 *
 *   1. **`account` is permanently UNAVAILABLE.** Its declared verdict is `UNAVAILABLE`, so
 *      {@link buildTierOne} renders the marker with the declared reason and reads no path at
 *      all. Nothing on either read reports which exchange account is trading:
 *      `exchange.exchanges[]` carries `{exchange_id, status, latency_ms, last_sync}`, which
 *      is the venue and not the account. A masked "…4821" assembled here would be an
 *      invented identifier for a real-money account, which is the one place in this app
 *      where a plausible-looking string is most expensive.
 *   2. **The connection figure and the socket state are two different facts, and each is
 *      labelled for what it is.** `exchange.exchanges[].status` is a hardcoded `"connected"`
 *      in the aggregation service, so the tier-1 figure confirms a key is CONFIGURED and not
 *      that a venue answered — {@link CONNECTION_HINT} says so on the figure itself.
 *      `useConnectionStatus()` is a fact about THIS BROWSER's socket to our own server; it
 *      is reported separately in the panel header, outside the tier container, under its own
 *      label. Neither is evidence for the other, and merging them into one green chip would
 *      manufacture exactly that evidence.
 *   3. **The strategy version is the strategy's CURRENT version.** The pair rendered is
 *      `strategies[].name` and `strategies[].version`, and nothing on either read says which
 *      version a running worker was bound to. So it is not labelled a deployed version —
 *      {@link STRATEGY_HINT} states what it is instead.
 *   4. **The environment is resolved ONLY from a server field** (§8.1). The declared path is
 *      `positions[].environment`; an absent label renders `ENVIRONMENT UNCONFIRMED` through
 *      `ds/TradingEnvironmentBadge`'s §8.2 null column, never a default. Defaulting to LIVE
 *      is alarmist and defaulting to PAPER is dangerous, and both are guesses.
 *
 * THE STRIP AND THE FIGURE ARE ALSO TWO DIFFERENT FACTS
 * ----------------------------------------------------
 * The full-width `ds/TradingEnvironmentBadge variant="strip"` under the header states what
 * the ROUTE is: this page reads the live ledger and the money is real (Requirement 1.3). The
 * tier-1 `Environment` figure states what the SERVER LABELLED the records it returned. They
 * are not the same claim and the second is not derived from the first — which is why a read
 * that comes back with no environment label leaves the strip saying LIVE and the figure
 * saying `ENVIRONMENT UNCONFIRMED`. That pair is informative, and collapsing it would
 * either hide a missing label or invent one.
 *
 * ONE FIGURE PER FIELD, OR THE MARKER — NEVER AN ARBITRARY PICK
 * ------------------------------------------------------------
 * Four of the six declared paths point INTO a list (`exchange.exchanges[]`,
 * `strategies[]`), and tier 1 has one slot per field. {@link reportedAcross} therefore
 * collects the DISTINCT values a list reports and {@link soleReport} renders a figure only
 * when there is exactly one of them. Nothing reported is the declared reason; several things
 * reported is {@link DISAGREEMENT_REASON}, because picking the first row would name one
 * venue, or one strategy, for a page that has no basis to prefer it. Neither arm is ever a
 * zero, an empty string or a guessed default.
 *
 * NO `C.` SHIM, NO COLOUR LITERAL, NO POLLING
 * ------------------------------------------
 * Every colour on this page comes from a `ds/` primitive or a token utility class, so
 * `no-colour-literals.budget.js` carries this file at 0 from its first commit and
 * `legacy-c.budget.js` carries no entry for it at all. There is no `usePolling` here for the
 * reason `Dashboard` gives: it lists `data` in its fetch dependency array, so its interval is
 * torn down and re-created on every tick (§1.12).
 *
 * @module pages/LiveTrading
 */

import { useCallback, useId, useMemo } from "react";
import { RefreshCw } from "lucide-react";

import { dashboardApi } from "../api/modules/dashboard";
// `endpoints.strategies.list` reached by module path rather than through the `endpoints`
// alias: `api/index.js` marks that alias deprecated and re-exports this exact object as
// `api.strategies`, so this is the same function with one fewer module graph pulled into
// this route's chunk (§26, and `Dashboard.jsx`'s import of `api/modules/dashboard`).
import { strategiesApi } from "../api/modules/strategies";
import { CommandButton } from "../components/ds/CommandButton";
import { ErrorState } from "../components/ds/ErrorState";
import { Metric } from "../components/ds/Metric";
import { PageHeader } from "../components/ds/PageHeader";
import { Panel } from "../components/ds/Panel";
import { TradingEnvironmentBadge } from "../components/ds/TradingEnvironmentBadge";
import { PAGES, PAGE_FIELDS_BY_PAGE, VERDICT } from "../design/pageFields";
import {
  PAGE_HIERARCHY_BY_PAGE,
  TIER_ATTRIBUTE,
  TIER_PAGE_ATTRIBUTE,
} from "../design/pageHierarchy";
import { available, unavailable } from "../design/reported";
import { useConnectionStatus } from "../hooks/useConnectionStatus";
import { PANEL_STATES, usePanelState } from "../hooks/usePanelState";

/* ══════════════════════════════════════════════════════════════════════════
 * THE DECLARATION — read, never restated
 * ══════════════════════════════════════════════════════════════════════════ */

const LIVE_TRADING_FIELDS = PAGE_FIELDS_BY_PAGE[PAGES.LIVE_TRADING] ?? [];

/** §7.5's tiers. This part renders the first of them and declares nothing about the others. */
const TIERS = PAGE_HIERARCHY_BY_PAGE[PAGES.LIVE_TRADING]?.tiers ?? [];

/**
 * §7.5's tier 1, in declaration order: connection, exchange, account, strategy (version),
 * market, environment. Requirement 7.1's row, and the order IS the requirement.
 */
const TIER_ONE = TIERS.filter((entry) => entry.tier === 1);

/** One field's declaration. */
const fieldEntry = (field) => LIVE_TRADING_FIELDS.find((entry) => entry.field === field) ?? null;

/** A declared field's not-available sentence (Requirement 19.3). */
const reasonOf = (field) => fieldEntry(field)?.reason ?? undefined;

/**
 * The environment field, named once.
 *
 * It is the one tier-1 slot that is not a `ds/Metric`: §8.1 and §8.2 put the LIVE/PAPER
 * distinction in `ds/TradingEnvironmentBadge`, which differs on all four of §8.2's axes and
 * owns the `ENVIRONMENT UNCONFIRMED` rendering for a record the server did not label.
 * Spelling that word into a `Metric` here would be a second copy of that vocabulary.
 */
const ENVIRONMENT_FIELD = "tradingEnvironment";

/** The strategy's version, which is an `inputs` entry of `strategy` rather than a field. */
const STRATEGY_VERSION_PATH = "strategies[].version";

/* ══════════════════════════════════════════════════════════════════════════
 * THE TWO CAVEATS THAT ARE RENDERED, NOT ONLY RECORDED
 * ══════════════════════════════════════════════════════════════════════════
 *
 * `pageFields` separates `tooltip` — copy a trader reads beside the figure — from `note`,
 * which is the developer record of why a field is sourced the way it is. Neither of these
 * two fields declares a `tooltip`, and their `note`s are written for a reader of the
 * declaration: one of them says "do not label it deployed version", which is an instruction
 * to this file and not a sentence for a trader. So the trader-facing form is authored here,
 * says the same thing, and claims nothing the notes do not.
 */

/** Why a `connected` reading is not a reachable venue. */
const CONNECTION_HINT =
  "The state the server reports for this exchange key. It confirms a key is configured, "
  + "not that the venue answered — no request is made to the exchange to produce it.";

/** Why the version beside the name is not the running worker's version. */
const STRATEGY_HINT =
  "The strategy's current name and version. It is not necessarily the version a running "
  + "worker was bound to — nothing on this page reports that. A strategy that reports no "
  + "version renders its name alone.";

/** How the environment is resolved, and what an absent label renders. */
const ENVIRONMENT_HINT =
  "Read from the environment the server labels each open position with. An absent or "
  + "inconsistent label renders ENVIRONMENT UNCONFIRMED rather than a default.";

/**
 * The reason a list-sourced figure is not shown when the list disagrees with itself.
 *
 * Not a declared reason because it is not an absence: the read answered, and it answered
 * with more than one value for a slot that holds one. Picking one would name a venue, or a
 * strategy, that this page has no basis to prefer over the others it was handed.
 *
 * @param {number} count
 * @returns {string}
 */
const DISAGREEMENT_REASON = (count) =>
  `This read reported ${count} different values for this field, so there is no single `
  + "reading to show. One picked from among them would be an arbitrary choice.";

/* ══════════════════════════════════════════════════════════════════════════
 * READING A DECLARED PATH
 * ══════════════════════════════════════════════════════════════════════════ */

/** The marker inside a declared path that says "one per element of this list". */
const LIST_MARKER = "[]";

/** A dotted path off a response body, or `undefined`. */
const readPath = (body, dottedPath) =>
  dottedPath.split(".").reduce(
    (node, key) => (node && typeof node === "object" ? node[key] : undefined),
    body,
  );

/**
 * A reported scalar as the string a trader reads, or `null`.
 *
 * A finite number becomes its own digits — a `version` of `3` is a reading, and rejecting it
 * for not being a string would turn a reported value into a marker. Everything else that is
 * not a non-blank string is `null`, which is how an absent field travels to the marker:
 * `0`-as-a-default and `""`-as-a-value both stop here (Requirement 14.5).
 *
 * @param {unknown} value
 * @returns {string|null}
 */
const scalarText = (value) => {
  if (typeof value === "string") return value.trim() === "" ? null : value.trim();
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : null;
  return null;
};

/**
 * The DISTINCT values a declared `a.b[].c` path reports, in first-seen order.
 *
 * The path is split on {@link LIST_MARKER} rather than retyped as a container plus a key, so
 * the declaration stays the only place either half is written down. A body that carries no
 * such list answers `[]`, which {@link soleReport} renders as the declared absence — not as
 * an empty figure.
 *
 * @param {unknown} body A resolved response body, or `null`.
 * @param {string} declaredPath A `pageFields` `path`, e.g. `exchange.exchanges[].status`.
 * @returns {string[]}
 */
const reportedAcross = (body, declaredPath) => {
  const [containerPath, leafPath] = declaredPath.split(LIST_MARKER);
  const list = readPath(body, containerPath);
  if (!Array.isArray(list)) return [];

  const leafKey = leafPath.replace(/^\./, "");
  const distinct = [];
  for (const row of list) {
    const value = scalarText(row && typeof row === "object" ? row[leafKey] : undefined);
    if (value !== null && !distinct.includes(value)) distinct.push(value);
  }
  return distinct;
};

/**
 * A list of distinct readings → the one figure, or the marker with the right sentence.
 *
 * @param {string[]} values
 * @param {string} field The declared field, for its own absence reason.
 * @returns {{available: boolean}} A `Reported<string>`.
 */
const soleReport = (values, field) => {
  if (values.length === 1) return available(values[0]);
  return unavailable(values.length === 0 ? reasonOf(field) : DISAGREEMENT_REASON(values.length));
};

/**
 * `strategies[].name` + `strategies[].version` → one reading, or the marker.
 *
 * The name is the declared path and the version is one of its declared `inputs`. The version
 * is only appended when the same read reports exactly one of those too: a name with a
 * version borrowed from a different strategy would be a fabricated pair, and a name on its
 * own is still a true reading of the field (see {@link STRATEGY_HINT}).
 *
 * @param {unknown} body
 * @param {{field: string, path: string}} entry The `strategy` declaration.
 * @returns {{available: boolean}}
 */
const readStrategyPair = (body, entry) => {
  const names = reportedAcross(body, entry.path);
  const report = soleReport(names, entry.field);
  if (!report.available) return report;

  const versions = reportedAcross(body, STRATEGY_VERSION_PATH);
  return versions.length === 1 ? available(`${report.value} · v${versions[0]}`) : report;
};

/**
 * The two payloads → tier 1's six `Reported<string>`s, keyed by declared field.
 *
 * Every branch is chosen by the DECLARATION rather than by a name in this function:
 *
 *   * `verdict: UNAVAILABLE` — `account`'s case — renders the marker with the declared
 *     reason and reads no path, because there is no path to read. Requirement 19.3's state
 *     is a rendered element with a sentence on it, and it keeps its place in the row.
 *   * `read: DASHBOARD_READ` picks the dashboard body; anything else picks the strategies
 *     body. That is the declaration's own `read` field, so a field re-sourced there moves
 *     here without an edit.
 *
 * A `null` payload — no read yet, or a read that failed and had its payload dropped — makes
 * every list empty and therefore every figure the marker. Never a zero and never a default.
 *
 * @param {unknown} dashboardBody A resolved `GET /api/dashboard` body, or `null`.
 * @param {unknown} strategiesBody A `{strategies: []}`-shaped body, or `null`.
 * @returns {Object<string, {available: boolean}>}
 */
const buildTierOne = (dashboardBody, strategiesBody) => {
  const model = {};
  for (const { key } of TIER_ONE) {
    const entry = fieldEntry(key);

    if (entry.verdict === VERDICT.UNAVAILABLE) {
      model[key] = unavailable(entry.reason);
      continue;
    }

    const body = entry.path.startsWith("strategies") ? strategiesBody : dashboardBody;
    model[key] = key === "strategy"
      ? readStrategyPair(body, entry)
      : soleReport(reportedAcross(body, entry.path), key);
  }
  return model;
};

/**
 * `GET /api/strategies` → the `{strategies: []}` shape the declared paths are written
 * against.
 *
 * The endpoint answers `{strategies, total, include_archived, archived_total}`, and
 * `pages/Strategies.jsx` and `pages/Backtester.jsx` both accept a bare array or a `data`
 * envelope from it as well. The same three shapes are accepted here and normalised to one,
 * so `strategies[].name` resolves whichever the server sent. An unrecognised shape becomes
 * an empty list, which renders the declared markers rather than an empty figure.
 *
 * @param {unknown} payload
 * @returns {{strategies: unknown[]}}
 */
const asStrategiesBody = (payload) => {
  if (Array.isArray(payload)) return { strategies: payload };
  if (payload && typeof payload === "object") {
    if (Array.isArray(payload.strategies)) return { strategies: payload.strategies };
    if (Array.isArray(payload.data)) return { strategies: payload.data };
  }
  return { strategies: [] };
};

/* ══════════════════════════════════════════════════════════════════════════
 * THE TWO READINGS THAT ARE NOT DECLARED FIELDS
 * ══════════════════════════════════════════════════════════════════════════ */

/** The failure states. One read failed means the page failed (see the module docblock). */
const isFailure = (state) =>
  state === PANEL_STATES.ERROR || state === PANEL_STATES.UNAUTHORISED;

/** A read that has not answered yet. */
const isReading = (state) =>
  state === PANEL_STATES.IDLE
  || state === PANEL_STATES.LOADING
  || state === PANEL_STATES.REFRESHING;

/**
 * This browser's socket state, in the panel header and OUTSIDE the tier container.
 *
 * `useConnectionStatus()` reports the client's own transport to our server, unmapped. It is
 * not one of §7.5's tier-1 fields and it is not evidence about a venue, so it gets neither a
 * tier nor a `data-region`: `data-region` is spelled as a `pageFields` key everywhere on this
 * page, and reusing it for a reading the declaration does not carry would make "the declared
 * element rendered" undecidable from the DOM. `data-client-reading` instead, following
 * `Dashboard`'s `data-health-reading`.
 *
 * The label names whose connection it is, because the failure this page is built to avoid is
 * a trader reading a healthy socket as a healthy exchange link.
 */
function ClientSocketReading({ status }) {
  const hintId = useId();
  const reading = scalarText(status);

  return (
    <div className="flex min-w-0 items-center gap-2" data-client-reading="browser-socket">
      <span
        className="text-micro uppercase tracking-wide text-content-secondary"
        aria-describedby={hintId}
      >
        This browser&apos;s stream
      </span>
      <span id={hintId} className="sr-only">
        The state of this browser&apos;s connection to our server. It is not a reading of any
        exchange connection.
      </span>
      <span className="font-mono text-micro font-bold uppercase tracking-wide text-content-primary">
        {reading ?? "not reported"}
      </span>
    </div>
  );
}

/**
 * The tier-1 environment slot.
 *
 * `ds/Metric`'s label typography with `ds/TradingEnvironmentBadge` where the figure goes —
 * see {@link ENVIRONMENT_FIELD} for why this one slot is not a `Metric`. It carries the same
 * `data-region` as its five neighbours, so document order over the six declared slots is
 * decidable from the DOM (Property 4's shape, task 20.4).
 */
function EnvironmentSlot({ label, environment, className = "" }) {
  const hintId = useId();

  return (
    <div
      className={`flex min-w-0 flex-col gap-1 ${className}`.trim()}
      data-region={ENVIRONMENT_FIELD}
    >
      <span
        className="cursor-help text-small uppercase tracking-wide text-content-secondary"
        title={ENVIRONMENT_HINT}
        aria-describedby={hintId}
      >
        {label}
      </span>
      <span id={hintId} className="sr-only">{ENVIRONMENT_HINT}</span>
      <span className="flex min-w-0 items-baseline">
        <TradingEnvironmentBadge environment={environment} variant="inline" />
      </span>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE PAGE
 * ══════════════════════════════════════════════════════════════════════════ */

/** Which tier-1 figures carry an authored hint, and what it says. */
const TIER_ONE_HINT = Object.freeze({
  connectionState: CONNECTION_HINT,
  strategy: STRATEGY_HINT,
});

export default function LiveTrading() {
  /*
   * THE TWO READS (§7.5).
   *
   * `usePanelState` and not `usePolling`, for the reason `Dashboard` gives: the hook drops
   * `data` on failure, so no figure can survive a read that has since broken, and its
   * `refetch` identity survives every payload. Neither reader takes an argument that a
   * control on this page selects, so both `deps` lists are empty — the question does not
   * change while the page is mounted.
   *
   * The environment is pinned to `live`. This route is the live ledger (Requirement 1.3);
   * it is not a ledger switch, and `positions[].environment` below reports what the server
   * labelled the records that came back, which is a different claim.
   */
  const readDashboard = useCallback(
    () => dashboardApi.getDashboard({ environment: "live" }),
    [],
  );
  const readStrategies = useCallback(() => strategiesApi.list(), []);

  /*
   * Both reads are destructured rather than held as objects, and the names say which read
   * each half came from. `Dashboard` does the same, for the same reason: `data` and `error`
   * mean different things on the two reads, and one identifier meaning "the body" in one
   * place and "the other body" in another is how a strategy's symbol ends up rendered as a
   * venue. It also keeps `retry` below depending on the two stable `refetch` identities
   * rather than on two objects that change on every state transition (§1.12).
   */
  const {
    data: exchangePayload,
    error: exchangeError,
    refetch: refetchExchange,
    state: exchangeState,
  } = usePanelState(readDashboard);
  const {
    data: strategiesPayload,
    error: strategiesError,
    refetch: refetchStrategies,
    state: strategiesState,
  } = usePanelState(readStrategies);

  /** This browser's socket, which is not a fact about any exchange. */
  const socketStatus = useConnectionStatus();

  const tierOne = useMemo(
    () => buildTierOne(exchangePayload, asStrategiesBody(strategiesPayload)),
    [exchangePayload, strategiesPayload],
  );

  /*
   * ONE FAILURE FOR TWO READS.
   *
   * `empty` is deliberately not a failure: a 2xx carrying no venue keys and no strategies is
   * a successful read of nothing, and it renders the six declared markers with their own
   * reasons rather than an error the server never reported.
   */
  const pageFailed = isFailure(exchangeState) || isFailure(strategiesState);
  const readError = isFailure(exchangeState) ? exchangeError : strategiesError;

  const busy = isReading(exchangeState) || isReading(strategiesState);

  /** Re-issue both reads. One statement, so a retry that fixed half of it would not help. */
  const retry = useCallback(() => {
    refetchExchange();
    refetchStrategies();
  }, [refetchExchange, refetchStrategies]);

  /**
   * Tier 1's panel state.
   *
   * `loading` until both reads have answered, `refreshing` while one is being re-read over
   * figures already on screen, `ready` otherwise. `error` is unreachable here on purpose:
   * the failure is the page-level branch below, and a panel-level error would imply this
   * panel owns a read of its own.
   */
  const unanswered = (state) =>
    state === PANEL_STATES.IDLE || state === PANEL_STATES.LOADING;
  const tierOneState = unanswered(exchangeState) || unanswered(strategiesState)
    ? PANEL_STATES.LOADING
    : (busy ? PANEL_STATES.REFRESHING : PANEL_STATES.READY);

  return (
    <div className="flex min-w-0 flex-col gap-4 overflow-y-auto bg-surface-canvas p-5 text-content-primary">

      {/* ═══ PAGE CHROME ══════════════════════════════════════════════════════════
          `PageHeader` renders the page's only `<h1>`, and the title is the ROUTE's title
          from `shell/navigation.js` rather than the name of anything that arrives from a
          request. No environment switch: this route is the live ledger. */}
      <PageHeader
        title="Live Trading"
        subtitle="What is running right now, and what it is running against"
        actions={(
          <CommandButton
            intent="secondary"
            icon={RefreshCw}
            loading={busy}
            loadingLabel="Refreshing"
            onClick={retry}
          >
            Refresh
          </CommandButton>
        )}
      />

      {/* ═══ THE LIVE STRIP (Requirement 1.3, §8.2) ═══════════════════════════════
          Full-width, immediately under the header, before anything a trader could act on.
          This states what the ROUTE is — the live ledger, real capital — and is the one
          instance on the page that announces, so a screen reader hears it once. The tier-1
          `Environment` figure below is a different claim; see the module docblock. */}
      <TradingEnvironmentBadge variant="strip" environment="LIVE" announce />

      {/* ═══ THE ONE FAILURE STATE (Requirement 14.5, §11.1) ══════════════════════
          Rendered INSTEAD of the body, so a broken read leaves no figure, no marker and no
          table in the DOM rather than an empty row that reads as "nothing is running".
          `ds/ErrorState` renders `translateError`'s output only, and offers the retry just
          when the failure is retryable — a 503 is, an expired session is not. */}
      {pageFailed ? (
        <ErrorState
          error={readError}
          context="live-trading"
          onRetry={retry}
          data-region="page-error"
        />
      ) : (
        /* ═══ TIER 1 — Requirement 7.1 ═══════════════════════════════════════════
            ONE container, six slots, walked from `pageHierarchy`'s tier-1 list — so a
            seventh figure cannot appear here without being declared, the order is the
            declaration's, and `Metric tier={1}` exists nowhere else on the page. The
            container carries `data-page` + `data-page-tier`, so both claims are decidable
            from the rendered DOM (task 20.4). */
        <Panel
          title="Execution attribution"
          state={tierOneState}
          loading={{ kind: "skeleton-metric", rows: 1, columns: 6 }}
          actions={<ClientSocketReading status={socketStatus} />}
          data-region="tier-1"
        >
          {/* Six equal-weight slots in ONE row: `flex-1` from a zero basis gives each the
              same width, which is what "one row of equally-weighted figures" means. Not
              `grid-cols-6` — Tailwind v4 generates a utility only where the source asks for
              it, and the built stylesheet carries bare `grid-cols-1…4` plus
              `lg:grid-cols-6`, so a bare `grid-cols-6` compiles to nothing and renders as
              though the attribute were absent (`dead-tailwind`, design.md §1.2). */}
          <div
            {...{ [TIER_PAGE_ATTRIBUTE]: PAGES.LIVE_TRADING, [TIER_ATTRIBUTE]: 1 }}
            className="flex min-w-0 items-start gap-4"
          >
            {TIER_ONE.map(({ key, label }) => (
              key === ENVIRONMENT_FIELD ? (
                <EnvironmentSlot
                  key={key}
                  label={label}
                  className="flex-1"
                  // The server's own label, or `null`. `ds/TradingEnvironmentBadge` renders
                  // `ENVIRONMENT UNCONFIRMED` for `null` and for anything outside §8.2's
                  // three named environments — never a guess (Requirement 7.4).
                  environment={tierOne[key].available ? tierOne[key].value : null}
                />
              ) : (
                <Metric
                  key={key}
                  tier={1}
                  label={label}
                  value={tierOne[key]}
                  // `raw`: every tier-1 field here is a name, a state word or a symbol.
                  // A numeric format would group and round text that is not a quantity.
                  format="raw"
                  hint={TIER_ONE_HINT[key] ?? fieldEntry(key)?.tooltip ?? undefined}
                  className="flex-1"
                  data-region={key}
                />
              )
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
