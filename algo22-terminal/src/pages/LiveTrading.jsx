/**
 * ═══════════════════════════════════════════════════════════════════════════
 * pages/LiveTrading — what is actually running, with real money (`/app/live-trading`)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 20.1: part A the page shell, the route and tier 1; part B the
 * tier-2 money row; part C the tier-3 activity row; part D the deployment selector above
 * tier 1. Task 20.3 adds the three destructive controls — stop the selected deployment,
 * cancel the named open order, cancel every open order at the venue — each behind a
 * `ds/ConfirmDialog`, with the acknowledgement on the bulk one ONLY. design.md §7.5, §8.1,
 * §8.2, §8.3, §8.4, §11.1.
 * Requirements 1.3, 7.1, 7.2, 7.3, 7.4, 7.6, 8.1, 8.3, 8.5, 12.2, 14.1, 14.4, 14.5, 19.1,
 * 19.3, 19.4.
 *
 * The page was READ-ONLY through 20.1 and is no longer. Everything about the three writes —
 * why the acknowledgement is asymmetric, why Property 13 is structural rather than a property
 * of a `confirm()` return value, which two client paths were wrong and how each route was
 * verified, and which control is withheld when its endpoint cannot be addressed — is in the
 * "THREE DESTRUCTIVE ACTIONS" section below, next to the code it governs.
 *
 * Until this task `/app/live-trading` rendered `pages/Dashboard`, so the route existed and
 * answered a different question — the account's capital — than the one Requirement 7.1
 * asks: is this thing actually running, and against what?
 *
 * WHAT THIS PAGE IS, AND WHAT THE SELECTOR CAN AND CANNOT RE-SCOPE
 * ---------------------------------------------------------------
 * All three of §7.5's tiers, plus the untiered deployment selector above them. Part D adds
 * that selector — `pageHierarchy` registers `deployment` with NO tier precisely because it
 * sits above tier 1 and chooses what the tiers describe — and it fixes exactly as much of
 * the attribution gap as the payloads allow. It does not fix all of it, and the split is the
 * point:
 *
 *   * **RE-SCOPED by a selection.** `strategy` and `market` are `strategies[].name` and
 *     `strategies[].symbol`, and `latestSignal` filters by `strategies[].id`. A selected
 *     deployment names the `strategy_id` it was read under, so {@link scopeStrategies}
 *     narrows the strategies body to that one strategy and the SAME tier-1 and tier-3
 *     builders then report "this deployment's strategy" instead of "the sole reported
 *     strategy". Nothing is renamed and no new source appears: the narrowing happens in the
 *     body, before {@link soleReport} ever sees it, so {@link DISAGREEMENT_REASON} keeps
 *     meaning what it meant.
 *   * **NOT re-scoped, and no attribution is invented.** `overview.today_realized_pnl` and
 *     `risk.risk_level` are account-wide sums and `positions[]` carries no deployment key at
 *     all, so tier 2 keeps its account-wide labels and hints with a deployment selected —
 *     word for word, unchanged. `connectionState`, `exchange` and `tradingEnvironment` come
 *     off the dashboard read per venue key and per position, not per deployment.
 *     `latestOrder` and `executionStatus` stay the account's at that venue. Attributing any
 *     of them to the selected row would be the fabrication Requirement 14.5 forbids, and it
 *     is the whole reason `realisedPnlPerDeployment` is declared UNAVAILABLE rather than
 *     computed.
 *
 * So a selection sharpens six words on the page and leaves nine figures exactly as they
 * were. That is a smaller change than the selector looks like it should make, and saying so
 * on the surface — in each account-wide slot's own hint — is what stops it from being read
 * as a per-deployment page.
 *
 * FOUR READS, ONE PAGE-LEVEL FAILURE AND TWO SLOT-LEVEL ONES
 * ---------------------------------------------------------
 * `GET /api/dashboard` carries the venue keys, the open positions, the signals and the
 * executions; `GET /api/strategies` carries the strategies, their ids and their symbols;
 * `GET /api/orders/open` carries one venue's open orders; and
 * `GET /api/strategies/{id}/deployments` carries the selector's rows, once per strategy (see
 * {@link readDeploymentUnion}). All four go through `usePanelState`, which DROPS its payload
 * on failure — see its docblock's three inversions of `usePolling` — so no figure on this
 * page can be a value from a read that has since broken.
 *
 * The first two share ONE page-level `ds/ErrorState`, rendered INSTEAD of the body. That is
 * not a simplification: tier 1's six figures are one statement about one running thing, and
 * half a statement about a real-money deployment is worse than none. Because the failure is a
 * branch and not a banner, no figure, marker or table exists in the DOM at all while either
 * read is broken (Requirement 14.5) — there is no markup left that could hold the other
 * read's payload under an error indicator.
 *
 * THE OPEN-ORDERS READ IS DELIBERATELY NOT IN THAT BRANCH, AND THIS IS THE DIFFERENCE
 * ----------------------------------------------------------------------------------
 * It feeds exactly ONE slot, `latestOrder`, and it is a request to a VENUE. The other two are
 * requests to our own server for the state of the running thing. So the reasoning that puts
 * them in one branch does not extend to it: if it did, a venue that 500s on
 * `fetch_open_orders` would erase the position, the unrealised P&L, the exposure and the risk
 * state — figures that were read successfully, about real money, seconds ago. Withholding a
 * true statement about an open position because a different endpoint failed is worse than the
 * page-level branch it would be imitating.
 *
 * So it fails in place: `latestOrder` renders its declared marker — "Open orders could not be
 * read for this exchange" — and its two neighbours, which come off the dashboard read, keep
 * reporting. There is no second `ds/ErrorState` and no second alert, because one slot's
 * failure already has exactly one rendering, and the header's Refresh re-issues all four
 * reads. Requirement 14.5 is satisfied the same way in both places: the figure is absent and
 * explained, never stale and never zero.
 *
 * AND NEITHER IS THE DEPLOYMENT UNION — FOR THE SAME REASON, ONE LEVEL DOWN
 * -----------------------------------------------------------------------
 * The union is N requests, one per strategy, and part C's precedent decides both questions it
 * raises. It is not in the page-level branch: a selector that could not be read must not erase
 * the position, the exposure and the risk state, which are true statements about real money
 * from a read that succeeded. And WITHIN the union, one strategy's failure does not erase the
 * others' rows — {@link readDeploymentUnion} settles every call and keeps what answered, then
 * says how many did not. The alternative, rejecting the whole union on the first failure,
 * would hide every deployment a trader has because one strategy's read timed out, which is
 * the same erasure at a smaller scale.
 *
 * That partial failure is DISCLOSED rather than absorbed: a `ds/Alert` above the table names
 * the strategies whose deployments could not be read, so a short list is never mistaken for a
 * complete one. Requirement 14.5's shape again — what is missing is missing, and it says why.
 *
 * THE SELECTOR'S LIST IS NOT A COMPLETE LIST, AND IT SAYS SO ON THE SURFACE
 * ------------------------------------------------------------------------
 * `strategiesApi.listDeployments`' docblock records the limitation in as many words: the
 * handler lists `deployment_manager`'s IN-PROCESS registry, so a deployment the current
 * backend process did not start — one from before a restart, or one `deploy_version` created
 * elsewhere — is not in it, and the endpoint can answer with fewer deployments than the
 * `strategy_deployments` table holds. Nothing on the client can repair that; the only honest
 * response is to stop the list from being read as exhaustive.
 *
 * So {@link REGISTRY_CAVEAT} is rendered ONCE, above the rows, in every state where there are
 * rows or an absence to explain, and it is written for a trader rather than for a reader of
 * this file: a deployment you expect and cannot find here is UNKNOWN, not stopped, so this
 * screen is not grounds for deploying it again. That last clause is the actionable half — the
 * cost of misreading an incomplete list on this page is a duplicate live deployment.
 *
 * It is a sentence and not a `ds/Alert`, deliberately. It is a permanent property of the read,
 * true on every load and on every account, and §11.1's alert vocabulary is for conditions.
 * An always-on warning banner is the one that gets dismissed by habit; the partial-failure
 * alert above it is a condition, and keeping the two visibly different is what lets the
 * conditional one still register.
 *
 * ONE DEFECT IN A READ PATH, FIXED HERE BECAUSE THIS TASK ADOPTS THE FIELD
 * -----------------------------------------------------------------------
 * `latestOrder`'s declaration recorded that `GET /api/orders/open` requires an `exchange_id`
 * QUERY PARAMETER — `routers/orders.py` declares it `Query(...)` with no default — while
 * `ordersApi.getOpenOrders(symbol)` sent only `symbol`, so the call answered 422 for every
 * caller. `api/modules/orders.js` now takes the venue as a second optional parameter and sends
 * it; `symbol` stays first, so nothing that called it positionally changed meaning. That is
 * the whole change on the read: one missing query parameter.
 *
 * Task 20.3 then found the same class of defect on the two CANCEL methods and corrected them
 * the same way — path, method and required parameters, nothing about what the endpoints do.
 * This page calls `cancelOrder` and `cancelAllOrders` from task 20.3 onwards, and it still
 * never calls `createOrder` or `updateOrder`: `POST /api/orders/execute` stays behind its
 * algo-only guard and this page adds no manual-order affordance of any kind (Requirement
 * 19.1). See the "THREE DESTRUCTIVE ACTIONS" section.
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

import { useCallback, useId, useMemo, useState } from "react";
import { RefreshCw, Server } from "lucide-react";

import { dashboardApi } from "../api/modules/dashboard";
// The third read, and the one that goes to a VENUE rather than to our own server. Its
// `getOpenOrders` gained the `exchange_id` the route requires as part of this task — see the
// module docblock's note on the defect `pageFields` recorded against this field.
import { ordersApi } from "../api/modules/orders";
// `endpoints.strategies.list` reached by module path rather than through the `endpoints`
// alias: `api/index.js` marks that alias deprecated and re-exports this exact object as
// `api.strategies`, so this is the same function with one fewer module graph pulled into
// this route's chunk (§26, and `Dashboard.jsx`'s import of `api/modules/dashboard`).
import { strategiesApi } from "../api/modules/strategies";
import { Alert } from "../components/ds/Alert";
import { CommandButton } from "../components/ds/CommandButton";
// The ONE confirmation surface (§5.1, §8.3). Task 20.3's three controls all go through it, and
// its own confirm handler is the only caller of the three mutations — see that section.
import { ConfirmDialog } from "../components/ds/ConfirmDialog";
import { DataTable } from "../components/ds/DataTable";
import { EmptyState } from "../components/ds/EmptyState";
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
import { available, fromNullable, unavailable } from "../design/reported";
import { useConnectionStatus } from "../hooks/useConnectionStatus";
import { PANEL_STATES, usePanelState } from "../hooks/usePanelState";
/*
 * `pages/Dashboard.jsx`'s two readings of the same payload, imported rather than copied.
 *
 * Both are about `GET /api/dashboard`, which is the read this page already makes, and both
 * are decisions that must not be made twice: `computeLiquidationDistance` is the derivation
 * `liquidationDistance` declares by name, and `readPositionsDegradation` is BC-2's marker,
 * which is the only thing that separates a flat account from a failed positions read. A
 * second copy of either would be a second chance to disagree with the page that already
 * renders them. Nothing else crosses this boundary: no component, no state, no layout.
 */
import { computeLiquidationDistance, readPositionsDegradation } from "./Dashboard";

/* ══════════════════════════════════════════════════════════════════════════
 * THE DECLARATION — read, never restated
 * ══════════════════════════════════════════════════════════════════════════ */

const LIVE_TRADING_FIELDS = PAGE_FIELDS_BY_PAGE[PAGES.LIVE_TRADING] ?? [];

/** §7.5's three tiers. The untiered deployment selector is declared there and rendered nowhere. */
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
 * TIER 2 — THE POSITION, ITS P&L, ITS EXPOSURE AND ITS RISK (Requirement 7.2)
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Seven declared slots, every one of them off the dashboard read this page already makes.
 * The four things this tier has to get right are all cases where a plausible-looking figure
 * would be worse than the marker:
 *
 *   1. **`positions: []` is not "flat".** It is also what a FAILED positions read answers,
 *      and BC-2's top-level `degraded` marker is the only thing that tells the two apart.
 *      {@link positionsReport} consults it BEFORE the list, exactly as `Dashboard`'s
 *      `openPositions` does, and renders the server's own sentence on that arm.
 *   2. **The realised-P&L pair.** `overview.today_realized_pnl` is an ACCOUNT-WIDE sum, so
 *      it is rendered under its declared label — "Realised P&L (account, today)" — and the
 *      per-deployment field beside it renders its own marker. See {@link buildTierTwo}.
 *   3. **The liquidation distance is derived, and its absence is classified.** A blank cell
 *      beside a leveraged position reads as "no liquidation risk", so
 *      {@link LIQUIDATION_ABSENCE} says WHICH absence this is — spot, paper, unpriced by the
 *      venue, or unmarked.
 *   4. **One figure per field, or the marker.** Five of the seven paths point into
 *      `positions[]` and this tier has one slot per field, so {@link soleReport} — tier 1's
 *      own function — renders a figure only where the list reports exactly one value. A sum
 *      would be a different quantity (`overview.total_exposure` is the account's, and it is
 *      declared as an INPUT rather than as this field's source), and a first row picked from
 *      several would name a position this page has no basis to prefer.
 */

/** §7.5's tier 2, in declaration order. */
const TIER_TWO = TIERS.filter((entry) => entry.tier === 2);

/** The one tier-2 field whose value is computed here rather than read. */
const LIQUIDATION_FIELD = "liquidationDistance";

/**
 * A declared `inputs` path, found by its LEAF rather than by its index.
 *
 * `inputs[1]` would silently become a different path the day the declaration gains an entry;
 * the leaf is the thing this file actually means.
 *
 * @param {string} field
 * @param {string} leaf e.g. `".mark_price"`
 * @returns {string|null}
 */
const inputPath = (field, leaf) =>
  fieldEntry(field)?.inputs?.find((path) => path.endsWith(leaf)) ?? null;

/** The leaf key of a declared `a.b[].c` path — `"c"`. */
const leafKeyOf = (declaredPath) =>
  (declaredPath ?? "").split(LIST_MARKER)[1]?.replace(/^\./, "") ?? "";

/** `positions[].side`, which is one of `position`'s declared inputs. */
const POSITION_SIDE_PATH = inputPath("position", ".side");

/**
 * The container every position-sourced tier-2 field reads out of, taken from a declared
 * path rather than typed here.
 */
const POSITIONS_PATH = (POSITION_SIDE_PATH ?? "positions[]").split(LIST_MARKER)[0];

const SIDE_KEY = leafKeyOf(POSITION_SIDE_PATH);
const MARK_PRICE_KEY = leafKeyOf(inputPath(LIQUIDATION_FIELD, ".mark_price"));
const LIQUIDATION_PRICE_KEY = leafKeyOf(inputPath(LIQUIDATION_FIELD, ".liquidation_price"));
const ENVIRONMENT_KEY = leafKeyOf(fieldEntry(ENVIRONMENT_FIELD)?.path);

/**
 * `market_type`, which `computeLiquidationDistance` needs and which no field on THIS page
 * declares — `pageFields`' `dashboard/openPositions` note carries it, under this name, in the
 * list of what `dashboard_aggregation_service` publishes per position.
 *
 * It is read and never defaulted, for the reason `Dashboard`'s projection gives: a market
 * type is what decides whether a position can be liquidated at all, so defaulting it to
 * `"spot"` would answer "no liquidation risk" for a leveraged position that reported none.
 */
const MARKET_TYPE_KEY = "market_type";

/** The precision the liquidation level is quoted to, as `Dashboard`'s own cell quotes it. */
const LIQUIDATION_PRECISION = 1;

/**
 * The rows of the positions list, or `[]`.
 *
 * `[]` is returned for an absent list AND for a list that is not one, so the length test in
 * {@link positionsReport} is a test of what the server sent rather than of its shape.
 */
const positionRows = (body) => {
  const list = readPath(body, POSITIONS_PATH);
  return Array.isArray(list) ? list.filter((row) => row && typeof row === "object") : [];
};

/**
 * A reported quantity, or `null` — {@link scalarText}'s discipline for a number.
 *
 * It goes through `scalarText` rather than around it so that `""`, `NaN`, `null` and an
 * object all stop in the same place they stop for every other field on this page. Only the
 * liquidation derivation needs numbers: everything else renders through `ds/Metric`, which
 * groups a numeric string without rounding it and so keeps the server's own trailing zeros.
 */
const numberOf = (value) => {
  const text = scalarText(value);
  if (text === null) return null;
  const numeric = Number(text);
  return Number.isFinite(numeric) ? numeric : null;
};

/**
 * The account holds nothing, and the read that says so SUCCEEDED.
 *
 * Authored rather than declared, because the declaration has one reason per field and this
 * is the other half of BC-2's distinction: the declared reasons ("The open positions could
 * not be read", "The position's notional exposure was not reported") describe a read that
 * did not deliver, and saying that to a trader whose account is simply flat would report a
 * failure that did not happen. The degraded arm gets the server's own sentence instead.
 */
const NO_POSITION_REASON =
  "The server reported no open position on this account, so there is no position figure to "
  + "show. This is a complete read: the positions read did not fail.";

/**
 * WHY a liquidation distance could not be computed — and it matters which.
 *
 * `liquidation_price` is `null` for every paper position, for every spot position, and for a
 * futures position the venue did not price. All three are legitimate PERMANENT absences
 * rather than failed reads, and they mean different things to a trader: a blank cell beside a
 * leveraged position reads as "no liquidation risk", which is the one reading that must not
 * be available here. The spot sentence is the declaration's own; the other three are authored
 * because the declaration carries one reason per field and this field has four.
 */
const LIQUIDATION_ABSENCE = Object.freeze({
  spot: reasonOf(LIQUIDATION_FIELD),
  paper: "This is a paper position. No venue holds it, so nothing can liquidate it.",
  unpriced:
    "The venue reported no liquidation price for this leveraged position, so the distance "
    + "cannot be computed. This is not a reading of no liquidation risk.",
  unmarked:
    "The venue reported no mark price for this position, so there is no price to measure "
    + "the liquidation level against.",
  sideless:
    "The position did not report a side, so which way its liquidation level sits from the "
    + "mark is unknown and a signed distance would be a guess.",
});

/**
 * One position row → which of {@link LIQUIDATION_ABSENCE} applies to it.
 *
 * Order is the point: a spot position in a paper account is a spot position first, and a
 * position with no side is unmeasurable whatever its prices say.
 *
 * @param {Object} row
 * @returns {string}
 */
const liquidationAbsenceOf = (row) => {
  const marketType = scalarText(row[MARKET_TYPE_KEY]);
  if (marketType !== null && marketType.toLowerCase() === "spot") {
    return LIQUIDATION_ABSENCE.spot;
  }
  const environment = scalarText(row[ENVIRONMENT_KEY]);
  if (environment !== null && environment.toLowerCase() === "paper") {
    return LIQUIDATION_ABSENCE.paper;
  }
  if (scalarText(row[SIDE_KEY]) === null) return LIQUIDATION_ABSENCE.sideless;
  if (numberOf(row[MARK_PRICE_KEY]) === null) return LIQUIDATION_ABSENCE.unmarked;
  return LIQUIDATION_ABSENCE.unpriced;
};

/**
 * One position row → its liquidation distance as a percentage of the mark, or `null`.
 *
 * `computeLiquidationDistance` is `Dashboard`'s, imported: it answers `null` for a spot
 * market, for an absent or non-positive liquidation level and for an absent mark, so a
 * distance is never computed against a price nobody reported.
 *
 * The one operand it does not guard is the side, which selects the SIGN of the subtraction —
 * an unreported side falls to its short branch and would render a negative distance for a
 * long position. So a row with no side is not passed to it at all.
 *
 * @param {Object} row
 * @returns {number|null}
 */
const liquidationOf = (row) => {
  const side = scalarText(row[SIDE_KEY]);
  if (side === null) return null;
  const marketType = scalarText(row[MARKET_TYPE_KEY]);

  return computeLiquidationDistance(
    numberOf(row[MARK_PRICE_KEY]),
    numberOf(row[LIQUIDATION_PRICE_KEY]),
    side.toLowerCase(),
    marketType === null ? null : marketType.toLowerCase(),
  );
};

/**
 * The derived liquidation-distance slot: one figure, or the marker with the right sentence.
 *
 * The three arms in front of the derivation are the same three every position field has —
 * degraded, flat, then the list — and the arms behind it are {@link soleReport}'s: one
 * distinct distance is the figure, several is a disagreement rather than a pick, and none is
 * the reason or reasons the rows themselves give.
 *
 * @param {unknown} body A resolved `GET /api/dashboard` body, or `null`.
 * @param {string|null} degradedReason BC-2's marker, already read.
 * @returns {{available: boolean}}
 */
const liquidationReport = (body, degradedReason) => {
  if (degradedReason !== null) return unavailable(degradedReason);

  const rows = positionRows(body);
  if (rows.length === 0) return unavailable(NO_POSITION_REASON);

  const distances = [];
  const absences = [];
  for (const row of rows) {
    const distance = liquidationOf(row);
    if (distance === null) {
      const reason = liquidationAbsenceOf(row);
      if (!absences.includes(reason)) absences.push(reason);
      continue;
    }
    const figure = Number(distance.toFixed(LIQUIDATION_PRECISION));
    if (!distances.includes(figure)) distances.push(figure);
  }

  if (distances.length === 1) return available(distances[0]);
  if (distances.length > 1) return unavailable(DISAGREEMENT_REASON(distances.length));
  // `rows` is non-empty and none of them yielded a distance, so `absences` is not empty.
  // More than one sentence means the open positions are absent for more than one reason,
  // and both are true of the account.
  return unavailable(absences.join(" "));
};

/**
 * `positions[].contracts` + `positions[].side` → one reading, or the marker.
 *
 * The size is the declared path and the side is one of its declared inputs, appended only
 * when the same read reports exactly one of those too — a size carrying a side borrowed from
 * a different position would be a fabricated pair, and the size alone is still a true reading
 * of the field. `readStrategyPair`'s shape, for the same reason.
 *
 * @param {unknown} body
 * @param {{field: string, path: string}} entry
 * @returns {{available: boolean}}
 */
const readPositionPair = (body, entry) => {
  const report = soleReport(reportedAcross(body, entry.path), entry.field);
  if (!report.available) return report;

  const sides = reportedAcross(body, POSITION_SIDE_PATH);
  return sides.length === 1 ? available(`${sides[0]} ${report.value}`) : report;
};

/**
 * A position-sourced tier-2 field → its reading, or the RIGHT absence. Three arms, and the
 * marker is consulted BEFORE the list (BC-2, Requirement 14.5):
 *
 *   * `degraded` non-null — the positions read behind this 200 failed. The server's own
 *     sentence, because the server knows which environment failed and why, and the figure
 *     that would otherwise render here is "flat".
 *   * an empty list with no degradation — a COMPLETE read of an account holding nothing.
 *     {@link NO_POSITION_REASON}, which says so.
 *   * otherwise — the sole reading, or the field's own declared reason.
 *
 * Every position field takes all three, not just `position`: `unrealisedPnl` and `exposure`
 * come off the same rows, so on the degraded arm their declared reasons ("was not reported")
 * would describe a read that never happened.
 *
 * @param {unknown} body
 * @param {{field: string, path: string}} entry
 * @param {string|null} degradedReason
 * @returns {{available: boolean}}
 */
const positionsReport = (body, entry, degradedReason) => {
  if (degradedReason !== null) return unavailable(degradedReason);
  if (positionRows(body).length === 0) return unavailable(NO_POSITION_REASON);

  return entry.field === "position"
    ? readPositionPair(body, entry)
    : soleReport(reportedAcross(body, entry.path), entry.field);
};

/**
 * The dashboard payload → tier 2's seven `Reported<>`s, keyed by declared field.
 *
 * Every branch is chosen by the DECLARATION, as `buildTierOne`'s are:
 *
 *   * `verdict: UNAVAILABLE` — `realisedPnlPerDeployment`'s case, and the point of
 *     Requirement 14.5 on this page. Realised P&L is reported for the whole ACCOUNT:
 *     `overview.today_realized_pnl` and BC-5's `overview.realized_pnl` are both account-wide
 *     sums, so attributing either to a deployment is the fabrication. The marker keeps its
 *     declared place in the row, next to the account-wide figure under its own explicit
 *     label, and the two together say what one figure labelled "Realised P&L" could not.
 *   * `verdict: DERIVED` — the liquidation distance, the tier's only computed field, whose
 *     `derivation` names the function this page imports.
 *   * a path INTO `positions[]` — {@link positionsReport}, gated on `degraded`.
 *   * a scalar path — `overview.today_realized_pnl` and `risk.risk_level`, both account-wide
 *     and neither gated on the positions marker, because neither comes off that list.
 *
 * A `null` payload — no read yet, or a read whose payload `usePanelState` dropped — makes
 * every list empty and every scalar absent, so every slot is the marker. Never a zero.
 *
 * @param {unknown} dashboardBody
 * @param {string|null} degradedReason
 * @returns {Object<string, {available: boolean}>}
 */
const buildTierTwo = (dashboardBody, degradedReason) => {
  const model = {};
  for (const { key } of TIER_TWO) {
    const entry = fieldEntry(key);

    if (entry.verdict === VERDICT.UNAVAILABLE) {
      model[key] = unavailable(entry.reason);
      continue;
    }
    if (entry.verdict === VERDICT.DERIVED) {
      model[key] = liquidationReport(dashboardBody, degradedReason);
      continue;
    }

    model[key] = entry.path.includes(LIST_MARKER)
      ? positionsReport(dashboardBody, entry, degradedReason)
      : fromNullable(scalarText(readPath(dashboardBody, entry.path)), reasonOf(key));
  }
  return model;
};

/* ══════════════════════════════════════════════════════════════════════════
 * TIER 3 — WHAT HAPPENED LAST (Requirement 7.3)
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Three declared slots: the latest signal, the latest order, the execution status. Two of
 * them come off the dashboard read this page already makes; the third is the only read on
 * this page that goes to a venue.
 *
 * THE SIGNAL IS THE ONE FIELD WHERE READING THE DECLARED PATH WOULD BE THE BUG
 * ---------------------------------------------------------------------------
 * `recent_activity.signals[0]` is the newest signal ON THE ACCOUNT, and this page's entire
 * subject is attribution — which strategy, which venue, which deployment. Rendering that row
 * under the label "Latest signal" beside one strategy's name states that the strategy
 * produced it, which is a claim the payload does not support and which no marker can be
 * retracted from once a trader has acted on it. The declaration says so in as many words:
 * filter by strategy id first, and render the marker rather than the account's newest signal
 * when the filter is empty. {@link latestSignalReport} does exactly that, and the strategy id
 * it filters by is `strategies[].id` — one of `strategy`'s own declared inputs, and the same
 * strategy tier 1 names — taken only when the read reports exactly one of them.
 *
 * IN PRACTICE THAT FILTER IS EMPTY TODAY, AND THAT IS THE HONEST OUTCOME
 * --------------------------------------------------------------------
 * `dashboard_aggregation_service.get_recent_signals` selects
 * `id, generated_at, decision, symbol, exchange_id, risk_passed` and publishes
 * `{id, time, text, type}`. There is no strategy id on the row and none in the select, so no
 * published signal is attributable to a strategy at all, and {@link SIGNAL_ABSENCE}'s
 * `unattributed` sentence is what a trader sees. That is the field working: the marker names
 * a real gap in the payload, where the alternative is a sentence about someone else's
 * strategy rendered as this one's. The strategy-id filter is still written and still runs,
 * because it is the check that must not be skipped the moment the service publishes the
 * column, and because it is the only thing that distinguishes "no signal is attributable"
 * from "this strategy has no signal".
 *
 * THE ORDER IS ONE VENUE'S, AND THE VENUE HAS TO BE PASSED
 * -------------------------------------------------------
 * `GET /api/orders/open` requires `exchange_id`; the venue comes from tier 1's `exchange`
 * reading, which is `exchange.exchanges[].exchange_id` collapsed by {@link soleReport}. If no
 * single venue is reported there is nothing to query, so NO REQUEST IS ISSUED — `usePanelState`
 * is left disabled — and the slot renders {@link ORDERS_ABSENCE}`.noVenue`. Guessing
 * `"binance"` is what the old surfaces did, and it would query the wrong venue's keys.
 *
 * "Latest" is a claim about time, so it is read from a reported time: ccxt's `timestamp`, or
 * its `datetime` parsed. A single open order is unambiguous whatever it reports, but several
 * orders with no reported time have no newest one, and the marker says that rather than
 * letting the array's arrival order decide.
 *
 * THE EXECUTION STATUS IS ACCOUNT-WIDE AND SAYS SO
 * -----------------------------------------------
 * `executions[].status` is the whole account's, exactly like `risk.risk_level` in tier 2, and
 * it goes through {@link soleReport} for tier 1's reason: one slot holds one reading, an empty
 * list is the declared absence, and several distinct statuses is a disagreement rather than a
 * row picked out of a list.
 */

/** §7.5's tier 3, in declaration order. */
const TIER_THREE = TIERS.filter((entry) => entry.tier === 3);

/** The tier-1 field whose reading is the venue the open-orders read is issued against. */
const VENUE_FIELD = "exchange";

/** The three tier-3 fields, named once each. */
const SIGNAL_FIELD = "latestSignal";
const ORDERS_FIELD = "latestOrder";

/** `strategies[].id`, which is one of `strategy`'s declared inputs. */
const STRATEGY_ID_PATH = inputPath("strategy", ".id") ?? "strategies[].id";

/**
 * The keys a signal row would carry a strategy id under.
 *
 * Two spellings and no third: the service publishes neither today, and a wider net — matching
 * anything containing "strategy" — would eventually match a NAME and compare it against an id,
 * which fails open. Both of these are ids or nothing.
 */
const SIGNAL_STRATEGY_KEYS = Object.freeze(["strategy_id", "strategyId"]);

/** The composed sentence `get_recent_signals` publishes per row. */
const SIGNAL_TEXT_KEY = "text";

/**
 * WHY a signal could not be attributed to this strategy — and it matters which.
 *
 * The declaration carries one reason for this field, and it is the one for an account with no
 * signals at all ("No signal has been recorded for this account yet"). These four are the
 * other outcomes, and none of them may collapse into that sentence: an account with five
 * signals none of which is attributable has recorded signals, and saying otherwise would
 * report an empty table where the truth is an unattributable one.
 */
const SIGNAL_ABSENCE = Object.freeze({
  unknownStrategy:
    "This read reported no single strategy, so there is no strategy id to match a signal "
    + "against. The account's newest signal is not shown, because nothing says it is this "
    + "strategy's.",
  unattributed:
    "Signals have been recorded on this account, but none of them reports the strategy that "
    + "produced it, so none can be attributed to this strategy. The account's newest signal "
    + "is not shown as this strategy's.",
  otherStrategy:
    "Signals have been recorded on this account, and every one of them belongs to another "
    + "strategy. This strategy has produced none.",
  undescribed:
    "This strategy's newest signal carries no description, so there is nothing to show for "
    + "it. The signal itself was recorded.",
});

/**
 * WHY there is no latest order.
 *
 * The declared reason covers exactly one of these — the read that failed — and it is used for
 * that arm verbatim. The other three are successes, or non-attempts, and reporting them as
 * "could not be read" would blame a venue that answered.
 */
const ORDERS_ABSENCE = Object.freeze({
  noVenue:
    "No single exchange is reported for this account, so there is no venue whose open orders "
    + "could be requested. Open orders are held per venue and this read takes one.",
  none:
    "This venue reports no open order for this account, so there is no latest order to show. "
    + "This is a complete read: the open-orders read did not fail.",
  undated:
    "The venue reported several open orders and none of them carries a time, so which is the "
    + "latest is unknown. The first one in the list is not evidence of the newest.",
  undescribed:
    "The venue's newest open order reports no side, size or market, so there is nothing to "
    + "show for it. The order itself is open.",
});

/** The one strategy id the strategies read reports, or `null` when it is not exactly one. */
const soleStrategyId = (strategiesBody) => {
  const ids = reportedAcross(strategiesBody, STRATEGY_ID_PATH);
  return ids.length === 1 ? ids[0] : null;
};

/** The strategy id a signal row attributes itself to, or `null` when it attributes itself to none. */
const signalStrategyId = (row) => {
  for (const key of SIGNAL_STRATEGY_KEYS) {
    const value = scalarText(row[key]);
    if (value !== null) return value;
  }
  return null;
};

/** The rows of a declared list off a body, objects only, in the order the server sent them. */
const listRows = (body, dottedPath) => {
  const list = readPath(body, dottedPath);
  return Array.isArray(list) ? list.filter((row) => row && typeof row === "object") : [];
};

/**
 * The latest signal THIS STRATEGY produced, or the marker saying which absence this is.
 *
 * Five arms, and the order is the point:
 *
 *   1. no signals on the account — the DECLARED reason, the only arm it describes.
 *   2. no single strategy id from the strategies read — nothing to filter by.
 *   3. signals exist and none reports a strategy — the payload's own gap (see the section
 *      docblock: this is today's outcome for every account).
 *   4. signals exist, all attributed elsewhere — this strategy has produced none.
 *   5. the newest of this strategy's, which is the first matching row because the service
 *      orders by `generated_at` descending.
 *
 * Arms 2, 3 and 4 are the ones that would otherwise render the account's newest signal.
 *
 * @param {unknown} dashboardBody
 * @param {string|null} strategyId The one strategy id reported, or `null`.
 * @returns {{available: boolean}}
 */
const latestSignalReport = (dashboardBody, strategyId) => {
  const entry = fieldEntry(SIGNAL_FIELD);
  const rows = listRows(dashboardBody, entry.path);
  if (rows.length === 0) return unavailable(entry.reason);
  if (strategyId === null) return unavailable(SIGNAL_ABSENCE.unknownStrategy);

  const attributed = rows.filter((row) => signalStrategyId(row) !== null);
  if (attributed.length === 0) return unavailable(SIGNAL_ABSENCE.unattributed);

  const mine = attributed.filter((row) => signalStrategyId(row) === strategyId);
  if (mine.length === 0) return unavailable(SIGNAL_ABSENCE.otherStrategy);

  return fromNullable(scalarText(mine[0][SIGNAL_TEXT_KEY]), SIGNAL_ABSENCE.undescribed);
};

/**
 * One ccxt order row → the millisecond time it reports, or `null`.
 *
 * `timestamp` is ccxt's own millisecond integer and `datetime` is the same instant as an ISO
 * string; the second is read only when the first is absent, so two encodings of one fact
 * cannot disagree here. Neither is defaulted to "now", which would make every undated order
 * the newest.
 */
const orderTime = (row) => {
  const millis = numberOf(row.timestamp);
  if (millis !== null) return millis;

  const iso = scalarText(row.datetime);
  if (iso === null) return null;
  const parsed = Date.parse(iso);
  return Number.isFinite(parsed) ? parsed : null;
};

/**
 * The newest order among the venue's open ones, or `null` when there is no basis to pick one.
 *
 * A single order is the latest by being the only one, whatever it reports about time. With
 * several, only a reported time decides — the array's own order is the venue's, and ccxt makes
 * no promise about it.
 */
const latestOrderRow = (rows) => {
  if (rows.length === 1) return rows[0];

  let newest = null;
  let newestAt = null;
  for (const row of rows) {
    const at = orderTime(row);
    if (at === null) continue;
    if (newestAt === null || at > newestAt) {
      newest = row;
      newestAt = at;
    }
  }
  return newest;
};

/**
 * The venue's open-orders response → its object rows, or `[]`.
 *
 * The response root IS the ccxt array, so there is no envelope to unwrap. Extracted from
 * {@link latestOrderReport} in task 20.3 because the cancel control reads the SAME rows: two
 * copies of this filter would be two chances for the order a trader is shown and the order a
 * cancel addresses to be different rows.
 *
 * @param {unknown} payload
 * @returns {Object[]}
 */
const openOrderRows = (payload) =>
  (Array.isArray(payload) ? payload.filter((row) => row && typeof row === "object") : []);

/** The parts of one order row that make up the reading, in the order they are read. */
const ORDER_PARTS = Object.freeze(["side", "amount", "symbol"]);

/**
 * One order row → `"buy 0.25 BTC/USDT"`, or `null` when it reports none of the three.
 *
 * All three come off the SAME row, so nothing here is a pair assembled across rows — the
 * failure `readPositionPair` and `readStrategyPair` are shaped to avoid. A row reporting two
 * of the three renders those two: a partial reading of one order is still a true one.
 */
const orderText = (row) => {
  const parts = [];
  for (const key of ORDER_PARTS) {
    const part = scalarText(row[key]);
    if (part !== null) parts.push(part);
  }
  return parts.length === 0 ? null : parts.join(" ");
};

/**
 * The open-orders read → the `latestOrder` slot. Four arms, and only one of them is the
 * declared reason.
 *
 * @param {Object} orders
 * @param {string|null} orders.venue The one venue reported, or `null` — no request was issued.
 * @param {boolean} orders.failed Whether the read failed. `usePanelState` has already dropped
 *   its payload, so this is the only thing that tells a failure from a venue holding nothing.
 * @param {unknown} orders.payload The response root, which IS the order array.
 * @returns {{available: boolean}}
 */
const latestOrderReport = ({ venue, failed, payload }) => {
  if (venue === null) return unavailable(ORDERS_ABSENCE.noVenue);
  if (failed) return unavailable(reasonOf(ORDERS_FIELD));

  const rows = openOrderRows(payload);
  if (rows.length === 0) return unavailable(ORDERS_ABSENCE.none);

  const newest = latestOrderRow(rows);
  if (newest === null) return unavailable(ORDERS_ABSENCE.undated);

  return fromNullable(orderText(newest), ORDERS_ABSENCE.undescribed);
};

/**
 * The three payloads → tier 3's three `Reported<>`s, keyed by declared field.
 *
 * The signal and the order each have their own function above, for the reasons those
 * functions carry. `executionStatus` is the plain case: a declared path into a list, through
 * {@link soleReport}, so an empty `executions` is the declared absence and several distinct
 * statuses is a disagreement rather than a pick.
 *
 * @param {unknown} dashboardBody
 * @param {unknown} strategiesBody
 * @param {{available: boolean}} orderReport {@link latestOrderReport}'s answer.
 * @returns {Object<string, {available: boolean}>}
 */
const buildTierThree = (dashboardBody, strategiesBody, orderReport) => {
  const model = {};
  for (const { key } of TIER_THREE) {
    if (key === ORDERS_FIELD) {
      model[key] = orderReport;
      continue;
    }
    if (key === SIGNAL_FIELD) {
      model[key] = latestSignalReport(dashboardBody, soleStrategyId(strategiesBody));
      continue;
    }
    model[key] = soleReport(reportedAcross(dashboardBody, fieldEntry(key).path), key);
  }
  return model;
};

/* ══════════════════════════════════════════════════════════════════════════
 * THE SELECTOR ABOVE TIER 1 — THE PER-STRATEGY UNION (part D)
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Declared here, after tier 3, because it reuses {@link listRows} and
 * {@link STRATEGY_ID_PATH} from that section. It RENDERS above tier 1, which is where
 * `pageHierarchy` puts it and why it carries no tier.
 *
 * THE READ IS PER STRATEGY, SO THE SELECTOR IS A UNION AND COSTS N REQUESTS
 * -----------------------------------------------------------------------
 * `deployment`'s declaration says it outright: "there is no single read that returns every
 * deployment a trader has". `GET /api/strategies/{id}/deployments` answers for ONE strategy
 * id, so the list §7.5 draws is the union over the strategies `GET /api/strategies` already
 * returned — one call per strategy, issued together. There is no account-wide deployment
 * endpoint to prefer instead, and there is no cap on the fan-out: a cap would silently drop
 * a running deployment from a selector whose whole job is to list them, which is a worse
 * failure than N requests.
 *
 * They are issued ONCE per answer, not per render. The reader is `useCallback`'d over a
 * memoised id array whose identity changes only when the strategies payload does, and
 * `usePanelState`'s `deps` holds that same array — the hook compares `deps` element-wise and
 * reads the reader through a ref, so neither a re-render nor a new closure re-issues
 * anything. A changed id list IS a new question and does re-issue, which is correct: rows
 * from strategies the account no longer lists must not stay selectable.
 *
 * ONE STRATEGY'S FAILURE KEEPS THE OTHERS' ROWS (part C's precedent, one level down)
 * -------------------------------------------------------------------------------
 * `Promise.allSettled`, not `Promise.all`. `all` rejects on the first failure, which would
 * make one strategy's timeout erase every deployment the account has — the same erasure part
 * C refused when it kept the venue's orders read out of the page-level branch. Here the union
 * keeps what answered and reports what did not, and {@link REGISTRY_CAVEAT}'s neighbour alert
 * names the unread strategies so a short list is never read as a complete one.
 *
 * WHAT A ROW CARRIES, AND WHAT IT DOES NOT
 * ---------------------------------------
 * The six columns are the record's own keys and nothing else — `deployment_id`, `status`,
 * `environment`, `worker`, `started_at`, `health`. Three of them (`status`, `worker`,
 * `started_at`) are the declaration's own `inputs`; `environment` is an input of
 * `tradingEnvironment`; `deployment_id` and `health` are on the response shape
 * `strategiesApi.listDeployments` documents. An absent one renders `ds/DataTable`'s
 * not-available marker, which is what that component does for `null`, `undefined` and `""` —
 * never `0`, never a guessed `"healthy"`, which is the default `pages/Strategies.jsx` was
 * caught inventing for exactly this field.
 *
 * The record names NO strategy, so {@link deploymentRow} attaches the `strategy_id` the read
 * was made under — the response's own, falling back to the id requested. That is not a join
 * and not an inference: it is the scope of the request that produced the row, and it is the
 * only thing that makes a selection able to re-scope anything at all.
 *
 * A ROW'S `environment` IS THE RECORD'S OWN, AND A DISAGREEMENT IS SURFACED
 * -----------------------------------------------------------------------
 * This route is the live ledger (Requirement 1.3), and a deployment record labelled anything
 * else is a fact worth knowing rather than a fact worth hiding: it means the row a trader is
 * about to scope the page by is not running against real money, whatever the strip above
 * says. {@link nonLiveDeployments} collects them and the surface names them. The strip is
 * NOT altered by it — the strip states what the route is, and that claim is still true.
 */

/** The declared field the selector renders. `pageHierarchy` registers it with NO tier. */
const DEPLOYMENT_FIELD = "deployment";

/** The declared path — the container the response carries the records under. */
const DEPLOYMENTS_PATH = fieldEntry(DEPLOYMENT_FIELD)?.path ?? "deployments";

/** The response's own scope field, and the key {@link deploymentRow} records it under. */
const STRATEGY_SCOPE_PATH = "strategy_id";

/** A row's identity in the union: the strategy scope plus the deployment id. */
const UNION_KEY = "union_row_key";

/** `strategies[].id`'s leaf, so {@link scopeStrategies} matches on the declared key. */
const STRATEGY_ID_KEY = leafKeyOf(STRATEGY_ID_PATH);

/** The one environment a row may report without contradicting this route. */
const LIVE_ENVIRONMENT = "live";

/**
 * The six columns, which are the six keys the read carries.
 *
 * `sortable` is on none of them and `onSortChange` is not passed: `ds/DataTable` renders a
 * sortable header as a button and a non-sortable one as text, so declaring sorting without
 * wiring it is precisely the dead control Requirement 19.4 forbids. `started_at` is a
 * `timestamp`, so a value the browser cannot parse as an instant renders the marker rather
 * than being shown as an unordered string.
 */
const DEPLOYMENT_COLUMNS = Object.freeze([
  Object.freeze({ key: "deployment_id", header: "Deployment", format: "text", priority: 1 }),
  Object.freeze({ key: "status", header: "Status", format: "text", priority: 1 }),
  Object.freeze({ key: "environment", header: "Environment", format: "text", priority: 1 }),
  Object.freeze({ key: "worker", header: "Worker", format: "text", priority: 2 }),
  Object.freeze({ key: "started_at", header: "Started", format: "timestamp", priority: 2 }),
  Object.freeze({ key: "health", header: "Health", format: "text", priority: 2 }),
]);

/** The record keys the columns project, derived so the two lists cannot diverge. */
const DEPLOYMENT_RECORD_KEYS = Object.freeze(DEPLOYMENT_COLUMNS.map((column) => column.key));

/** The first column's key, which is also a row's own identifier. */
const DEPLOYMENT_ID_KEY = DEPLOYMENT_COLUMNS[0].key;

/** The environment column's key. */
const DEPLOYMENT_ENVIRONMENT_KEY = "environment";

/** The status column's key, which task 20.3's stop confirmation states before it acts. */
const DEPLOYMENT_STATUS_KEY = "status";

/**
 * THE INCOMPLETENESS, IN WORDS A TRADER CAN ACT ON.
 *
 * Not a declared reason — the declaration's `reason` is for an absent record, and this is a
 * property of a list that DID answer. `strategiesApi.listDeployments`' docblock is what this
 * sentence carries to the surface: the handler lists an in-process registry, so a deployment
 * the current backend process did not start is not in it and the answer can be shorter than
 * the `strategy_deployments` table.
 *
 * The last clause is the actionable one. The expensive misreading here is not "this list is
 * short", it is "this deployment is not running, so I will start it again".
 */
const REGISTRY_CAVEAT =
  "This list may be incomplete. It reports the deployments the backend process currently "
  + "holds in memory, so one started by an earlier process — before a restart, or on another "
  + "worker — is not listed here even though it is still recorded and may still be trading. "
  + "Treat a deployment you cannot find as UNKNOWN rather than stopped, and do not deploy it "
  + "again on the strength of this list.";

/** What a selection re-scopes, and what it deliberately does not. Rendered beside the table. */
const SELECTION_CAVEAT =
  "Selecting a deployment re-scopes the strategy, the market and the latest signal below to "
  + "that deployment's strategy. It does not re-scope the realised P&L, the risk state or the "
  + "position: those are reported for the whole account, and no read on this page attributes "
  + "them to a deployment.";

/**
 * One record → one row of the union.
 *
 * Built key by key rather than spread, so a field the response gains later cannot arrive in
 * the table without a column being declared for it, and so the two synthetic keys cannot
 * collide with a record key.
 *
 * @param {Object} record One element of `deployments`.
 * @param {string} strategyId The scope the record was read under.
 * @param {number} ordinal Its position in the union, for a row with no reported id.
 * @returns {Object}
 */
const deploymentRow = (record, strategyId, ordinal) => {
  const row = {};
  for (const key of DEPLOYMENT_RECORD_KEYS) row[key] = record[key];

  const id = scalarText(record[DEPLOYMENT_ID_KEY]);
  row[STRATEGY_SCOPE_PATH] = strategyId;
  row[UNION_KEY] = `${strategyId}::${id ?? `row-${ordinal}`}`;
  return row;
};

/**
 * Every strategy's deployments, read together, with the failures counted rather than thrown.
 *
 * @param {string[]} strategyIds The ids `GET /api/strategies` reported.
 * @returns {Promise<{deployments: Object[], unreadStrategies: string[]}>} Never rejects for a
 *   single strategy's failure; see the section docblock.
 */
const readDeploymentUnion = async (strategyIds) => {
  const settled = await Promise.allSettled(
    strategyIds.map((strategyId) => strategiesApi.listDeployments(strategyId)),
  );

  const deployments = [];
  const unreadStrategies = [];

  settled.forEach((outcome, index) => {
    const requestedId = strategyIds[index];
    if (outcome.status !== "fulfilled") {
      unreadStrategies.push(requestedId);
      return;
    }
    // The response states its own scope; the requested id is the same fact, and is used only
    // when a server answered without echoing it.
    const scope = scalarText(readPath(outcome.value, STRATEGY_SCOPE_PATH)) ?? requestedId;
    for (const record of listRows(outcome.value, DEPLOYMENTS_PATH)) {
      deployments.push(deploymentRow(record, scope, deployments.length));
    }
  });

  return { deployments, unreadStrategies };
};

/** The union's rows off the read's payload, or `[]` — no read yet, or a failed one. */
const unionRows = (payload) =>
  (Array.isArray(payload?.deployments) ? payload.deployments : []);

/** The strategies whose deployments could not be read, or `[]`. */
const unreadStrategiesOf = (payload) =>
  (Array.isArray(payload?.unreadStrategies) ? payload.unreadStrategies : []);

/**
 * The rows whose OWN environment contradicts this route.
 *
 * A row reporting no environment is not in the list: an absent label is the marker's job and
 * is not evidence of a paper deployment (§8.1's rule, one level out).
 *
 * @param {Object[]} rows
 * @returns {Object[]}
 */
const nonLiveDeployments = (rows) =>
  rows.filter((row) => {
    const environment = scalarText(row[DEPLOYMENT_ENVIRONMENT_KEY]);
    return environment !== null && environment.toLowerCase() !== LIVE_ENVIRONMENT;
  });

/** A row's own id as a trader reads it, or its union key when it reported none. */
const deploymentLabel = (row) =>
  scalarText(row[DEPLOYMENT_ID_KEY]) ?? String(row[UNION_KEY]);

/** Where a trader goes to deploy one. `App.jsx`'s route, not a guess. */
const STRATEGIES_ROUTE = "/app/strategies";

/** "The one strategy this page lists was asked …" / "All 4 … were asked …", with the answer. */
const askedClause = (count, answer) =>
  (count === 1
    ? `The one strategy this page lists was asked for its deployments, and answered: "${answer}"`
    : `All ${count} strategies this page lists were asked for their deployments, and every one `
      + `answered: "${answer}"`);

/** "The one strategy this page lists did not answer" / "None of the 4 … answered". */
const unreadClause = (count) =>
  (count === 1
    ? "The one strategy this page lists did not answer"
    : `None of the ${count} strategies this page lists answered`);

/**
 * An empty union → Requirement 14.1's three fields, and WHICH emptiness this is.
 *
 * Three cases, and collapsing them would be the failure `ds/EmptyState` exists to stop. "No
 * deployment is reported" and "the deployment reads failed" are different facts with different
 * next actions, and an account with no strategy at all has not been asked anything: reporting
 * that as "nothing is running" would state the result of a read that was never issued.
 *
 * The declared reason is quoted VERBATIM in the third case, because it is the server's answer
 * to the question that was actually asked — once per strategy.
 *
 * @param {{strategyCount: number, unreadCount: number, retry: Function}} input
 * @returns {{headline: string, body: string, action: Object}}
 */
const emptySelectorCopy = ({ strategyCount, unreadCount, retry }) => {
  if (strategyCount === 0) {
    return {
      headline: "There is no strategy to ask about",
      body:
        "Deployments are read one strategy at a time, and this account lists no strategy — so "
        + "no deployment read was issued. This is not a reading of nothing running; it is the "
        + "absence of anything to ask.",
      action: { label: "Open Strategies", to: STRATEGIES_ROUTE },
    };
  }

  if (unreadCount >= strategyCount) {
    return {
      headline: "The deployment list could not be read",
      body:
        `${unreadClause(strategyCount)}, so this list is empty because the reads failed — not `
        + "because nothing is deployed. Nothing below is scoped to a deployment.",
      action: { label: "Try again", onClick: retry },
    };
  }

  return {
    headline: "No deployment is reported",
    body: unreadCount === 0
      ? askedClause(strategyCount, reasonOf(DEPLOYMENT_FIELD))
      : `The ${strategyCount - unreadCount} strategies that answered each said: `
        + `"${reasonOf(DEPLOYMENT_FIELD)}" The other ${unreadCount} could not be read, so this `
        + "is not evidence that nothing is deployed.",
    action: { label: "Open Strategies", to: STRATEGIES_ROUTE },
  };
};

/**
 * The strategies body, narrowed to the selected deployment's strategy.
 *
 * This is the ENTIRE re-scoping mechanism, and it is a narrowing of the payload rather than a
 * new code path: `buildTierOne` and `buildTierThree` are handed a body with one strategy in
 * it, so `strategies[].name`, `strategies[].symbol` and `strategies[].id` resolve to that
 * strategy through the same {@link soleReport} they always used. Nothing downstream knows a
 * selection happened, which is why a selection cannot introduce a figure that an unselected
 * page could not produce.
 *
 * A selection whose strategy is not in the list narrows to NOTHING rather than falling back
 * to the whole list: the tiers then render their declared absences, which is the honest answer
 * to "we cannot describe this deployment's strategy". Falling back would label another
 * strategy's name as this deployment's.
 *
 * @param {{strategies: unknown[]}} strategiesBody
 * @param {Object|null} selectedRow
 * @returns {{strategies: unknown[]}}
 */
const scopeStrategies = (strategiesBody, selectedRow) => {
  if (selectedRow === null) return strategiesBody;

  const strategyId = scalarText(selectedRow[STRATEGY_SCOPE_PATH]);
  if (strategyId === null) return strategiesBody;

  return {
    strategies: strategiesBody.strategies.filter(
      (row) => row
        && typeof row === "object"
        && scalarText(row[STRATEGY_ID_KEY]) === strategyId,
    ),
  };
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
 * THE THREE DESTRUCTIVE ACTIONS (task 20.3) — Requirements 7.6, 8.1, 8.3, 19.1, 19.4
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Until this task the page was READ-ONLY. It now carries three controls and NOTHING ELSE:
 * stop the selected deployment, cancel the one open order tier 3 names, cancel every open
 * order at the venue. Each one is a `ds/ConfirmDialog` over an endpoint that already exists.
 *
 * THERE IS NO MANUAL-ORDER AFFORDANCE HERE, AND THAT IS A REQUIREMENT
 * ------------------------------------------------------------------
 * `POST /api/orders/execute` answers every caller with `MANUAL_EXECUTION_BLOCKED` and its
 * aliases — `/create`, `/stop-loss`, `/take-profit` — do the same (Requirement 19.1). So this
 * task adds no place-order control, no amend control and no modify-position control: the
 * three below either STOP something or REMOVE a resting order. `ordersApi.createOrder` and
 * `ordersApi.updateOrder` are not imported by this page and are not called from it.
 *
 * THE ACKNOWLEDGEMENT IS ON ONE OF THE THREE, AND THE ASYMMETRY IS THE POINT
 * -------------------------------------------------------------------------
 * §8.4's inventory spends an acknowledgement on two kinds of action — the irreversible ones
 * ("Cancel all orders … it is bulk and irreversible") and the real-funds ones (Requirement
 * 8.2's deploy to live) — and refuses one to everything reversible or risk-reducing:
 *
 *   * **Stop deployment — NO acknowledgement.** Stopping is risk-REDUCING. It stops new
 *     orders being generated and it is reversible one control away, on the same list, by
 *     deploying again. `pages/Dashboard.jsx` makes exactly this argument in reverse for its
 *     kill switch: the halt earns the checkbox, the RECOVERY does not, because "gating the
 *     recovery would put a checkbox between a trader and the restoration of trading".
 *   * **Cancel live order — NO acknowledgement.** One resting order, named in the review grid
 *     by side, size, market and id. It is the smallest reversible-by-re-placing action on the
 *     page, and `pages/Strategies.jsx`'s archive dialog is the same verdict for the same
 *     reason: the checkbox is "reserved for the irreversible and the live-funds cases".
 *   * **Cancel all orders — YES, required.** Bulk, every market at the venue, and nothing on
 *     this page or on the server re-places what it removes.
 *
 * Making the three symmetric is the failure mode, not the safe default. A trader who ticks a
 * box to stop a bot learns to tick boxes without reading them, and that habit is spent on the
 * one action here that actually needs the pause — which is what makes the gate worthless
 * exactly where §8.4 put it.
 *
 * The LIVE badge is on all three, and it is a different axis. `environment="LIVE"` states
 * which LEDGER is being acted on (Requirement 8.5), and every action on this route acts on
 * the live one (Requirement 1.3). The acknowledgement states the RISK CLASS of the action.
 * Only the second is asymmetric, and conflating them would either drop a badge from a
 * real-money confirmation or put a checkbox on all three.
 *
 * PROPERTY 13 IS STRUCTURAL HERE, NOT A PROPERTY OF A RETURN VALUE
 * ---------------------------------------------------------------
 * Each action is split in two, copying `pages/Strategies.jsx`'s
 * `requestArchiveStrategy`/`handleConfirmArchive` shape and its reasoning: the `request…`
 * half is the WHOLE of what the button does — it sets one state object and issues nothing —
 * and the `handleConfirm…` half is the dialog's own confirm action and the only place the
 * mutation is called from. Under a `window.confirm` the button and the request were one
 * statement, so "no request before the confirmation" was a property of what `confirm()`
 * returned. It is now a property of this file's structure: there is no code path from a
 * button to `stopDeployment`, `cancelOrder` or `cancelAllOrders`, which is what Property 13
 * can check. `ds/ConfirmDialog` then enforces the acknowledgement in two independent places
 * of its own — the `disabled` attribute AND a guard inside its confirm handler — so a stray
 * `.click()` cannot reach the cancel-all mutation before the box is ticked.
 *
 * EVERY PATH WAS CHECKED AGAINST ITS ROUTER, AND TWO OF THEM WERE WRONG
 * --------------------------------------------------------------------
 * This codebase has shipped client paths that resolve to nothing four times over — three
 * recorded in `api/modules/strategies.js`, one in the open-orders read this page fixed in
 * task 20.1c. Both cancel methods were the fifth and sixth: `DELETE /api/orders/{id}` and
 * `DELETE /api/orders?{options}`, against a router that declares no `DELETE` at all and
 * spells the two cancels `POST /cancel/{order_id}` and `POST /cancel-all`, each with a
 * REQUIRED `exchange_id` query parameter and a body. `api/modules/orders.js` now sends what
 * the routes declare — a corrected address for the same two actions, with the server's own
 * freeze check, execution guard, cancel lock and idempotency untouched.
 *
 *   | control            | route (verified)                            | required           |
 *   |--------------------|---------------------------------------------|--------------------|
 *   | Stop deployment    | `POST /api/deployments/{id}/stop`           | path id; body opt. |
 *   | Cancel live order  | `POST /api/orders/cancel/{order_id}`        | `?exchange_id`,    |
 *   |                    |                                             | `{order_id,symbol}`|
 *   | Cancel all orders  | `POST /api/orders/cancel-all`               | `?exchange_id`     |
 *
 * A CONTROL WHOSE ENDPOINT CANNOT BE ADDRESSED IS NOT RENDERED
 * -----------------------------------------------------------
 * Every required parameter above is a rendering condition, because a button that 422s or
 * 404s on a live account is worse than an absent one:
 *
 *   * **Stop** needs a deployment id in the path, so it renders only for a selected row that
 *     REPORTS one. {@link deploymentRow} synthesises `union_row_key` for a row that reported
 *     no id — that key is this page's, not the server's, and posting it would address nothing.
 *   * **Cancel live order** needs the id, the symbol and the venue. It renders only when the
 *     open-orders read answered, named exactly one newest order (tier 3's own reading) and
 *     that order reports both an id and a symbol.
 *   * **Cancel all** needs the venue, which is tier 1's `exchange` reading. No single venue
 *     reported means no request was ever issued for the orders read either, and the control
 *     is absent rather than guessing an `exchange_id`.
 *
 * Cancel-all is deliberately NOT gated on the open-orders read having found something. The
 * read is one venue's snapshot and it can fail in place — that is this page's whole doctrine
 * for it — and a bulk risk-reducing control that disappears when a venue read breaks is a
 * control that is missing exactly when a trader reaches for it. What the read knows is stated
 * in the dialog's review grid instead, including "could not be read" when it failed.
 *
 * A FAILED MUTATION RENDERS THROUGH THE DIALOG, AND THE DIALOG STAYS OPEN
 * ----------------------------------------------------------------------
 * `ConfirmDialog`'s `error` prop, which renders `translateError`'s output and nothing else —
 * never `err.message`, never a status code, never a stack (Requirement 14.4). The dialog is
 * NOT closed on failure: §8.3's `Failed` state returns to Review, where the confirm button is
 * the retry, and the review grid a trader was reading is still the description of what did
 * not happen. Nothing here hand-rolls an error box.
 */

/** The reason recorded against a stop issued from this page. `stopDeployment` preserves it. */
const STOP_REASON = "Stopped by the trader from the Live Trading page.";

/**
 * A `Reported<>` → a `ConfirmDialog` review value.
 *
 * An unavailable reading becomes `null`, which the dialog renders as its not-available marker
 * with the row's label beside it. That is the point of routing through here rather than
 * reading `.value`: a review grid must never state a figure this page could not read, and it
 * must never drop the row either (§8.3's safety decision 3).
 *
 * @param {{available: boolean}|undefined} report
 * @returns {string|null}
 */
const reviewValue = (report) => (report?.available === true ? report.value : null);

/**
 * The newest open order as something a cancel can ADDRESS, or `null`.
 *
 * The same row tier 3's `latestOrder` names — {@link latestOrderRow} over
 * {@link openOrderRows}, one definition each — so the order described on the page and the
 * order a cancel is issued against cannot be different orders.
 *
 * `null` on every arm where the route could not be satisfied: no venue (no request was
 * issued), a failed read (`usePanelState` has dropped the payload, so there is no row), no
 * newest row (several orders and none reports a time — {@link latestOrderRow} refuses to
 * pick), or a row missing the id or the symbol `cancel_order` requires. Each of those is a
 * control that is not rendered rather than a request that cannot succeed.
 *
 * @param {Object} orders
 * @param {string|null} orders.venue
 * @param {boolean} orders.failed
 * @param {unknown} orders.payload
 * @returns {{orderId: string, symbol: string, reading: string|null}|null}
 */
const cancellableOrder = ({ venue, failed, payload }) => {
  if (venue === null || failed) return null;

  const newest = latestOrderRow(openOrderRows(payload));
  if (newest === null) return null;

  const orderId = scalarText(newest.id);
  const symbol = scalarText(newest.symbol);
  if (orderId === null || symbol === null) return null;

  return { orderId, symbol, reading: orderText(newest) };
};

/**
 * What confirming a STOP does, in the words the server's own path supports and no further.
 *
 * `strategy_service.transition_deployment` gates the transition, asks the in-process runtime
 * best effort, writes `status` and `stopped_at` with the reason on the row, moves the version
 * and audits it. It does not close a position and it does not cancel a resting order, and the
 * response says nothing about either — so this copy says nothing about either happening, and
 * says instead that it does not know. The alternative, "your position will be closed", is the
 * one sentence here that could cost a trader real money on a claim nothing reports.
 */
const STOP_DESCRIPTION =
  "Stopping moves this deployment to stopped, records the reason on it and stops the version "
  + "it is running, so it generates no further signals or orders. That is all the server "
  + "reports doing. It does NOT report closing your open position and it does NOT report "
  + "cancelling orders already resting at the exchange — nothing on this page confirms either, "
  + "so treat the position and the open orders as still there and deal with them yourself. "
  + "Stopping a deployment that is already stopped changes nothing.";

/** What confirming ONE cancel does. The refusal sentence is why a dialog can show an error. */
const CANCEL_ORDER_DESCRIPTION =
  "Cancelling removes this one resting order from the exchange. It does not close an open "
  + "position, it does not affect any other order, and a quantity this order has already "
  + "filled is not undone by cancelling it. The server runs its own safety checks on a cancel "
  + "and can refuse one; a refusal is reported here rather than silently.";

/** What confirming a cancel-ALL does, including the two things a trader under-reads. */
const CANCEL_ALL_DESCRIPTION =
  "Cancelling removes EVERY order this account currently has resting at this venue, in every "
  + "market — not only the one shown above. Open orders are held per venue and carry no "
  + "strategy, so this includes orders your running deployments placed, and a deployment that "
  + "is still running may place new ones immediately afterwards. Nothing here re-places a "
  + "cancelled order, and cancelling does not close any open position.";

/**
 * §8.4's acknowledgement, for the ONE action on this page that earns one.
 *
 * Constructed for cancel-all and for nothing else — off that action there is no object to
 * pass, so the gate is not hidden, it does not exist (§8.4's reading of "SHALL NOT display").
 * The statement names the scope and the irreversibility, because those are the two things the
 * label alone cannot carry.
 */
const CANCEL_ALL_ACKNOWLEDGEMENT = Object.freeze({
  statement:
    "Confirming cancels every order resting at this venue for this account, across every "
    + "market and every deployment. Cancelled orders are not restored and nothing here "
    + "re-places them.",
  label: "I understand every open order at this venue will be cancelled",
  control: "checkbox",
});

/* ══════════════════════════════════════════════════════════════════════════
 * THE PAGE
 * ══════════════════════════════════════════════════════════════════════════ */

/** Which tier-1 figures carry an authored hint, and what it says. */
const TIER_ONE_HINT = Object.freeze({
  connectionState: CONNECTION_HINT,
  strategy: STRATEGY_HINT,
});

/**
 * How each tier-2 figure is FORMATTED. Not in `pageFields`, because a format is a rendering
 * choice and that module holds none — `Dashboard`'s `TIER_ONE_FORMAT` makes the same split.
 *
 * `precision: 2` on the three money figures is a balance. The two text fields are `raw`: a
 * side-and-size pair and a risk word are not quantities, and a numeric format would group and
 * round them. `integer` appears nowhere here — it rounds, and none of these is a count.
 */
const TIER_TWO_FORMAT = Object.freeze({
  position: Object.freeze({ format: "raw" }),
  unrealisedPnl: Object.freeze({ format: "currency", precision: 2 }),
  realisedPnlAccount: Object.freeze({ format: "currency", precision: 2 }),
  realisedPnlPerDeployment: Object.freeze({ format: "currency", precision: 2 }),
  exposure: Object.freeze({ format: "currency", precision: 2 }),
  riskState: Object.freeze({ format: "raw" }),
  [LIQUIDATION_FIELD]: Object.freeze({ format: "percent", precision: LIQUIDATION_PRECISION }),
});

/**
 * The two tier-2 figures that need a sentence the declaration does not carry as a `tooltip`.
 *
 * `realisedPnlAccount` and `liquidationDistance` already declare their own and are read from
 * there. These two do not, and both are places where the label alone could be read as
 * narrower than the figure is:
 *
 *   * `riskState` is account-wide — `risk.risk_level` is one state for the account, not for a
 *     deployment — and its label is the single word "Risk". The hint says whose risk it is,
 *     which is the same distinction the realised-P&L pair is built around.
 *   * `position` is a composed reading, and what it does NOT mean is worth stating: an empty
 *     positions list is only "flat" when the server also reported no degradation.
 */
const TIER_TWO_HINT = Object.freeze({
  position:
    "The side and size the server reports for the open position. A position figure is shown "
    + "only for a completed positions read: if that read failed, this says so instead of "
    + "reporting a flat account.",
  riskState:
    "The risk state the server reports for the WHOLE ACCOUNT, across every deployment and "
    + "every open position — not for one deployment.",
});

/**
 * Tier 3's three hints. None of the three fields declares a `tooltip`, and all three are
 * places where the label is narrower than the reading behind it:
 *
 *   * `latestSignal` is the one field on the page that is FILTERED rather than caveated, so
 *     its hint says what the filter is and that an unmatched account signal is not shown in
 *     its place. That is the difference between this slot and a wrong attribution.
 *   * `latestOrder` is everything the VENUE holds open for the account — `fetch_open_orders`
 *     takes no strategy — so the hint says whose orders they are and which venue was asked.
 *   * `executionStatus` is account-wide, exactly as `riskState` is, and gets the same
 *     treatment for the same reason.
 */
const TIER_THREE_HINT = Object.freeze({
  latestSignal:
    "The newest signal recorded against THIS STRATEGY's id. Signals are recorded per account, "
    + "so one that reports a different strategy — or no strategy at all — is not shown here: "
    + "the account's newest signal is not evidence this strategy produced it.",
  latestOrder:
    "The newest order the exchange reports as still open for this account at the venue shown "
    + "in Exchange above. Open orders are held per venue and are not recorded against a "
    + "strategy, so this is the account's order at that venue, not this deployment's.",
  executionStatus:
    "The status the server reports for recorded executions on the WHOLE ACCOUNT — not for one "
    + "deployment. Several different statuses show no single reading rather than one of them.",
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

  /**
   * The strategies read, normalised ONCE.
   *
   * Memoised because three things now read it — the fan-out's id list, tier 1 and tier 3 —
   * and `asStrategiesBody` returns a fresh object per call: an un-memoised call in the fan-out
   * reader's dependency list would be a new identity on every render, which is how N parallel
   * requests turn into N requests per render.
   */
  const strategiesBody = useMemo(() => asStrategiesBody(strategiesPayload), [strategiesPayload]);

  /*
   * THE FOURTH READ (part D): ONE `listDeployments` PER STRATEGY.
   *
   * `strategies[].id` — `strategy`'s own declared input, collected distinct — is the fan-out's
   * subject list and is also `deps`. Its identity changes only when the strategies payload
   * does, so the N calls are issued once per answer rather than once per render (see the
   * section docblock), and a changed id list is a new question whose previous answer
   * `usePanelState` discards. `enabled` false while the list is empty is what makes "no
   * strategies" cost no requests at all.
   */
  const strategyIds = useMemo(
    () => reportedAcross(strategiesBody, STRATEGY_ID_PATH),
    [strategiesBody],
  );

  const readDeployments = useCallback(() => readDeploymentUnion(strategyIds), [strategyIds]);
  const {
    data: deploymentsPayload,
    error: deploymentsError,
    refetch: refetchDeployments,
    state: deploymentsState,
  } = usePanelState(readDeployments, {
    deps: [strategyIds],
    enabled: strategyIds.length > 0,
  });

  const deploymentRows = useMemo(() => unionRows(deploymentsPayload), [deploymentsPayload]);
  const unreadStrategies = useMemo(
    () => unreadStrategiesOf(deploymentsPayload),
    [deploymentsPayload],
  );

  /*
   * THE SELECTION, HELD AS A KEY AND RESOLVED BY LOOKUP.
   *
   * The key rather than the row, so a re-read cannot leave a row object from a previous answer
   * scoping the tiers. A key that is no longer in the union resolves to `null` and the page is
   * account-wide again — which is the honest outcome for a deployment that has stopped being
   * reported, and it needs no effect to clean up after it.
   */
  const [selectedKey, setSelectedKey] = useState(null);
  const selectedRow = useMemo(
    () => deploymentRows.find((row) => row[UNION_KEY] === selectedKey) ?? null,
    [deploymentRows, selectedKey],
  );

  const selectDeployment = useCallback((row) => {
    setSelectedKey(row?.[UNION_KEY] ?? null);
  }, []);
  const clearDeployment = useCallback(() => setSelectedKey(null), []);
  const rowIdOf = useCallback((row) => row[UNION_KEY], []);

  /**
   * The strategies body the tiers are built from — narrowed by the selection, or not.
   *
   * This is the whole of the re-scoping. Tier 1's `strategy` and `market` and tier 3's
   * signal filter read `strategies[]` off this body, so they describe the selected
   * deployment's strategy without a second code path; every other slot reads the dashboard
   * body, which no selection touches, and therefore keeps its account-wide label and hint.
   */
  const scopedStrategies = useMemo(
    () => scopeStrategies(strategiesBody, selectedRow),
    [strategiesBody, selectedRow],
  );

  const tierOne = useMemo(
    () => buildTierOne(exchangePayload, scopedStrategies),
    [exchangePayload, scopedStrategies],
  );

  /*
   * BC-2's marker, read ONCE and kept apart from the list it qualifies.
   *
   * It is a state of the response and not of the request: a 200 whose positions read failed
   * is still a 200, so `usePanelState` reports `ready` and this is the only thing that says
   * otherwise. Both tier 2's model and the alert below it read this one value.
   */
  const positionsDegraded = useMemo(
    () => readPositionsDegradation(exchangePayload),
    [exchangePayload],
  );

  const tierTwo = useMemo(
    () => buildTierTwo(exchangePayload, positionsDegraded),
    [exchangePayload, positionsDegraded],
  );

  /*
   * THE THIRD READ (Requirement 7.3), AND THE VENUE IT NEEDS.
   *
   * `ordersVenue` is tier 1's own `exchange` reading and not a second collapse of the same
   * list: the figure a trader sees under "Exchange" is the venue this read is issued against,
   * so the two cannot disagree. `null` — nothing reported, or several venues reported — means
   * there is no venue to query, and `enabled: false` is how that becomes NO REQUEST rather
   * than a request with a guessed `exchange_id` (`usePanelState` leaves it `idle`).
   *
   * `deps: [ordersVenue]` because a different venue is a different question: the previous
   * venue's orders must not stay on screen labelled as this one's, and the hook discards them.
   * `symbol` is left unsent — `fetch_open_orders` filters by market, not by strategy, and this
   * page has no basis to narrow one venue's open orders to one market.
   */
  const ordersVenue = tierOne[VENUE_FIELD]?.available ? tierOne[VENUE_FIELD].value : null;

  const readOrders = useCallback(
    () => ordersApi.getOpenOrders(undefined, ordersVenue),
    [ordersVenue],
  );
  const {
    data: ordersPayload,
    refetch: refetchOrders,
    state: ordersState,
  } = usePanelState(readOrders, { deps: [ordersVenue], enabled: ordersVenue !== null });

  /**
   * The environment the SERVER labelled the records with, or `null`.
   *
   * One reading, used three times: tier 1's declared figure, tier 2's `ds/Panel` (which
   * throws in development for a `money` panel with no `environment` — Requirement 7.4) and
   * that panel's `ds/TradingEnvironmentBadge` chip. `null` is a decision and not an omission:
   * §8.2's badge renders `ENVIRONMENT UNCONFIRMED` for it, which is what a record the server
   * did not label deserves. The strip at the top of the page says something else — what the
   * ROUTE is — and is not derived from this.
   */
  const serverEnvironment = tierOne[ENVIRONMENT_FIELD]?.available
    ? tierOne[ENVIRONMENT_FIELD].value
    : null;

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
  /*
   * The orders read is only in flight when it was issued at all. Without the venue test
   * `isReading` would report the disabled `idle` state as in-flight for ever, which would
   * leave the Refresh button spinning on an account with no single reported venue.
   */
  const ordersReading = ordersVenue !== null && isReading(ordersState);
  /* The same test for the union: no strategies means no requests, not a permanent spinner. */
  const deploymentsReading = strategyIds.length > 0 && isReading(deploymentsState);

  const tierThree = useMemo(
    () => buildTierThree(
      exchangePayload,
      // The SCOPED body, so `latestSignal`'s strategy-id filter is the selected deployment's
      // strategy rather than the sole reported one. The filter itself is unchanged; what it
      // filters by is what a selection re-scopes.
      scopedStrategies,
      latestOrderReport({
        venue: ordersVenue,
        failed: isFailure(ordersState),
        payload: ordersPayload,
      }),
    ),
    [exchangePayload, scopedStrategies, ordersVenue, ordersState, ordersPayload],
  );

  /**
   * Re-issue every read.
   *
   * Tiers 1 and 2 are one statement, so a retry that fixed half of it would not help; the
   * orders read is a separate failure surface but shares this one control, because a trader
   * pressing Refresh is asking for the page and not for one slot. `refetchOrders` is a no-op
   * while that read is disabled — `usePanelState` issues nothing from `idle` — so the button
   * is not a dead control on an account with no single venue: the other two reads still go.
   */
  const retry = useCallback(() => {
    refetchExchange();
    refetchStrategies();
    refetchOrders();
    // The union too: a trader pressing Refresh after a strategy's deployments failed to read
    // is asking for that list again as much as for the figures.
    refetchDeployments();
  }, [refetchExchange, refetchStrategies, refetchOrders, refetchDeployments]);

  /* ══════════════════════════════════════════════════════════════════════
   * THE THREE ACTIONS (task 20.3)
   * ══════════════════════════════════════════════════════════════════════
   *
   * One state object per action, `null` when its dialog is closed. Each is set by exactly
   * ONE `request…` handler and read by exactly one `handleConfirm…`, which is the whole of
   * Property 13's structural argument: a button sets state, and the mutation is reachable
   * only from the dialog's confirm action (see the section docblock above).
   *
   * The object also carries that request's own `busy` and `error`, rather than three more
   * pieces of page state. Both belong to one attempt on one target, and holding them apart
   * from it is how a refusal of a cancel ends up rendered over a stop.
   *
   * The review fields are read off the page's own readings HERE, at click time, so the
   * dialog keeps describing what the trader clicked even if a read answers underneath it —
   * `pages/Strategies.jsx`'s archive dialog does the same, for the same reason.
   */
  const [stopRequest, setStopRequest] = useState(null);
  const [cancelOrderRequest, setCancelOrderRequest] = useState(null);
  const [cancelAllRequest, setCancelAllRequest] = useState(null);

  /** The selected row's own reported deployment id, or `null` — the stop's path parameter. */
  const selectedDeploymentId = selectedRow === null
    ? null
    : scalarText(selectedRow[DEPLOYMENT_ID_KEY]);

  /** The newest open order as something `cancel_order` can address, or `null`. */
  const cancelTarget = useMemo(
    () => cancellableOrder({
      venue: ordersVenue,
      failed: isFailure(ordersState),
      payload: ordersPayload,
    }),
    [ordersVenue, ordersState, ordersPayload],
  );

  /**
   * How many open orders the venue reported, or `null` when that read did not answer.
   *
   * A review-grid figure only: `null` renders `ConfirmDialog`'s not-available marker, which is
   * the honest reading for a failed or unissued read. It gates nothing — see the section
   * docblock on why cancel-all does not depend on this read.
   */
  const openOrderCount = ordersVenue === null || isFailure(ordersState) || isReading(ordersState)
    ? null
    : String(openOrderRows(ordersPayload).length);

  /** The button's WHOLE job: open the dialog. It issues nothing. */
  const requestStopDeployment = useCallback(() => {
    if (selectedDeploymentId === null) return;
    setStopRequest({
      deploymentId: selectedDeploymentId,
      // The tier figures' own readings, so the dialog cannot describe a different strategy,
      // market or position from the one on the page. An unavailable reading travels as `null`
      // and renders the dialog's marker (Requirement 14.5), never a fabricated field.
      strategy: reviewValue(tierOne.strategy),
      market: reviewValue(tierOne.market),
      position: reviewValue(tierTwo.position),
      status: scalarText(selectedRow?.[DEPLOYMENT_STATUS_KEY]),
      environment: scalarText(selectedRow?.[DEPLOYMENT_ENVIRONMENT_KEY]),
      busy: false,
      error: null,
    });
  }, [selectedDeploymentId, selectedRow, tierOne, tierTwo]);

  /** The button's WHOLE job: open the dialog. It issues nothing. */
  const requestCancelOrder = useCallback(() => {
    if (cancelTarget === null) return;
    setCancelOrderRequest({ ...cancelTarget, venue: ordersVenue, busy: false, error: null });
  }, [cancelTarget, ordersVenue]);

  /** The button's WHOLE job: open the dialog. It issues nothing. */
  const requestCancelAll = useCallback(() => {
    if (ordersVenue === null) return;
    setCancelAllRequest({
      venue: ordersVenue,
      openCount: openOrderCount,
      busy: false,
      error: null,
    });
  }, [ordersVenue, openOrderCount]);

  /**
   * The ONLY caller of `strategiesApi.stopDeployment`.
   *
   * On success the dialog closes and every read is re-issued: the row's `status`, the position
   * and the risk state are the page's answer to "did that work?", and leaving the old reading
   * on screen would be this page's own failure mode. On failure the dialog STAYS OPEN carrying
   * the error, where §8.3 puts the retry.
   */
  const handleConfirmStop = useCallback(async () => {
    if (stopRequest === null || stopRequest.busy === true) return;
    const { deploymentId } = stopRequest;
    setStopRequest((current) => (current === null
      ? null
      : { ...current, busy: true, error: null }));
    try {
      await strategiesApi.stopDeployment(deploymentId, STOP_REASON);
      setStopRequest(null);
      retry();
    } catch (error) {
      setStopRequest((current) => (current === null
        ? null
        : { ...current, busy: false, error }));
    }
  }, [stopRequest, retry]);

  /** The ONLY caller of `ordersApi.cancelOrder`. Three arguments, all three route-required. */
  const handleConfirmCancelOrder = useCallback(async () => {
    if (cancelOrderRequest === null || cancelOrderRequest.busy === true) return;
    const { orderId, symbol, venue } = cancelOrderRequest;
    setCancelOrderRequest((current) => (current === null
      ? null
      : { ...current, busy: true, error: null }));
    try {
      await ordersApi.cancelOrder(orderId, symbol, venue);
      setCancelOrderRequest(null);
      retry();
    } catch (error) {
      setCancelOrderRequest((current) => (current === null
        ? null
        : { ...current, busy: false, error }));
    }
  }, [cancelOrderRequest, retry]);

  /**
   * The ONLY caller of `ordersApi.cancelAllOrders`, and it is reachable only through
   * `ConfirmDialog`'s acknowledgement gate.
   *
   * No `symbol`: this control is the every-market one, and the route reads an absent `symbol`
   * as exactly that. Narrowing it here would make the button do less than the dialog says.
   */
  const handleConfirmCancelAll = useCallback(async () => {
    if (cancelAllRequest === null || cancelAllRequest.busy === true) return;
    const { venue } = cancelAllRequest;
    setCancelAllRequest((current) => (current === null
      ? null
      : { ...current, busy: true, error: null }));
    try {
      await ordersApi.cancelAllOrders(venue);
      setCancelAllRequest(null);
      retry();
    } catch (error) {
      setCancelAllRequest((current) => (current === null
        ? null
        : { ...current, busy: false, error }));
    }
  }, [cancelAllRequest, retry]);

  /**
   * BOTH panels' state, because both tiers are projections of the same two reads.
   *
   * `loading` until both reads have answered, `refreshing` while one is being re-read over
   * figures already on screen, `ready` otherwise. `error` is unreachable here on purpose:
   * the failure is the page-level branch below, and a panel-level error would imply a panel
   * owns a read of its own. A degraded positions read is NOT this state — it is a state of a
   * successful response, and it is rendered inside tier 2 rather than as a panel failure.
   */
  const unanswered = (state) =>
    state === PANEL_STATES.IDLE || state === PANEL_STATES.LOADING;
  const readState = unanswered(exchangeState) || unanswered(strategiesState)
    ? PANEL_STATES.LOADING
    : (busy ? PANEL_STATES.REFRESHING : PANEL_STATES.READY);

  /**
   * Tier 3's panel state, which is the two reads' PLUS the orders read's.
   *
   * It is separate from `readState` because tiers 1 and 2 do not project the orders read, and
   * showing them as `refreshing` while a venue is being asked about its order book would claim
   * their own reads were in flight. The `loading` arm is what keeps
   * {@link ORDERS_ABSENCE}`.none` — "this venue reports no open order" — out of the DOM while
   * the request that would contradict it is still outstanding: §11.1 renders no children in
   * `loading`, so there is no window in which the marker states a completed read.
   *
   * A disabled orders read is ANSWERED for this purpose. There is nothing outstanding, the
   * slot's marker is final, and treating `idle` as loading would leave the tier as a skeleton
   * for ever on an account with no single reported venue.
   */
  const tierThreeState = ordersVenue !== null && unanswered(ordersState)
    ? PANEL_STATES.LOADING
    : (readState === PANEL_STATES.READY && ordersReading ? PANEL_STATES.REFRESHING : readState);

  /**
   * The selector's OWN panel state, which is the union read's and nothing else.
   *
   * Not `readState`: the selector is above tier 1 and projects neither of the two reads that
   * state shares, so borrowing it would show the selector as loading while the dashboard was
   * being re-read, and as ready while the union was still outstanding.
   *
   * `empty` is deliberately NOT among the outcomes, even though zero deployments is exactly
   * what `ds/Panel`'s `empty` state is for. Requirement 14.1's three fields are rendered — by
   * `ds/EmptyState`, as a child — because the empty rendering has to sit BESIDE
   * {@link REGISTRY_CAVEAT} and beside the partial-failure alert, and `empty` renders no
   * children at all: the panel would then say "nothing is running" with the sentence
   * explaining why that may be false suppressed. `error` is reachable only if the fan-out
   * itself throws rather than one of its calls failing, which {@link readDeploymentUnion}
   * settles — so it is wired for honesty, not because it is expected.
   */
  const selectorState = strategyIds.length === 0
    ? PANEL_STATES.READY
    : (isFailure(deploymentsState)
      ? deploymentsState
      : (unanswered(deploymentsState)
        ? PANEL_STATES.LOADING
        : (deploymentsReading ? PANEL_STATES.REFRESHING : PANEL_STATES.READY)));

  /** The rows whose own environment contradicts the LIVE route, which is worth surfacing. */
  const misEnvironmented = nonLiveDeployments(deploymentRows);

  /** Which empty case this is, in Requirement 14.1's three fields. */
  const selectorEmpty = emptySelectorCopy({
    strategyCount: strategyIds.length,
    unreadCount: unreadStrategies.length,
    retry,
  });

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
            // Every read this page makes, including the venue's order book and the
            // per-strategy deployment union: the control re-issues all four, so it reports
            // in-flight for all four.
            loading={busy || ordersReading || deploymentsReading}
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
        <>
        {/* ═══ THE DEPLOYMENT SELECTOR — UNTIERED, ABOVE TIER 1 (part D) ═══════════
            `data-region="deployment"` is the declared field's own key and there is NO
            `data-page-tier` anywhere in here: `pageHierarchy` registers `deployment` as
            untiered precisely because it sits above tier 1 and chooses what the tiers
            describe, so giving it a tier would put a control into Property 4's ordering of
            figures.

            NOT a `money` panel. Its columns are ids, states, a worker name and two words
            about health; `ds/Panel`'s `money` assertion is for balances, P&L and positions,
            and a second environment badge here would imply these rows are amounts. The
            per-row `environment` column is the deployment record's OWN label, and where it
            contradicts this route the alert below says so rather than the badge. */}
        <Panel
          title="Deployments"
          state={selectorState}
          loading={{ kind: "skeleton-table", rows: 3, columns: DEPLOYMENT_COLUMNS.length }}
          // Reachable only if the fan-out itself throws — see `selectorState`. It fails HERE
          // and not page-wide, for part C's reason: a selector that cannot be read must not
          // erase figures about real money that were read successfully.
          error={{ error: deploymentsError, context: "live-trading", onRetry: retry }}
          actions={selectedRow === null ? null : (
            <div
              className="flex min-w-0 items-center gap-2"
              data-selected-deployment={deploymentLabel(selectedRow)}
            >
              <span className="text-micro uppercase tracking-wide text-content-secondary">
                Tiers scoped to
              </span>
              <span className="font-mono text-micro font-bold uppercase tracking-wide text-content-primary">
                {deploymentLabel(selectedRow)}
              </span>
              {/* Rendered only while something is selected, so it always has something to
                  clear (Requirement 19.4). */}
              <CommandButton intent="secondary" onClick={clearDeployment}>
                Show all deployments
              </CommandButton>
              {/* ── STOP, on the SELECTED row (task 20.3) ────────────────────────────
                  Rendered only for a row that REPORTS a deployment id: the path parameter
                  `POST /api/deployments/{id}/stop` needs is the server's id, and a row that
                  reported none carries only this page's synthetic union key, which addresses
                  nothing. An absent control beats one that 404s on a live account.

                  `onClick` opens the dialog and does nothing else — the request lives in
                  `handleConfirmStop`, which the dialog alone calls (Property 13). */}
              {selectedDeploymentId === null ? null : (
                <CommandButton
                  intent="destructive"
                  onClick={requestStopDeployment}
                  data-live-action="stop-deployment"
                >
                  Stop deployment
                </CommandButton>
              )}
            </div>
          )}
          data-region={DEPLOYMENT_FIELD}
        >
          <div className="flex min-w-0 flex-col gap-3">
            {/* ── The incompleteness, ONCE, above the rows it qualifies ─────────────
                A sentence and not an alert: it is a permanent property of the read rather
                than a condition, and an always-on warning banner is the one that stops being
                read. See the module docblock. */}
            <p
              className="text-small text-content-secondary"
              data-selector-caveat="in-process-registry"
            >
              {REGISTRY_CAVEAT}
            </p>
            {/* What a selection does and does not re-scope, stated where the expectation is
                formed rather than only in each account-wide slot's hint. */}
            <p
              className="text-small text-content-secondary"
              data-selector-caveat="selection-scope"
            >
              {SELECTION_CAVEAT}
            </p>

            {/* ── Partial fan-out failure, DISCLOSED (part C's precedent) ───────────
                The strategies that answered keep their rows; the ones that did not are named,
                so a short list is never read as a complete one. */}
            {unreadStrategies.length === 0 ? null : (
              <Alert
                severity="warning"
                title="Some strategies' deployments could not be read"
                action={{ label: "Try again", onClick: retry }}
                data-deployments-read="partial"
              >
                {`${unreadStrategies.length} of ${strategyIds.length} strategies did not answer, `
                  + "so any deployment they hold is missing from the list below: "
                  + `${unreadStrategies.join(", ")}. The rest answered and their deployments are `
                  + "shown."}
              </Alert>
            )}

            {/* ── A row's own environment against the LIVE route ───────────────────
                Surfaced, not hidden, and not suppressed to a selected row: a trader about to
                scope this page by a row that is not trading real money should read that
                before clicking it, not after. The strip above is unaffected — it states what
                the ROUTE is, which is still true. */}
            {misEnvironmented.length === 0 ? null : (
              <Alert
                severity="warning"
                title="A deployment below is not labelled live"
                data-deployments-environment="disagrees"
              >
                {`This page reads the live ledger, but ${misEnvironmented.length} of the `
                  + `${deploymentRows.length} deployments listed reports its own environment as `
                  + `something else: ${misEnvironmented
                    .map((row) => `${deploymentLabel(row)} (${scalarText(row[DEPLOYMENT_ENVIRONMENT_KEY])})`)
                    .join(", ")}. The label is the deployment record's, not this route's.`}
              </Alert>
            )}

            {/* ── The rows, or Requirement 14.1's three fields ─────────────────────
                Never an empty table: a header row over nothing says "no deployments" in a
                form that cannot say which emptiness this is, and a selector that selects
                nothing is the dead control Requirement 19.4 forbids. */}
            {deploymentRows.length === 0 ? (
              <EmptyState
                icon={Server}
                headline={selectorEmpty.headline}
                body={selectorEmpty.body}
                action={selectorEmpty.action}
                data-deployments-empty="true"
              />
            ) : (
              <DataTable
                caption={
                  "Deployments reported for your strategies. Select one to scope the strategy, "
                  + "market and latest signal below to it."
                }
                columns={DEPLOYMENT_COLUMNS}
                rows={deploymentRows}
                getRowId={rowIdOf}
                onRowClick={selectDeployment}
                // The union is the whole list and it is short; `0` disables pagination, so no
                // page control is rendered that has nothing to page (Requirement 19.4).
                pageSize={0}
                stickyHeader
              />
            )}
          </div>
        </Panel>

        {/* ═══ TIER 1 — Requirement 7.1 ═══════════════════════════════════════════
            ONE container, six slots, walked from `pageHierarchy`'s tier-1 list — so a
            seventh figure cannot appear here without being declared, the order is the
            declaration's, and `Metric tier={1}` exists nowhere else on the page. The
            container carries `data-page` + `data-page-tier`, so both claims are decidable
            from the rendered DOM (task 20.4). */}
        <Panel
          title="Execution attribution"
          state={readState}
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
                  environment={serverEnvironment}
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

        {/* ═══ TIER 2 — Requirement 7.2, and Requirement 7.4 on the panel ═════════
            A money panel: `money` with an `environment` is the pair `ds/Panel` asserts on,
            so this panel cannot ship without an environment decision, and `null` — the
            server labelled none — is a decision it accepts and renders as
            `ENVIRONMENT UNCONFIRMED`. The `chip` beside it is §8.2's own badge on a panel
            showing position and P&L data (Requirement 12.2). */}
        <Panel
          title="Position and risk"
          money
          environment={serverEnvironment}
          state={readState}
          loading={{ kind: "skeleton-metric", rows: 1, columns: 7 }}
          actions={<TradingEnvironmentBadge variant="chip" environment={serverEnvironment} />}
          data-region="tier-2"
        >
          <div className="flex min-w-0 flex-col gap-4">
            {/* ── BC-2's degraded arm ───────────────────────────────────────────────
                The server's own sentence, verbatim and not paraphrased: it knows which
                environment failed and why, `translateError` carries no free-form message by
                design (Requirement 14.4), and this is the only place that account reaches
                the screen. It sits ABOVE the row it explains, and the row's position
                figure is the marker carrying this same reason — never a flat position. */}
            {positionsDegraded === null ? null : (
              <Alert
                severity="warning"
                title="The server could not read your open positions"
                action={{ label: "Try again", onClick: retry }}
                data-positions-read="degraded"
              >
                {positionsDegraded}
              </Alert>
            )}

            {/* Seven equal-weight slots in ONE row, walked from `pageHierarchy`'s tier-2
                list, each carrying `data-region` spelled as its `pageFields` key. `flex-1`
                from a zero basis, as tier 1 does and for the same reason: `grid-cols-7` is
                not a utility this build emits, so it would compile to nothing. */}
            <div
              {...{ [TIER_PAGE_ATTRIBUTE]: PAGES.LIVE_TRADING, [TIER_ATTRIBUTE]: 2 }}
              className="flex min-w-0 items-start gap-4"
            >
              {TIER_TWO.map(({ key, label }) => (
                <Metric
                  key={key}
                  tier={2}
                  // The DECLARED label, which for the realised-P&L pair is load-bearing:
                  // "Realised P&L (account, today)" beside "Realised P&L (this deployment)"
                  // is what stops the account-wide figure being read as a deployment's.
                  label={label}
                  value={tierTwo[key]}
                  {...TIER_TWO_FORMAT[key]}
                  hint={TIER_TWO_HINT[key] ?? fieldEntry(key)?.tooltip ?? undefined}
                  className="flex-1"
                  data-region={key}
                />
              ))}
            </div>
          </div>
        </Panel>

        {/* ═══ TIER 3 — Requirement 7.3 ═══════════════════════════════════════════
            "What happened last?", and the tier where the page's subject — attribution —
            is enforced rather than caveated: `latestSignal` is FILTERED by strategy id and
            renders the marker rather than the account's newest signal when nothing matches.

            NOT a `money` panel. Its three readings are a sentence, an open order and a
            status word; `money` is `ds/Panel`'s assertion for a surface showing balances,
            P&L or positions, and declaring it here would put a second environment badge on
            the page for figures that are not amounts. Tier 2 is where the money is, and it
            carries the badge Requirements 7.4 and 12.2 ask for.

            The orders read's failure does NOT come out here as a panel error: it belongs to
            one slot, and a panel-level error would suppress the two neighbours that read
            successfully off the dashboard. See the module docblock. */}
        <Panel
          title="What happened last"
          state={tierThreeState}
          loading={{ kind: "skeleton-metric", rows: 1, columns: 3 }}
          /* ── THE TWO ORDER CONTROLS (task 20.3) ────────────────────────────────────
             Here rather than anywhere else because this is the panel that NAMES the order:
             `latestOrder` is the reading `cancelTarget` addresses, and a cancel control
             separated from the thing it cancels is how the wrong order gets cancelled.

             Each is rendered only where its route can be satisfied — the target for one, the
             venue for all — and neither is rendered at all when there is nothing addressable,
             so the header carries no empty control group. Both buttons only open a dialog. */
          actions={cancelTarget === null && ordersVenue === null ? null : (
            <div className="flex min-w-0 items-center gap-2" data-live-actions="orders">
              {cancelTarget === null ? null : (
                <CommandButton
                  intent="destructive"
                  onClick={requestCancelOrder}
                  data-live-action="cancel-order"
                >
                  Cancel live order
                </CommandButton>
              )}
              {ordersVenue === null ? null : (
                <CommandButton
                  intent="destructive"
                  onClick={requestCancelAll}
                  data-live-action="cancel-all-orders"
                >
                  Cancel all orders
                </CommandButton>
              )}
            </div>
          )}
          data-region="tier-3"
        >
          {/* Three equal-weight slots in ONE row, walked from `pageHierarchy`'s tier-3 list,
              each carrying `data-region` spelled as its `pageFields` key. `flex-1` from a
              zero basis, as tiers 1 and 2 do: a bare `grid-cols-*` above 4 is not a utility
              this build emits, so it would compile to nothing (design.md §1.2). */}
          <div
            {...{ [TIER_PAGE_ATTRIBUTE]: PAGES.LIVE_TRADING, [TIER_ATTRIBUTE]: 3 }}
            className="flex min-w-0 items-start gap-4"
          >
            {TIER_THREE.map(({ key, label }) => (
              <Metric
                key={key}
                tier={3}
                label={label}
                value={tierThree[key]}
                // `raw` for all three: a signal sentence, a side-size-market reading and a
                // status word. None is a quantity, and a numeric format would group and
                // round the size out of the middle of the order reading.
                format="raw"
                hint={TIER_THREE_HINT[key] ?? fieldEntry(key)?.tooltip ?? undefined}
                className="flex-1"
                data-region={key}
              />
            ))}
          </div>
        </Panel>

        {/* ═══ THE THREE CONFIRMATIONS (task 20.3) ═════════════════════════════════
            Requirements 7.6, 8.1, 8.3, 8.5. All three go through `ds/ConfirmDialog`, so all
            three get the focus trap, Escape, initial focus on CANCEL, the single-overlay
            claim and an error rendering that cannot leak `err.message`.

            Only one can be open: each `open` reads its own state object, and each object is
            set by exactly one `request…` handler and by nothing else — so the overlay
            registry's one-overlay rule is never actually contested, which is the argument
            `pages/Strategies.jsx` makes about its own pair.

            THE ACKNOWLEDGEMENT IS ON CANCEL-ALL AND ON NOTHING ELSE. §8.4 spends one on the
            bulk irreversible action and refuses one to everything risk-reducing; off that
            action there is no `acknowledgement` object at all — it is not constructed and
            hidden, it does not exist. The `environment="LIVE"` badge IS on all three,
            because every action on this route acts on the live ledger (Requirement 8.5) and
            that is a different claim from the risk class. See the section docblock. */}

        {/* ── Stop the selected deployment. NO acknowledgement: stopping reduces risk. ── */}
        <ConfirmDialog
          open={stopRequest !== null}
          onCancel={() => setStopRequest(null)}
          onConfirm={handleConfirmStop}
          title="Stop this deployment"
          intent="destructive"
          environment="LIVE"
          description={STOP_DESCRIPTION}
          /* The labels are authored and the VALUES are the page's own readings, so the
             dialog cannot describe a different strategy, market or position from the one
             above it. "Strategy" rather than the declared "Strategy (version)": in a stop
             review that label reads as the version the worker is bound to, and nothing on
             this page reports that. An unreadable field is the marker, never invented. */
          review={[
            { label: "Deployment", value: stopRequest?.deploymentId ?? null },
            { label: "Strategy", value: stopRequest?.strategy ?? null },
            { label: "Market", value: stopRequest?.market ?? null },
            { label: "Current position", value: stopRequest?.position ?? null },
            { label: "Reported status", value: stopRequest?.status ?? null },
            { label: "Deployment's own environment", value: stopRequest?.environment ?? null },
            { label: "Open position", value: "Not closed by this action" },
            { label: "Resting orders", value: "Not cancelled by this action" },
          ]}
          confirmLabel="Stop deployment"
          cancelLabel="Leave it running"
          busy={stopRequest?.busy === true}
          busyLabel="Stopping…"
          error={stopRequest?.error ?? undefined}
          errorContext="live-trading"
        />

        {/* ── Cancel ONE resting order. NO acknowledgement: one named, re-placeable order. ── */}
        <ConfirmDialog
          open={cancelOrderRequest !== null}
          onCancel={() => setCancelOrderRequest(null)}
          onConfirm={handleConfirmCancelOrder}
          title="Cancel this order"
          intent="destructive"
          environment="LIVE"
          description={CANCEL_ORDER_DESCRIPTION}
          review={[
            { label: "Order", value: cancelOrderRequest?.reading ?? null },
            { label: "Order id", value: cancelOrderRequest?.orderId ?? null },
            { label: "Market", value: cancelOrderRequest?.symbol ?? null },
            { label: "Venue", value: cancelOrderRequest?.venue ?? null },
          ]}
          confirmLabel="Cancel this order"
          cancelLabel="Leave it resting"
          busy={cancelOrderRequest?.busy === true}
          busyLabel="Cancelling…"
          error={cancelOrderRequest?.error ?? undefined}
          errorContext="live-trading"
        />

        {/* ── Cancel EVERY resting order at the venue. THE acknowledgement (§8.4). ──────
            Bulk, every market, and nothing re-places what it removes. The count is what the
            open-orders read knows; a read that failed or was never issued renders the
            dialog's marker there rather than a `0` that would read as "nothing to cancel". */}
        <ConfirmDialog
          open={cancelAllRequest !== null}
          onCancel={() => setCancelAllRequest(null)}
          onConfirm={handleConfirmCancelAll}
          title="Cancel all open orders"
          intent="destructive"
          environment="LIVE"
          description={CANCEL_ALL_DESCRIPTION}
          review={[
            { label: "Venue", value: cancelAllRequest?.venue ?? null },
            { label: "Scope", value: "Every open order at this venue, in every market" },
            { label: "Open orders reported", value: cancelAllRequest?.openCount ?? null },
            { label: "Open position", value: "Not closed by this action" },
          ]}
          acknowledgement={CANCEL_ALL_ACKNOWLEDGEMENT}
          confirmLabel="Cancel all orders"
          cancelLabel="Leave them resting"
          busy={cancelAllRequest?.busy === true}
          busyLabel="Cancelling…"
          error={cancelAllRequest?.error ?? undefined}
          errorContext="live-trading"
        />
        </>
      )}
    </div>
  );
}
