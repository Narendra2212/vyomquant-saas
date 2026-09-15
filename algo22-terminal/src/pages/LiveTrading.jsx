/**
 * ═══════════════════════════════════════════════════════════════════════════
 * pages/LiveTrading — what is actually running, with real money (`/app/live-trading`)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 20.1: part A the page shell, the route and tier 1; part B the
 * tier-2 money row. design.md §7.5, §8.1, §8.2, §11.1.
 * Requirements 1.3, 7.1, 7.2, 7.4, 12.2, 14.4, 14.5, 19.3.
 *
 * Until this task `/app/live-trading` rendered `pages/Dashboard`, so the route existed and
 * answered a different question — the account's capital — than the one Requirement 7.1
 * asks: is this thing actually running, and against what?
 *
 * WHAT THIS PAGE IS, AND WHAT IT IS NOT YET
 * -----------------------------------------
 * Tiers 1 and 2. §7.5's tier 3 (the latest signal, the latest order, the execution status)
 * and the deployment selector above tier 1 are declared in `design/pageHierarchy.js` and are
 * the next part of task 20.1; nothing here stubs them, because a stub of a money figure is
 * the fabrication Requirement 14.5 is about, and a selector that selects nothing is the dead
 * control Requirement 19.4 is about.
 *
 * Tier 2 therefore describes the ACCOUNT's one open position rather than a chosen
 * deployment's, and every one of its slots says so in its label or its hint. The two
 * account-wide fields are the ones that would mislead if they did not: `risk.risk_level` and
 * `overview.today_realized_pnl` are one state and one sum for the whole account.
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
import { Alert } from "../components/ds/Alert";
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

  /** Re-issue both reads. One statement, so a retry that fixed half of it would not help. */
  const retry = useCallback(() => {
    refetchExchange();
    refetchStrategies();
  }, [refetchExchange, refetchStrategies]);

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
        <>
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
        </>
      )}
    </div>
  );
}
