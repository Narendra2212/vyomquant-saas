import React, { useState, useEffect, useMemo, useCallback } from "react";
import { useNavigate, useLocation, useSearchParams } from "react-router-dom";
import {
  Plus, Layers, TrendingUp, Edit2,
  BarChart2, Pause, Play, Trash2, Copy, Settings,
  Activity, Zap, Globe, Shield, RefreshCw, AlertTriangle
} from "lucide-react";
import { endpoints, api } from "../api";
import { CONFIG } from "../config";
import { Tag2 } from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
// Task 10.3: the two confirmation surfaces. Task 17.1: the table, the filter row and the
// four states. Imported from the modules directly rather than through
// `components/ds/index.js` — this page charts nothing, and the barrel is what would
// otherwise put `ds/Chart`'s recharts dependency in its import graph (see ds/index.js).
import { Alert } from "../components/ds/Alert";
import { CommandButton } from "../components/ds/CommandButton";
import { ConfirmDialog } from "../components/ds/ConfirmDialog";
import { DataTable } from "../components/ds/DataTable";
import { EmptyState } from "../components/ds/EmptyState";
import { Field } from "../components/ds/Field";
import { FilterBar } from "../components/ds/FilterBar";
import { NotAvailableMarker } from "../components/ds/Metric";
import { PageHeader } from "../components/ds/PageHeader";
import { Panel } from "../components/ds/Panel";
import { StatusBadge, humaniseState } from "../components/ds/StatusBadge";
import { STRATEGY_STATUSES, StrategyStatus } from "../components/ds/StrategyStatus";
import { PAGES, PAGE_FIELDS_BY_PAGE, VERDICT } from "../design/pageFields";
import { PANEL_STATES } from "../hooks/usePanelState";
import StrategyBuilder from "./StrategyBuilder";
import {
  HEALTH_ERROR,
  HEALTH_UNDETERMINED,
  computeStrategyHealth,
} from "../lib/strategyHealth";
import {
  describeArchiveFailure,
  describeBlockingDeployment,
} from "../lib/strategyArchive";
import { deploymentRequest } from "../lib/deployPreflight";
import { useDeployPreflight } from "../hooks/useDeployPreflight";
import DeployPreflightPanel from "../components/DeployPreflightPanel";

// ── Normaliser helpers — module scope so action handlers can reference them ──

/**
 * A recorded scalar as non-blank text, or `null`.
 *
 * Task 17.1 replaced this normaliser's placeholder defaults — `"N/A"` for a market and a
 * timeframe, `"Custom"` for a type, `"1.0"` for a version, `"paper"` for an environment,
 * `0` for P&L, win rate and drawdown — with `null`, because every one of them was a value
 * the list projection does not carry. `"N/A"` in a cell is indistinguishable from a market
 * literally called N/A, and a `0%` P&L reads as a strategy that has never made money
 * (Requirement 14.5). `null` reaches the table's not-available marker, which says so and
 * carries `pageFields`' reason.
 */
const _text = (value) => {
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : null;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
};

const _normalizeStatus = (value = "") => {
  const s = String(value).toLowerCase();
  if (["running", "active", "live", "started"].includes(s)) return "running";
  if (["paused", "pause"].includes(s)) return "paused";
  if (["backtesting", "testing"].includes(s)) return "backtesting";
  if (["draft"].includes(s)) return "draft";
  if (["failed", "error"].includes(s)) return "failed";
  if (["stopped", "inactive", "stop"].includes(s)) return "stopped";
  return "stopped";
};

/**
 * §7.2's "Market / exchange" cell, as one projected string.
 *
 * The three keys `pageFields`' `market` entry names — `symbol`, `deployed_exchange` and
 * `timeframe` — joined, rather than rendered from three unprojected reads off `row`.
 * `DataTable` memoises a row on its PROJECTED cell values and deliberately excludes the row
 * object from that compare, so a cell that reaches past the projection can hold a stale
 * value; one value per column is what keeps the cell and the memo agreeing.
 */
const _marketLabel = (row) => {
  const parts = [
    _text(row.symbol) ?? _text(row.pair),
    _text(row.deployed_exchange),
    _text(row.timeframe) ?? _text(row.tf),
  ].filter((part) => part !== null);
  return parts.length === 0 ? null : parts.join(" · ");
};

const normalizeStrategies = (rows = []) =>
  (Array.isArray(rows) ? rows : []).map((row, i) => ({
    id: row.id ?? row.strategy_id ?? i + 1,
    name: _text(row.name) ?? _text(row.strategy_name),
    pair: _text(row.symbol) ?? _text(row.pair),
    market: _marketLabel(row),
    // The server's spelling, verbatim, for `ds/StrategyStatus` — which reports "Status not
    // reported" for a row carrying none rather than claiming `stopped`, the way
    // `_normalizeStatus` has to for the filter's closed vocabulary.
    statusRaw: _text(row.status),
    status: _normalizeStatus(row.status),
    // Requirement 3.3 / `pageFields`' `status` entry: an archived row is history, not an
    // actionable status, so the flag is read beside the status rather than folded into it.
    isArchived: row.is_archived === true,
    errorMessage: row.error_message || row.error || row.reason || null,
    tf: _text(row.timeframe) ?? _text(row.tf),
    current_version: _text(row.current_version) ?? _text(row.version),
    // `_LIST_COLUMNS` carries no `environment`. Kept as a nullable read for the archive
    // confirmation's review grid, which renders the not-available marker for it; the filter
    // row does not offer an environment control at all (see WITHHELD_FILTERS).
    environment: _text(row.environment),
    created_at: row.created_at,
    updated_at: row.updated_at,
    // BC-3 and BC-4, landed in task 12. Both keys are ALWAYS present on the response and
    // `null` when there is nothing to report, so neither is defaulted here.
    last_signal_at: row.last_signal_at ?? null,
    last_execution_at: row.last_execution_at ?? null,
    // `is_active` is the row's own deployment flag. It reports whether the strategy is
    // marked active and claims no live worker — the deployment RECORD is a separate read
    // this page does not make. Absent reads not-available, never "inactive".
    deploymentState:
      row.is_active === true ? "active" : row.is_active === false ? "inactive" : null,
    is_running: row.is_running === true,
    // Requirements 1.8/1.9: health is computed from this row's own most-recent deployment
    // status and most-recent backtest outcome, and from nothing else. It was
    // `row.health ?? "healthy"`, which reported every strategy as healthy — including one
    // whose deployment had failed — because the list endpoint reports no `health` field at
    // all. See src/lib/strategyHealth.js for the mapping and for why `undetermined` is the
    // correct reading of a row that carries neither record.
    most_recent_deployment: row.most_recent_deployment ?? null,
    most_recent_backtest: row.most_recent_backtest ?? null,
    health: computeStrategyHealth(row),
  }));

/* ══════════════════════════════════════════════════════════════════════════
 * §7.2's TEN COLUMNS — labels and reasons READ from `design/pageFields.js`
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Task 17.1. Requirements 4.1, 4.4, 4.5, 14.5, 19.3.
 *
 * The card grid this replaced is what Requirement 4.5 forbids: one 220px-tall card per
 * strategy carrying eight tags, three metric tiles, a progress bar and seven buttons, for
 * ten fields. `DataTable` rows satisfy 4.5 by construction.
 *
 * THREE OF THE TEN HAVE NOTHING BEHIND THEM, AND SAY SO
 * ----------------------------------------------------
 * `pageFields` declares `performance` and `riskState` ❌ on this read, and `market`'s and
 * `deploymentState`'s notes record that §7.2's `row.pnl` / `row.win_rate` / `row.max_dd` /
 * `row.health` / `row.is_running` / `row.most_recent_deployment` are on no strategy
 * projection at all. So:
 *
 *   * **Performance** renders the marker for every row, carrying the declaration's own
 *     sentence. Not `0`: a zero P&L reads as a strategy that has never made money, which is
 *     a different claim from "this list does not report performance" (Requirement 14.5).
 *   * **Risk** is `computeStrategyHealth(row)`, whose `undetermined` — the answer for every
 *     row today, because both permitted sources are absent from the listing — renders the
 *     marker. The `row.health ?? "healthy"` default it replaced reported every strategy as
 *     healthy, including one whose only deployment had failed.
 *   * **Last signal** (BC-3) and **Last execution** (BC-4) are real keys, always present and
 *     `null` when unreported, and render the marker with each entry's own reason.
 */

const STRATEGY_FIELDS = PAGE_FIELDS_BY_PAGE[PAGES.STRATEGIES] ?? [];

/** One field's declaration, or `null`. */
const fieldEntry = (field) => STRATEGY_FIELDS.find((e) => e.field === field) ?? null;

/** The column header §7.2 names for a field. Read, not restated. */
const labelFor = (field) => fieldEntry(field)?.label ?? field;

/**
 * The sentence rendered instead of a value, from the declaration.
 *
 * `undefined` rather than `null` when a field declares none, so `NotAvailableMarker` falls
 * back to its own `UNREPORTED_REASON` instead of being handed an empty reason.
 */
const reasonFor = (field) => fieldEntry(field)?.reason ?? undefined;

/** Whether the read reports the field at all. */
const isReported = (field) => fieldEntry(field)?.verdict === VERDICT.AVAILABLE;

/** Requirement 11.4 — a page of strategies, not a page of trades. */
const PAGE_SIZE = 25;

/** Newest first: the strategy a trader touched last is the one they are looking for. */
const DEFAULT_SORT = Object.freeze({ key: "updated_at", direction: "desc" });

/** What the filter row's live region counts. */
const COUNT_NOUN = "strategies";

/** An example, never a label (Requirement 15.1). */
const SEARCH_PLACEHOLDER = "BTC/USDT";

/**
 * The status segments: "All", then `ds/StrategyStatus`'s own six-state vocabulary.
 *
 * Read from the primitive rather than re-listed, so the segments and the badge cannot
 * disagree about which states exist. §7.2's sketch draws five of the six; `backtesting` and
 * `stopped` are included because `_normalizeStatus` can produce both, and a state a row can
 * hold with no segment to select it is a row a trader cannot filter to.
 */
const STATUS_FILTER_ALL = "all";
const STATUS_FILTER_OPTIONS = Object.freeze([
  Object.freeze({ value: STATUS_FILTER_ALL, label: "All" }),
  ...STRATEGY_STATUSES.map((state) =>
    Object.freeze({ value: state, label: humaniseState(state) })),
]);

/**
 * The controls §7.2 sketches that this read cannot support, each with the declaration's
 * reason.
 *
 * §7.2 draws an `[Environment ▾]` select. `GET /api/strategies` reports no environment —
 * `_LIST_COLUMNS` has no such key — so the select is **not rendered** and this sentence is
 * shown in its place. An environment control over the `row.environment ?? "paper"` default
 * it would have had to read would match every row or none, and Requirement 11.2 asks for
 * filters that work rather than for filters.
 */
const WITHHELD_FILTERS = Object.freeze([
  Object.freeze({
    id: "environment",
    label: labelFor("environment"),
    reason: reasonFor("environment"),
  }),
]);

/** Requirement 4.4's empty state. The body says what an empty list means, not that it is empty. */
const NO_STRATEGIES_STATE = Object.freeze({
  icon: Layers,
  headline: "No strategies yet",
  body: "A strategy is the thing that places orders for you — the platform refuses manual "
    + "ones. Nothing trades until you build one and deploy it, so this list stays empty "
    + "until then.",
  action: Object.freeze({ label: "Open the strategy builder", to: "/app/builder" }),
});

/** `undetermined` has no entry: it is an absence, not a verdict. Mirrors `ds/StrategyStatus`. */
const HEALTH_BADGE_STATE = Object.freeze({
  healthy: "healthy",
  degraded: "degraded",
  error: "error",
});

/* ── The cells ─────────────────────────────────────────────────────────── */

/**
 * The name, plus the deep-link marker.
 *
 * `focusedId` is closed over rather than read from a column, which is why the column list
 * is memoised on it: `DataTable`'s row memo compares projected cell values and the
 * `columns` identity, so a focus change has to move one of the two.
 */
const nameCell = (focusedId) => function NameCell({ value, row }) {
  const focused = focusedId !== null && String(row.id) === String(focusedId);
  return (
    <span className="inline-flex min-w-0 items-center gap-2">
      <span className="min-w-0">
        {value === null || value === undefined
          ? <NotAvailableMarker label={labelFor("name")} />
          : value}
      </span>
      {/* The card grid's "★ FOCUSED TARGET STRATEGY" banner, at row scale. `focused` is
          outside `statusToken`'s vocabulary, so it resolves to the neutral group: a
          deep-link marker is not a status and must not read as one. */}
      {focused ? (
        <StatusBadge state="focused" label="Focused target" size="sm" />
      ) : null}
    </span>
  );
};

/** Status through `ds/StrategyStatus`, with `is_archived` read beside it. */
function StatusCell({ row }) {
  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-1">
      <StrategyStatus
        status={row.statusRaw}
        isRunning={row.is_running}
        health={row}
        compact
      />
      {row.isArchived ? <StatusBadge state="archived" label="Archived" size="sm" /> : null}
    </span>
  );
}

/**
 * The risk state, from `computeStrategyHealth`'s already-computed answer.
 *
 * `undetermined` renders the marker carrying the declaration's sentence. There is no path
 * to a healthy badge that does not originate in that function.
 */
function RiskCell({ value }) {
  // `undetermined` is named explicitly, and anything outside the vocabulary lands in the
  // same arm — never in a cheerful one. There is no path from an unrecognised string to a
  // healthy badge, which is the defect `row.health ?? "healthy"` was.
  const badge = value === HEALTH_UNDETERMINED ? undefined : HEALTH_BADGE_STATE[value];
  return badge === undefined
    ? <NotAvailableMarker label={labelFor("riskState")} reason={reasonFor("riskState")} />
    : <StatusBadge state={badge} label={humaniseState(value)} size="sm" />;
}

/** The deployment flag as text. Absent reads not-available, never "inactive". */
function DeploymentCell({ value }) {
  if (value === "active") return <StatusBadge state="active" label="Active" size="sm" />;
  if (value === "inactive") return <StatusBadge state="idle" label="Not active" size="sm" />;
  return (
    <NotAvailableMarker
      label={labelFor("deploymentState")}
      reason={reasonFor("deploymentState")}
    />
  );
}

/** Plain recorded text, or the marker with the field's own reason. */
const textCell = (field) => function TextCell({ value }) {
  return value === null || value === undefined || value === ""
    ? <NotAvailableMarker label={labelFor(field)} reason={reasonFor(field)} />
    : value;
};

/** The marker a column renders for every row when the read reports nothing for it. */
const unreportedCell = (field) => function UnreportedCell() {
  return <NotAvailableMarker label={labelFor(field)} reason={reasonFor(field)} />;
};

/**
 * A recorded instant as local text, or the marker with the field's own reason.
 *
 * `formatInstant` is declared below and reached at RENDER time, not at module evaluation,
 * which is what lets the cells stay together here. It returns the raw value for a string
 * that is not a parseable instant, so a malformed timestamp is shown rather than hidden.
 */
const instantCell = (field) => function InstantCell({ value }) {
  const text = formatInstant(value);
  return text === null
    ? <NotAvailableMarker label={labelFor(field)} reason={reasonFor(field)} />
    : text;
};

// ─────────────────────────────────────────────────────────────────────────────
// Server-derived ownership: GET /api/library/my-strategies (task 32.7)
// Requirements 12.2, 12.3, 12.4, 12.5, 12.6, 12.8.
//
// This read sits BESIDE `endpoints.strategies.list()`, which is untouched: that call is what
// the owner's card grid below is built from, and nothing about it changes. What is added is
// the combined owned-and-subscribed list, whose `ownership` label, `subscription` triple,
// `entitling` flag, `unavailable_reason` code and `allowed_actions` list are all decided by
// `backend_app/backend/marketplace/library_entries.py` and rendered here EXACTLY as returned.
//
// WHY THE AFFORDANCES ARE A LOOKUP AND NOT A CONDITION
//   The rendered buttons are produced by mapping over `entry.allowed_actions` and looking each
//   member up in `ACTION_CATALOG`. There is no `ownership === "SUBSCRIBED" ? … : …` anywhere in
//   the affordance path and no hardcoded button list gated by a flag, so the page is
//   structurally incapable of offering an action the server did not return — an action absent
//   from `allowed_actions` is never iterated over, so its button is never constructed. The
//   catalogue is the page's own vocabulary of what it can DO with an action; an action the
//   server returns that the catalogue does not describe renders nothing, which narrows the
//   offer and can never widen it.
//
//   `allowed_actions` is an affordance list, never an authorisation: Requirement 12.7's 403 is
//   each restricted route's own, and the two disabled-execution chips below say so in words.
// ─────────────────────────────────────────────────────────────────────────────

/** The three actions that execute the strategy. Requirement 12.5 disables every one of them
 *  on an entry whose Subscription does not entitle, while still offering renewal. Named once,
 *  so the disabled state and the catalogue cannot disagree about which actions execute. */
const EXECUTION_ACTIONS = ["run_backtest", "deploy_live", "start_paper"];

/** `unavailable_reason` — the wire code `library_entries.entitlement_reason` maps the
 *  Entitlement_Resolver's own reason onto. `null` while the entry entitles. Spelled here as the
 *  server spells it, so the page's expired / unavailable-strategy states and the eventual
 *  refusal name the same condition. */
const REASON_TEXT = {
  MARKETPLACE_SUBSCRIPTION_EXPIRED:
    "This subscription period has ended, so it no longer entitles you to run this strategy.",
  MARKETPLACE_NOT_SUBSCRIBED:
    "No entitling subscription is held for this listing.",
  MARKETPLACE_STRATEGY_UNAVAILABLE:
    "This listing is unavailable — the strategy behind it cannot be resolved right now.",
  MARKETPLACE_OPERATION_NOT_PERMITTED:
    "This subscription is suspended, so execution is not permitted right now.",
};

/** The one place the page decides what a `MARKETPLACE_*` reason code means in words. An
 *  unrecognised code is reported verbatim rather than being softened into a generic sentence,
 *  so a code this build has not seen is still visible to the user and to support. */
const reasonText = (code) =>
  (code && REASON_TEXT[code]) || (code ? `Refused by the server: ${code}` : null);

/** A subscribed entry's Listing identifier as a query value, or `null`. */
const listingParam = (entry) =>
  entry?.listing_id ? encodeURIComponent(String(entry.listing_id)) : null;

/** An owned entry's strategy identifier as a query value, or `null`. */
const strategyParam = (entry) =>
  entry?.strategy_id ? encodeURIComponent(String(entry.strategy_id)) : null;

/**
 * What the page can do with each action name the server may return.
 *
 * `to(entry)` returns the in-app destination for the action, or `null` when this entry carries
 * no identifier the destination needs — in which case the button is not rendered at all, since
 * a button leading nowhere is worse than an absent one.
 *
 * `call` names one of the two real API calls this page performs for a subscription
 * (`api.library.renewSubscription`, `api.library.cancelSubscription`); `disclose` names a panel
 * rendered from fields already in this response.
 *
 * Actions outside this catalogue — `edit`, `open_in_builder`, `view_graph`, `edit_blocks`, the
 * indicator- and risk-parameter actions, the export/download actions, `view_model_params`,
 * `re_version`, `delete` — have no entry here. The owner's card grid below is unchanged and
 * remains where an owner edits, clones, renames, backtests, deploys and archives their own
 * strategy; nothing in this section constructs one of those controls, so a `SUBSCRIBED` entry
 * cannot acquire one however `allowed_actions` is spelled.
 */
const ACTION_CATALOG = {
  view_listing: {
    label: "View listing",
    icon: Globe,
    to: (entry) => {
      const id = listingParam(entry);
      return id ? `/app/marketplace?listing_id=${id}` : null;
    },
  },
  run_backtest: {
    label: "Run backtest",
    icon: BarChart2,
    to: (entry) => {
      const listing = listingParam(entry);
      if (listing) return `/app/backtest?listing_id=${listing}`;
      const strategy = strategyParam(entry);
      return strategy ? `/app/backtest?strategy_id=${strategy}` : null;
    },
  },
  deploy_live: {
    label: "Deploy live",
    icon: Play,
    // A subscribed Listing deploys through `POST /api/library/{id}/deploy`, which takes the
    // subscriber's own symbol, timeframe and capital. `api.library` exposes no method for it,
    // and this page constructs no HTTP call of its own, so the affordance leads to the Listing
    // surface where that form lives rather than inventing a request here.
    to: (entry) => {
      const id = listingParam(entry);
      return id ? `/app/marketplace?listing_id=${id}&intent=deploy_live` : null;
    },
  },
  start_paper: {
    label: "Start paper trading",
    icon: Zap,
    to: (entry) => {
      const listing = listingParam(entry);
      if (listing) return `/app/paper-trading?listing_id=${listing}`;
      const strategy = strategyParam(entry);
      return strategy ? `/app/paper-trading?strategy_id=${strategy}` : null;
    },
  },
  view_performance: {
    label: "View performance",
    icon: TrendingUp,
    disclose: "performance",
    // Only offered when this response actually carries figures; an empty panel would be a
    // fabricated "no results" where the truth is "this read did not fetch them".
    available: (entry) => Boolean(entry?.listing),
  },
  view_subscription: {
    label: "View subscription",
    icon: Shield,
    disclose: "subscription",
    available: (entry) => Boolean(entry?.subscription),
  },
  renew: {
    label: "Renew",
    icon: RefreshCw,
    call: "renew",
    available: (entry) => Boolean(entry?.subscription_id),
  },
  cancel_renewal: {
    label: "Cancel renewal",
    icon: Pause,
    call: "cancel_renewal",
    available: (entry) => Boolean(entry?.subscription_id),
  },
};

/** A timestamp as local text, or the raw value when it is not a parseable instant. Nothing is
 *  substituted for an absent expiry: the caller renders "not reported" instead. */
const formatInstant = (value) => {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
};

/** The entry's display name — the Listing's for a subscribed entry, the strategy's own for an
 *  owned one. Rendered as a React text child, never as HTML. */
const entryName = (entry) => entry?.listing?.name ?? entry?.name ?? null;

// ─────────────────────────────────────────────────────────────────────────────
// Rename validation (task 10.3) — Requirements 15.1, 15.2, 18.3
//
// `window.prompt("Enter new strategy name:", currentName)` returned a bare string with
// nowhere to put a label and nowhere to put a verdict, so the only thing the page could do
// with a bad name was drop it silently (`if (!newName || newName.trim() === "" …) return`)
// or let it round-trip to a 422. Requirements 15.1 and 15.2 are exactly that gap: a labelled
// control, and a message naming the field and the reason it is invalid.
//
// WHERE THESE BOUNDS COME FROM — they are the server's, read off the endpoint, not invented:
//
//   `backend_app/routers/strategies.py` :: `_validated_strategy_name`
//     STRATEGY_NAME_MIN_LENGTH = 1, STRATEGY_NAME_MAX_LENGTH = 100, both measured on the
//     name AFTER `raw.strip()`. Its four refusal reasons are `name_missing`,
//     `name_not_a_string`, `empty_after_trim` and `longer_than_max_length`, all under one
//     422 `STRATEGY_NAME_INVALID`. The first two are unreachable from a text input, which
//     always submits a string under the key the client module sends; the other two are the
//     two rules below.
//
// WHAT IS DELIBERATELY *NOT* VALIDATED HERE
//   **Uniqueness.** `rename_strategy` writes `strategies.name` with no uniqueness check and
//   there is no unique constraint behind it, so two strategies may legitimately share a
//   name. Rejecting a duplicate here would be this page inventing a rule the platform does
//   not have, and would block a rename the server would have accepted.
//   **Character sets.** The server accepts any string within the length bounds.
//
// The submitted string is sent EXACTLY as typed (`api/modules/strategies.js`: "Sent as
// submitted; the server trims it and stores the trimmed form"). Nothing here trims on the
// way out — trimming client-side would change the payload — so the bounds are measured on
// the trimmed form and the untrimmed form is what travels.
// ─────────────────────────────────────────────────────────────────────────────

/** `strategies.py::STRATEGY_NAME_MIN_LENGTH`, measured after trimming. */
export const STRATEGY_NAME_MIN_LENGTH = 1;

/** `strategies.py::STRATEGY_NAME_MAX_LENGTH`, measured after trimming. */
export const STRATEGY_NAME_MAX_LENGTH = 100;

/**
 * Why this submitted name cannot be sent, or `null` when it can.
 *
 * Pure, and exported so the inline message, the gate on the confirm handler and the tests
 * all read one implementation rather than three restatements of the same rule.
 *
 * The third rule is not the server's: an unchanged name is a request with no effect, and
 * `handleRenameStrategy` has always refused it (`newName === currentName`). It is kept
 * exactly as it was — an identical comparison on the raw strings — so that this change
 * alters nothing about which renames reach the backend. It is now *stated* instead of
 * silently dropped, which is the whole point of Requirement 15.2.
 *
 * @param {string} submitted The string in the field, untrimmed.
 * @param {string} [currentName] The strategy's present name.
 * @returns {string|null} A message naming the field and the reason, or `null`.
 */
export function strategyNameRefusal(submitted, currentName) {
  const raw = typeof submitted === "string" ? submitted : "";
  const trimmed = raw.trim();

  if (trimmed.length < STRATEGY_NAME_MIN_LENGTH) {
    return (
      "A strategy name needs at least one visible character. Leading and trailing " +
      "whitespace is removed before the name is measured and stored, so spaces alone " +
      "are an empty name. The current name is unchanged."
    );
  }

  if (trimmed.length > STRATEGY_NAME_MAX_LENGTH) {
    return (
      `A strategy name may be at most ${STRATEGY_NAME_MAX_LENGTH} characters once leading ` +
      `and trailing whitespace is removed; this one is ${trimmed.length}. The current name ` +
      "is unchanged."
    );
  }

  if (typeof currentName === "string" && raw === currentName) {
    return "This is already this strategy's name, so there is nothing to rename.";
  }

  return null;
}

export default function Strategies() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [view, setView] = useState("library");
  const [strategies, setStrategies] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  // Task 17.1 / Requirement 14.5: a failed owner read is an ERROR STATE, never an empty
  // table. The rejection itself is kept — `ds/Panel` hands it to `ds/ErrorState`, which
  // renders authored copy through `translateError` and never `err.message`.
  const [listError, setListError] = useState(null);
  const [listReloadKey, setListReloadKey] = useState(0);
  const [isProcessing, setIsProcessing] = useState({});
  // Task 16.3: the archive endpoint's refusal, kept so a 409 can be rendered as the
  // specific error it is — each blocking deployment by identifier and state (Requirement
  // 3.1) — instead of being logged to the console and lost (Requirement 2.10).
  const [archiveError, setArchiveError] = useState(null);
  // ── Task 10.3: the two confirmation surfaces that replaced `window.confirm` and
  // `window.prompt` (Requirements 15.1, 15.2, 18.3, design.md §1.8, §8.4).
  //
  // Each one is `null` until the trader asks for the action, and *holds the whole request*
  // until the dialog's own confirm action fires. That is the structural half of Property 13:
  // neither `endpoints.strategies.delete` nor `endpoints.strategies.rename` is reachable from
  // the card's button — the button only ever sets one of these two objects — so there is no
  // code path on which the mutation precedes the confirmation.
  const [archiveDialog, setArchiveDialog] = useState(null);
  const [renameDialog, setRenameDialog] = useState(null);
  const [editingStrategy, setEditingStrategy] = useState(null);
  const [filterStatus, setFilterStatus] = useState(STATUS_FILTER_ALL);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState(DEFAULT_SORT);
  const [page, setPage] = useState(1);
  const [focusedStrategyId, setFocusedStrategyId] = useState(() => searchParams.get("strategy_id") || null);
  const [deployModalStrategy, setDeployModalStrategy] = useState(null);
  const [deployConfig, setDeployConfig] = useState({
    exchange: "binance",
    environment: "live",
    capital: "10000",
    tradeSizePct: "10",
    maxDrawdown: "15",
    stopLoss: "2",
  });
  const [deployError, setDeployError] = useState(null);
  const [connectedExchanges, setConnectedExchanges] = useState([]);
  const [exchangesLoading, setExchangesLoading] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState(null);
  
  // ── Task 32.7: the server-derived ownership list (GET /api/library/my-strategies) ──
  // `null` means "not read yet or the read failed" and is never rendered as a list: an error
  // clears it, so the section shows the error state rather than the previous entries
  // (Requirement 12.8 — an error is an error state, never a stale list or a zero).
  const [ownershipEntries, setOwnershipEntries] = useState(null);
  const [ownershipMeta, setOwnershipMeta] = useState(null);
  const [ownershipLoading, setOwnershipLoading] = useState(true);
  const [ownershipError, setOwnershipError] = useState(null);
  const [ownershipReloadKey, setOwnershipReloadKey] = useState(0);
  // Per-entry disclosure of the two read-only panels and the outcome of the two subscription
  // calls. Keyed by the server's own `entry_id`, so nothing is keyed on an index.
  const [disclosed, setDisclosed] = useState({});
  const [subscriptionAction, setSubscriptionAction] = useState({});

  const resumeBuilderStrategy = location.state?.resumeBuilderStrategy || null;

  /** Re-read the owner list. The retry action on the error state, and nothing else. */
  const reloadStrategies = useCallback(() => setListReloadKey((k) => k + 1), []);

  useEffect(() => {
    let cancelled = false;

    const loadStrategies = async () => {
      try {
        setIsLoading(true);
        console.log("📊 API CALL: GET /api/strategies");
        const payload = await endpoints.strategies.list();
        console.log("📊 API RESPONSE:", payload);
        if (cancelled) return;
        const rows = Array.isArray(payload) ? payload : payload?.data || payload?.strategies || [];
        setStrategies(Array.isArray(rows) ? normalizeStrategies(rows) : []);
        setListError(null);
      } catch (err) {
        console.error("📊 API ERROR: Failed to load strategies:", err?.message);
        if (cancelled) return;
        // Requirement 14.5: the rows are dropped with the failure, because nobody knows
        // they are still current — and an empty table over a failed read is the one
        // rendering that reads as "you have no strategies" when the truth is "we could not
        // ask". `listError` puts the page in the error state instead.
        setStrategies([]);
        setListError(err);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    loadStrategies();
    return () => {
      cancelled = true;
    };
  }, [listReloadKey]);

  // ── Task 32.7: the combined owned-and-subscribed read, beside the one above ───────────
  // It is a second, independent read: `endpoints.strategies.list()` above still owns the
  // owner's card grid, and neither read's failure blanks or falsifies the other's section.
  useEffect(() => {
    let cancelled = false;

    const loadOwnership = async () => {
      setOwnershipLoading(true);
      setOwnershipError(null);
      try {
        const payload = await api.library.myStrategies();
        if (cancelled) return;
        const items = Array.isArray(payload?.items) ? payload.items : null;
        if (items === null) {
          // The read completed but carried no `items` array. That is not an empty list — it is
          // a response this page cannot interpret, so it is reported as an error rather than
          // rendered as "you own nothing" (Requirement 12.8).
          setOwnershipEntries(null);
          setOwnershipMeta(null);
          setOwnershipError({
            kind: "error",
            message:
              "The ownership list came back in a shape this page cannot read, so nothing is shown for it.",
          });
          return;
        }
        setOwnershipEntries(items);
        setOwnershipMeta({
          total: payload?.total,
          owned_total: payload?.owned_total,
          subscribed_total: payload?.subscribed_total,
          running_paper_sessions_available: payload?.running_paper_sessions_available,
          as_of: payload?.as_of,
        });
      } catch (err) {
        if (cancelled) return;
        // Requirement 12.8: the previous entries are dropped, so what renders is the error
        // state and not a list that is no longer known to be current.
        setOwnershipEntries(null);
        setOwnershipMeta(null);
        setOwnershipError({
          kind: err?.status === 401 || err?.status === 403 ? "unauthorised" : "error",
          status: err?.status ?? null,
          message:
            typeof err?.getUserMessage === "function"
              ? err.getUserMessage()
              : err?.message || "The ownership list could not be read.",
        });
      } finally {
        if (!cancelled) setOwnershipLoading(false);
      }
    };

    loadOwnership();
    return () => {
      cancelled = true;
    };
  }, [ownershipReloadKey]);

  const reloadOwnership = useCallback(() => {
    setOwnershipReloadKey((k) => k + 1);
  }, []);

  const toggleDisclosure = useCallback((entryId, panel) => {
    setDisclosed((prev) => {
      const current = prev[entryId] || {};
      return { ...prev, [entryId]: { ...current, [panel]: !current[panel] } };
    });
  }, []);

  /**
   * Renew one Subscription — `POST /api/library/subscriptions/{id}/renew` through `api.library`.
   *
   * The endpoint changes no state: it returns a provider session for the renewal amount, and the
   * transition into ACTIVE happens only when that payment is confirmed. So this handler reports
   * what came back and, when the provider supplied a `checkout_url`, hands the user to it. It
   * invents no amount: `amount_minor` is displayed as the integer number of minor units the
   * server returned, in the currency it named.
   */
  const handleRenewSubscription = useCallback(async (entry) => {
    const key = entry.entry_id;
    if (!entry.subscription_id) return;
    setSubscriptionAction((prev) => ({ ...prev, [key]: { busy: true } }));
    try {
      const res = await api.library.renewSubscription(entry.subscription_id);
      if (res?.checkout_url) {
        setSubscriptionAction((prev) => ({
          ...prev,
          [key]: { busy: false, message: "Opening the payment page for the next period." },
        }));
        window.location.assign(res.checkout_url);
        return;
      }
      const amount =
        res?.amount_minor !== undefined && res?.currency
          ? ` Amount: ${res.amount_minor} ${res.currency} minor units.`
          : "";
      setSubscriptionAction((prev) => ({
        ...prev,
        [key]: {
          busy: false,
          message: `Renewal status: ${res?.status ?? "reported without a status"}.${amount} The subscription becomes active only once the payment is confirmed.`,
        },
      }));
    } catch (err) {
      setSubscriptionAction((prev) => ({
        ...prev,
        [key]: {
          busy: false,
          error:
            typeof err?.getUserMessage === "function"
              ? err.getUserMessage()
              : err?.message || "The renewal could not be started.",
        },
      }));
    }
  }, []);

  /**
   * Cancel renewal — `POST /api/library/subscriptions/{id}/cancel` through `api.library`.
   *
   * This stops the next charge; entitlement runs to the unchanged current expiry. The list is
   * re-read afterwards so the rendered renewal state is the server's, not an optimistic guess.
   */
  const handleCancelRenewal = useCallback(async (entry) => {
    const key = entry.entry_id;
    if (!entry.subscription_id) return;
    setSubscriptionAction((prev) => ({ ...prev, [key]: { busy: true } }));
    try {
      const res = await api.library.cancelSubscription(entry.subscription_id);
      setSubscriptionAction((prev) => ({
        ...prev,
        [key]: {
          busy: false,
          message: `${res?.status ?? "cancelled"}${res?.message ? ` — ${res.message}` : ""}. Access runs to the unchanged period expiry.`,
        },
      }));
      reloadOwnership();
    } catch (err) {
      setSubscriptionAction((prev) => ({
        ...prev,
        [key]: {
          busy: false,
          error:
            typeof err?.getUserMessage === "function"
              ? err.getUserMessage()
              : err?.message || "The renewal could not be cancelled.",
        },
      }));
    }
  }, [reloadOwnership]);

  /** The click behaviour for one catalogued action on one entry. */
  const runOwnershipAction = useCallback(
    (entry, descriptor) => {
      if (descriptor.disclose) {
        toggleDisclosure(entry.entry_id, descriptor.disclose);
        return;
      }
      if (descriptor.call === "renew") {
        handleRenewSubscription(entry);
        return;
      }
      if (descriptor.call === "cancel_renewal") {
        handleCancelRenewal(entry);
        return;
      }
      const to = descriptor.to ? descriptor.to(entry) : null;
      if (to) navigate(to);
    },
    [toggleDisclosure, handleRenewSubscription, handleCancelRenewal, navigate],
  );

  // Sync URL search params on change. `?environment=` is deliberately NOT read: the list
  // projection carries no environment, so honouring it would filter on the
  // `row.environment ?? "paper"` default this page no longer writes. See WITHHELD_FILTERS.
  useEffect(() => {
    const stratId = searchParams.get("strategy_id");
    if (stratId) setFocusedStrategyId(stratId);
  }, [searchParams]);

  // Handle resume from StrategyBuilder
  useEffect(() => {
    if (!resumeBuilderStrategy) return;
    setEditingStrategy(resumeBuilderStrategy);
    setView("builder");
  }, [resumeBuilderStrategy]);

  /*
   * ── The filter row's answer (Requirements 11.2, 11.5) ─────────────────────
   *
   * The four fabricated aggregates that stood here — "Total P&L" as a sum over
   * `_toNumber(row.pnl ?? …, 0)` and "Avg Win Rate" as a mean over `row.wr` — are gone with
   * the card grid. Neither key is on the list projection, so both were sums over zeros
   * presented as account figures (Requirement 14.5). What replaces them is the filter row's
   * `n of m` count, which counts rows that were actually read.
   */
  const query = search.trim().toLowerCase();

  const visibleStrategies = useMemo(() => {
    if (!Array.isArray(strategies)) return [];
    return strategies.filter((s) => {
      if (filterStatus !== STATUS_FILTER_ALL && s.status !== filterStatus) return false;
      if (query === "") return true;
      // Name and market: the two columns this read really carries text for.
      return [s.name, s.market].some(
        (text) => typeof text === "string" && text.toLowerCase().includes(query),
      );
    });
  }, [strategies, filterStatus, query]);

  /** Requirement 4.1's failure reporting, hoisted out of the row (see the Alert below). */
  const failedStrategies = useMemo(
    () => visibleStrategies.filter((s) => s.status === "failed" || s.health === HEALTH_ERROR),
    [visibleStrategies],
  );

  const totalCount = Array.isArray(strategies) ? strategies.length : 0;
  const resultCount = visibleStrategies.length;
  const hasActiveFilters = filterStatus !== STATUS_FILTER_ALL || query !== "";

  /**
   * Requirement 11.5's two empty states, told apart by the same comparison
   * `ds/FilterBar.emptyVariantFor` makes: `no-data` when there is nothing to find, and
   * `no-match` — the only case that offers clear-filters — when rows exist and a filter is
   * hiding them.
   */
  const emptyVariant = resultCount > 0 ? null : (totalCount > 0 ? "no-match" : "no-data");

  /** §11.1's states for the table region. An error is never an empty table. */
  const listState = listError !== null
    ? PANEL_STATES.ERROR
    : isLoading
      ? PANEL_STATES.LOADING
      : totalCount === 0
        ? PANEL_STATES.EMPTY
        : PANEL_STATES.READY;

  const API_BASE = CONFIG.apiBaseUrl;

  const setProcessingFor = (id, value) =>
    setIsProcessing((prev) => ({ ...prev, [id]: value }));

  // ── Deploy Live: the versioned endpoint and its gate (task 16.1) ──────────────────────
  // Requirements 2.2, 11.5, 13.4, 13.5, 13.6.
  //
  // A Deployment binds one *immutable version* (Requirement 11.1), so the version label is
  // part of the address, not of the body: `POST /api/strategy-operations/strategies/{id}/
  // versions/{version}/deploy`. `current_version` is the field the list endpoint publishes
  // and the field `normalizeStrategies` above reads it into.
  const deployVersionLabel = deployModalStrategy?.current_version ?? null;

  // One description of the deployment, used for the POST body and for the preflight query
  // alike, so the gate cannot be asked about a different binding than the one submitted.
  // `deploymentRequest` owns the `DeploymentBindingRequest` shape (`extra="forbid"`
  // server-side) and the `execution_config` allow-list.
  const deployRequest = useMemo(
    () =>
      deploymentRequest({
        environment: deployConfig.environment,
        account: selectedAccount,
        capital: deployConfig.capital,
        tradeSizePct: deployConfig.tradeSizePct,
      }),
    [deployConfig.environment, deployConfig.capital, deployConfig.tradeSizePct, selectedAccount],
  );

  // Requirements 13.4/13.5/13.6: the Deploy button's enabled state is this poll's answer.
  // It runs only while the modal is open, and the endpoint is read-only and
  // side-effect-free, so an open modal reserves nothing. `DeployPreflightPanel` below
  // renders the same answer per condition (task 18.1), so the panel and the button cannot
  // disagree: both read this one summary.
  const preflight = useDeployPreflight({
    strategyId: deployModalStrategy?.id ? String(deployModalStrategy.id) : null,
    version: deployVersionLabel,
    query: deployRequest.query,
    enabled: Boolean(deployModalStrategy),
  });

  const handleOpenDeployModal = async (s) => {
    setDeployModalStrategy(s);
    setDeployError(null);
    setSelectedAccount(null);
    setConnectedExchanges([]);
    setExchangesLoading(true);
    try {
      const accounts = await endpoints.exchange.list();
      const list = Array.isArray(accounts) ? accounts : accounts?.data || accounts?.exchanges || [];
      setConnectedExchanges(list);
      // Auto-select first connected account
      const firstConnected = list.find(a => a.status === 'connected' || a.status === 'active');
      if (firstConnected) {
        setSelectedAccount(firstConnected);
        setDeployConfig(prev => ({ ...prev, exchange: firstConnected.exchange_id }));
      }
    } catch (err) {
      console.error('Failed to load exchange accounts:', err);
      setDeployError('Failed to load connected exchange accounts. Please add an exchange in Exchange Manager.');
    } finally {
      setExchangesLoading(false);
    }
  };

  /**
   * Deploy one immutable version through the Deployment_Gate (task 16.1).
   *
   * Re-pointed from `endpoints.strategies.deploy(id, {...})` — the legacy
   * `POST /api/strategies/{id}/deploy`, which binds no account, no risk configuration and
   * no mode, and travels no gate — to
   * `POST /api/strategy-operations/strategies/{id}/versions/{version}/deploy`, which is the
   * endpoint Requirement 11.5 names: it runs `evaluate_binding` and records the whole
   * Deployment_Binding as one row (Requirement 13.1).
   *
   * The body is the server's `DeploymentBindingRequest` and nothing else. The legacy body's
   * `exchange_id`, `account_id`, `capital_allocated`, `trade_size_pct`, `max_drawdown` and
   * `stop_loss` are not fields of it — that model declares `extra="forbid"`, so sending
   * them would be a 422 rather than a silently ignored setting. What survives the change is
   * carried where the platform actually reads it: the account by id, the mode as `mode`,
   * and the per-order notional the sizing inputs express as `execution_config.
   * max_order_notional`. The venue is resolved from the account row server-side and is
   * never sent (Requirement 12.5); `maxDrawdown`/`stopLoss` belong to a risk configuration
   * the binding references by id, so they are not smuggled into `execution_config`.
   *
   * Requirement 13.4 is enforced twice, deliberately: the button is disabled while the
   * preflight says the deployment is not deployable, and this handler refuses as well — a
   * click that raced a condition turning red must not reach the write path.
   */
  const handleConfirmDeploy = async () => {
    if (!deployModalStrategy) return;
    const id = deployModalStrategy.id;
    if (isProcessing[id]) return;
    if (!deployVersionLabel) {
      setDeployError(
        'This strategy has no current version to deploy. Save a version first — a ' +
          'deployment always binds one immutable version.',
      );
      return;
    }
    if (!preflight.deployable) {
      // Requirement 13.4: not every mandatory validation has passed.
      setDeployError(
        preflight.error?.message ??
          'Not every mandatory deployment check has passed yet, so nothing was deployed.',
      );
      return;
    }
    const prevStrategies = strategies;
    setProcessingFor(id, true);
    try {
      console.log(
        `📊 DEPLOY STRATEGY: POST /api/strategy-operations/strategies/${id}/versions/${deployVersionLabel}/deploy`,
        deployRequest.body,
      );
      const res = await endpoints.strategies.deployVersion(
        id,
        deployVersionLabel,
        deployRequest.body,
        { environment: deployRequest.environment },
      );
      console.log("📊 DEPLOY RESPONSE:", res);
      // Optimistic update
      setStrategies(prev => prev.map(s => s.id === id ? { ...s, status: "running", environment: deployConfig.environment } : s));
      setDeployModalStrategy(null);
    } catch (err) {
      console.error("📊 DEPLOY ERROR:", err);
      setDeployError(err?.data?.message || err?.response?.data?.message || err?.message || 'Deployment failed');
      setStrategies(prevStrategies);
    } finally {
      setProcessingFor(id, false);
    }
  };

  const handlePauseStrategy = async (id) => {
    if (isProcessing[id]) return;
    const prevStrategies = strategies;
    setProcessingFor(id, true);
    setStrategies(prev => prev.map(s => s.id === id ? { ...s, status: "paused" } : s));
    try {
      console.log(`📊 PAUSE STRATEGY: POST /api/strategies/${id}/pause`);
      const res = await endpoints.strategies.pause(id);
      console.log("📊 PAUSE RESPONSE:", res);
    } catch (err) {
      console.error("📊 PAUSE ERROR:", err);
      setStrategies(prevStrategies);
    } finally {
      setProcessingFor(id, false);
    }
  };

  /**
   * Archive one strategy (task 16.3, Requirements 2.9, 2.10, 3.1).
   *
   * The call site is unchanged on purpose: `DELETE /api/strategies/{id}` is the same path
   * and method as before, because task 5.1 rewired that route server-side from a hard row
   * delete into a soft archive (`strategies.archived_at`). What changed on this side is
   * everything the user sees:
   *
   * * The confirmation describes archival, not deletion — the row is no longer destroyed,
   *   and its versions, backtests, deployments and signals are all preserved
   *   (Requirements 3.2, 3.5), so a dialog promising a delete would be describing an
   *   operation the backend stopped performing.
   * * A 409 is rendered. `archive_strategy` refuses while any deployment is DEPLOYING,
   *   RUNNING or PAUSED and names each blocker; that used to reach a `console.error` and
   *   go no further, which left the row silently restored with no stated reason.
   *
   * The optimistic removal is retained — an accepted archive does take the strategy out of
   * the default list (Requirement 3.3) — and so is the restore on failure, which is what
   * makes a refusal leave the list exactly as it was (Requirement 3.1).
   *
   * ── Task 10.3: the confirmation moved, the request did not ────────────────────────────
   * The handler is split in two. `requestArchiveStrategy` is the *whole* of what the card's
   * archive button does — it opens the dialog and issues nothing. `handleConfirmArchive`
   * below is the dialog's confirm action and is the only place `endpoints.strategies.delete`
   * is called from. Under `window.confirm` the button and the request were one statement, so
   * "no request before the confirmation" was a property of `confirm`'s return value; it is
   * now a property of this page's structure, which is what Property 13 can check.
   *
   * The endpoint, the payload, the optimistic removal, the restore-on-failure and
   * `describeArchiveFailure`'s reading of the refusal are all untouched.
   */
  const requestArchiveStrategy = (strategy) => {
    const id = strategy?.id;
    if (id === undefined || id === null) return;
    if (isProcessing[id]) return;
    // A previous refusal is about a previous attempt; it is cleared as the next one opens,
    // which is the behaviour the old handler had at exactly this point.
    setArchiveError(null);
    // The review grid is read off the row here rather than looked up at render time, so the
    // dialog keeps describing the strategy the trader clicked even if the list re-reads
    // underneath it. Every field is the row's own; nothing is defaulted into existence — an
    // absent one renders `ConfirmDialog`'s not-available marker.
    setArchiveDialog({
      id,
      name: strategy.name ?? null,
      status: strategy.status ?? null,
      version: strategy.current_version ?? null,
      environment: strategy.environment ?? null,
    });
  };

  const handleConfirmArchive = async () => {
    if (!archiveDialog) return;
    const { id, name } = archiveDialog;
    if (isProcessing[id]) return;
    // Closed before the request: the refusal renders in the page's own `archive-error`
    // alert (below), which is where it has always rendered, and a dialog left open over it
    // would hide the list the alert is talking about.
    setArchiveDialog(null);
    const prevStrategies = strategies;
    setProcessingFor(id, true);
    setArchiveError(null);
    setStrategies(prev => prev.filter(s => s.id !== id));
    try {
      console.log(`📊 ARCHIVE STRATEGY: DELETE /api/strategies/${id}`);
      const res = await endpoints.strategies.delete(id);
      console.log("📊 ARCHIVE RESPONSE:", res);
    } catch (err) {
      console.error("📊 ARCHIVE ERROR:", err);
      setStrategies(prevStrategies);
      setArchiveError({
        strategyId: id,
        strategyName: name || null,
        ...describeArchiveFailure(err),
      });
    } finally {
      setProcessingFor(id, false);
    }
  };

  const handleCloneStrategy = async (id) => {
    if (isProcessing[id]) return;
    setProcessingFor(id, true);
    try {
      console.log(`📊 CLONE STRATEGY: POST /api/strategies/${id}/clone`);
      const token = sessionStorage.getItem("token");
      const res = await fetch(`${API_BASE}/api/strategies/${id}/clone`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        }
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.message || data.detail || `Clone failed (HTTP ${res.status})`);
      }
      console.log("📊 CLONE RESPONSE:", data);
      // Reload strategies
      const payload = await endpoints.strategies.list();
      const rows = Array.isArray(payload) ? payload : payload?.data || payload?.strategies || [];
      if (Array.isArray(rows)) setStrategies(normalizeStrategies(rows));
    } catch (err) {
      console.error("📊 CLONE ERROR:", err.message);
    } finally {
      setProcessingFor(id, false);
    }
  };

  /**
   * Ask to rename. Task 10.3, Requirements 15.1, 15.2, 18.3.
   *
   * `window.prompt("Enter new strategy name:", currentName)` used to be the whole interaction.
   * It cannot carry a label, cannot show a validation message, cannot be focus-trapped and
   * hands back a bare string with no way to refuse it in place — so the page's only options
   * were to drop a bad name silently or to let it round-trip to a 422. This opens the dialog
   * and issues nothing; {@link handleConfirmRename} is the only caller of
   * `endpoints.strategies.rename`.
   *
   * The field opens pre-filled with the current name, as `window.prompt`'s second argument
   * did, so the trader edits rather than retypes.
   */
  const requestRenameStrategy = (id, currentName) => {
    if (isProcessing[id]) return;
    const present = typeof currentName === "string" ? currentName : "";
    setRenameDialog({
      id,
      currentName: present,
      // The exact string in the field. Never trimmed here — see `strategyNameRefusal`.
      value: present,
      // Requirement 15.2's second trigger: the message shows on blur, and unconditionally
      // once a submit has been attempted.
      submitted: false,
      error: null,
    });
  };

  /**
   * Rename, from the dialog's confirm action and from nowhere else.
   *
   * The endpoint and the payload are byte-for-byte what they were: `endpoints.strategies
   * .rename(id, <the string as typed>)` → `PUT /api/strategies/{id}/rename` with
   * `{ name }`. The reload afterwards, the `console.error` on failure and the per-strategy
   * processing flag are all unchanged too. What is new is that an invalid name is refused
   * *here*, with the reason on screen, instead of being dropped without a word.
   */
  const handleConfirmRename = async () => {
    if (!renameDialog) return;
    const { id, currentName, value } = renameDialog;
    if (isProcessing[id]) return;

    /*
     * ═══ THE GATE — Requirement 15.2, Property 13 ═══
     * The refusal is checked here, in the handler, not only where the message is rendered.
     * A stray `.click()` on the confirm control, or a future refactor of the dialog, must
     * not be able to put an empty or over-long name on the wire. `submitted` is raised so
     * the reason is on screen for a trader who reached this by pressing Enter without ever
     * blurring the field.
     */
    if (strategyNameRefusal(value, currentName) !== null) {
      setRenameDialog((prev) => (prev ? { ...prev, submitted: true } : prev));
      return;
    }

    setRenameDialog((prev) => (prev ? { ...prev, submitted: true, error: null } : prev));
    setProcessingFor(id, true);
    try {
      console.log(`📊 RENAME STRATEGY: PUT /api/strategies/${id}/rename`);
      // Task 16.2: the raw `fetch` this replaced aimed at the same path, which did not
      // exist anywhere in the backend until task 5.3 added it — so every rename failed.
      // The route is real now, and the call travels the shared client like every other
      // strategy action, which is what carries the auth token, the retry/circuit policy,
      // and the server's own refusal message (`STRATEGY_NAME_INVALID` with the reason,
      // `STRATEGY_ARCHIVED`, or a 404 for a strategy that is not the caller's) onto
      // `err.message` below (Requirement 2.6).
      const data = await endpoints.strategies.rename(id, value);
      console.log("📊 RENAME RESPONSE:", data);
      // Reload strategies
      const payload = await endpoints.strategies.list();
      const rows = Array.isArray(payload) ? payload : payload?.data || payload?.strategies || [];
      if (Array.isArray(rows)) setStrategies(normalizeStrategies(rows));
      setRenameDialog(null);
    } catch (err) {
      console.error("📊 RENAME ERROR:", err.message);
      // The dialog stays open carrying the failure. `ConfirmDialog` renders it through
      // `translateError`, so what reaches the screen is authored copy — including
      // `STRATEGY_NAME_INVALID`'s, for a name this page's bounds let through and the
      // server's did not — and never `err.message`, a status code or a traceback
      // (Requirements 14.3, 14.4). The `console.error` above is unchanged.
      setRenameDialog((prev) => (prev ? { ...prev, error: err } : prev));
    } finally {
      setProcessingFor(id, false);
    }
  };

  /**
   * Why the open rename cannot be submitted, or `null`. Computed once per render so the
   * `Field`'s `invalid`, its `error`, the standing note beside it and
   * `handleConfirmRename`'s own guard are four readings of one value rather than four
   * chances to disagree.
   */
  const renameRefusal = renameDialog === null
    ? null
    : strategyNameRefusal(renameDialog.value, renameDialog.currentName);

  /* ══════════════════════════════════════════════════════════════════════════
   * THE ROW'S ACTIONS (Requirement 4.2)
   * ══════════════════════════════════════════════════════════════════════════
   *
   * The same seven controls the card carried, the same handlers, the same endpoints — moved
   * into the row and nothing else. Task 17.2 repartitions them into the inline set plus
   * `Deploy live` and `Delete` below a divider in the row's overflow menu, each behind a
   * `ConfirmDialog`; this task deliberately does not pre-empt that, because a page left with
   * no actions between the two is a page a trader cannot use.
   */
  const BUSY_TITLE = "An action on this strategy is still in progress.";

  /*
   * Declared per render, not memoised, and neither is the column list below it.
   * `DataTable` memoises a row on its projected cell values PLUS the `columns` identity, and
   * everything this cell reads that is not a projected value — `isProcessing`, and five
   * handlers that close over `strategies` — changes without any cell value changing. A
   * memo keyed on a subset of that is a row showing a stale disabled state; a memo keyed on
   * all of it recomputes every render anyway. So the identity moves every render, which is
   * what the card grid did, and the page has no WebSocket ticks for it to cost anything on.
   */
  const ActionsCell = function StrategyActionsCell({ row }) {
    const busy = Boolean(isProcessing[row.id]);
    const title = busy ? BUSY_TITLE : undefined;
    return (
      <span className="inline-flex flex-wrap items-center gap-1">
        <Button
          variant="ghost"
          size="xs"
          icon={Edit2}
          title={title}
          disabled={busy}
          onClick={() => { setEditingStrategy(row); setView("builder"); }}
        >
          Edit
        </Button>
        <Button
          variant="ghost"
          size="xs"
          icon={Copy}
          title={title}
          disabled={busy}
          onClick={() => handleCloneStrategy(row.id)}
        >
          Clone
        </Button>
        <Button
          variant="ghost"
          size="xs"
          icon={Settings}
          title={title}
          disabled={busy}
          onClick={() => requestRenameStrategy(row.id, row.name)}
        >
          Rename
        </Button>
        <Button
          variant="ghost"
          size="xs"
          icon={BarChart2}
          title={title}
          disabled={busy}
          onClick={() => navigate(`/app/backtest?strategy_id=${row.id}`, { state: { strategy: row } })}
        >
          Backtest
        </Button>
        <Button
          variant="ghost"
          size="xs"
          icon={Activity}
          title={title}
          disabled={busy}
          onClick={() => navigate(`/app/signal-trace?strategy_id=${row.id}`)}
        >
          Trace
        </Button>
        {row.status === "running" ? (
          <Button
            variant="ghost"
            size="xs"
            icon={Pause}
            title={title}
            disabled={busy}
            onClick={() => handlePauseStrategy(row.id)}
          >
            Pause
          </Button>
        ) : (
          <Button
            variant="success"
            size="xs"
            icon={Play}
            title={title}
            disabled={busy}
            onClick={() => handleOpenDeployModal(row)}
          >
            Deploy
          </Button>
        )}
        <Button
          variant="danger"
          size="xs"
          icon={Trash2}
          title={busy ? BUSY_TITLE : "Archive strategy"}
          aria-label={row.name ? `Archive ${row.name}` : `Archive strategy ${row.id}`}
          disabled={busy}
          onClick={() => requestArchiveStrategy(row)}
        />
      </span>
    );
  };

  /* ── §7.2's ten columns, in §7.2's order, plus the row's actions ────────── */
  const columns = [
    {
      key: "name",
      header: labelFor("name"),
      sortable: true,
      priority: 1,
      render: nameCell(focusedStrategyId),
    },
    { key: "status", header: labelFor("status"), priority: 1, render: StatusCell },
    {
      key: "current_version",
      header: labelFor("version"),
      priority: 2,
      render: textCell("version"),
    },
    {
      key: "market",
      header: labelFor("market"),
      format: "symbol",
      sortable: true,
      priority: 1,
      render: textCell("market"),
    },
    {
      key: "deploymentState",
      header: labelFor("deploymentState"),
      priority: 2,
      render: DeploymentCell,
    },
    {
      key: "performance",
      header: labelFor("performance"),
      align: "numeric",
      // A dead sort header over a column of markers is a control that does nothing.
      sortable: false,
      priority: 3,
      render: unreportedCell("performance"),
    },
    { key: "health", header: labelFor("riskState"), priority: 2, render: RiskCell },
    {
      key: "last_signal_at",
      header: labelFor("lastSignalAt"),
      format: "timestamp",
      sortable: isReported("lastSignalAt"),
      priority: 3,
      render: instantCell("lastSignalAt"),
    },
    {
      key: "last_execution_at",
      header: labelFor("lastExecutionAt"),
      format: "timestamp",
      sortable: isReported("lastExecutionAt"),
      priority: 3,
      render: instantCell("lastExecutionAt"),
    },
    {
      key: "updated_at",
      header: labelFor("updatedAt"),
      format: "timestamp",
      sortable: true,
      priority: 2,
      render: instantCell("updatedAt"),
    },
    // The eleventh column is the row's action set, not one of Requirement 4.1's ten fields
    // — §7.2's sketch draws it as the trailing `⋯`.
    { key: "actions", header: "Actions", priority: 1, render: ActionsCell },
  ];

  /* ── Handlers. Every one that changes the row set returns to page 1, or a
   *    stale page number renders an empty table over rows that exist. ────── */

  const handleFilterChange = useCallback((id, value) => {
    if (id === "status") setFilterStatus(value);
    setPage(1);
  }, []);

  const handleSearchChange = useCallback((text) => {
    setSearch(text);
    setPage(1);
  }, []);

  const clearFilters = useCallback(() => {
    setFilterStatus(STATUS_FILTER_ALL);
    setSearch("");
    setPage(1);
  }, []);

  if (view === "builder") return <StrategyBuilder onBack={() => setView("library")} strategy={editingStrategy} onBacktest={(payload) => navigate("/app/backtest", { state: { strategy: payload } })} />;

  return (
    <div className="flex min-w-0 flex-col gap-4 overflow-y-auto bg-surface-canvas p-5 text-content-primary">
      <PageHeader
        title="Strategies"
        subtitle="Manage, backtest and deploy your algorithmic strategies"
        actions={(
          <>
            <CommandButton
              intent="secondary"
              icon={RefreshCw}
              loading={isLoading}
              loadingLabel="Reading strategies"
              onClick={reloadStrategies}
            >
              Refresh
            </CommandButton>
            <CommandButton intent="primary" icon={Plus} onClick={() => setView("builder")}>
              New strategy
            </CommandButton>
          </>
        )}
      />

      {/* Archive refusal (task 16.3). Requirement 3.1 makes the server identify every
          blocking deployment by identifier and state; Requirement 2.10 makes the page say
          those deployments must be stopped first. Both are rendered from the response
          itself — the message is the server's own wording, and the list below it is the
          `blocking_deployments` it named. Task 17.1 moved the hand-rolled red box this was
          onto `ds/Alert`, which takes the hue, the icon, the border style and the
          live-region role from `severity` (Requirement 1.4); the copy, the testids and the
          dismiss control are unchanged. */}
      {archiveError && (
        <Alert
          severity="error"
          variant="strip"
          data-testid="archive-error"
          title={`${archiveError.blocked ? "ARCHIVE BLOCKED" : "ARCHIVE FAILED"}`
            + `${archiveError.strategyName ? ` — ${archiveError.strategyName}` : ""}`}
          onDismiss={() => setArchiveError(null)}
          dismissLabel="Dismiss archive error"
        >
          <p>{archiveError.message}</p>
          {archiveError.blockingDeployments.length > 0 && (
            <ul data-testid="archive-blocking-deployments" className="mt-2 flex flex-col gap-0.5 font-mono">
              {archiveError.blockingDeployments.map((d, i) => (
                <li key={d.deploymentId ?? `${d.source || "own"}-${i}`}>
                  {describeBlockingDeployment(d)}
                </li>
              ))}
            </ul>
          )}
        </Alert>
      )}

      {/* Requirement 4.1's failure reporting, hoisted out of the row. The card grid put this
          banner inside each failed card; a table row is not the place for a sentence, and
          `DataTable`'s memo does not observe a field no column projects — so the condition
          is summarised once, above the table, from the rows in view. The reason is the row's
          own `error_message` when it carries one. */}
      {failedStrategies.length > 0 && (
        <Alert
          severity="error"
          variant="strip"
          data-testid="strategy-failures"
          title={failedStrategies.length === 1
            ? "1 strategy reported a failure"
            : `${failedStrategies.length} strategies reported a failure`}
          action={{
            label: "Open Signal Trace",
            to: `/app/signal-trace?strategy_id=${failedStrategies[0].id}`,
          }}
        >
          <ul data-testid="strategy-failure-reasons" className="flex flex-col gap-0.5">
            {failedStrategies.map((s) => (
              <li key={s.id}>
                <span className="font-medium">{s.name ?? `Strategy ${s.id}`}</span>
                {": "}
                <span>{s.errorMessage || "Strategy execution halted due to error."}</span>
              </li>
            ))}
          </ul>
        </Alert>
      )}

      {/* ── Owned and subscribed, as the server labels them (task 32.7) ────────────
              Requirements 12.2, 12.3, 12.4, 12.5, 12.6, 12.8. Every label, every
              subscription field and every button below comes from
              `api.library.myStrategies()`; the section infers no ownership, no entitlement
              and no affordance of its own. */}
          <section
            aria-label="Owned and subscribed strategies"
            data-testid="ownership-section"
            style={{ background: "#0c1017", border: "1px solid #1e293b", borderRadius: 12, padding: 14, marginBottom: 16 }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
              <div>
                <div style={{ color: "#f8fafc", fontSize: 12, fontWeight: 900, letterSpacing: 1, textTransform: "uppercase" }}>
                  Owned &amp; subscribed
                </div>
                <div style={{ color: "#64748b", fontSize: 9, fontFamily: "monospace", marginTop: 3 }}>
                  Ownership, subscription state and available actions as returned by the server
                </div>
              </div>
              {ownershipMeta && !ownershipError && (
                <div data-testid="ownership-counts" style={{ color: "#64748b", fontSize: 9, fontFamily: "monospace", textAlign: "right" }}>
                  {ownershipMeta.owned_total !== undefined && <div>Owned: {ownershipMeta.owned_total}</div>}
                  {ownershipMeta.subscribed_total !== undefined && <div>Subscribed: {ownershipMeta.subscribed_total}</div>}
                  {ownershipMeta.as_of && <div>As of {formatInstant(ownershipMeta.as_of)}</div>}
                </div>
              )}
            </div>

            {/* Loading (Requirement 12.8) */}
            {ownershipLoading && (
              <div role="status" data-testid="ownership-loading" style={{ color: "#64748b", fontSize: 11, fontFamily: "monospace", padding: "8px 2px" }}>
                Loading your owned and subscribed strategies...
              </div>
            )}

            {/* Unauthorised, and error-with-retry (Requirement 12.8). Nothing of the previous
                list survives an error: `ownershipEntries` was cleared, so there is no stale
                list and no zero standing in for a figure that was not read. */}
            {!ownershipLoading && ownershipError && (
              <div
                role="alert"
                data-testid={ownershipError.kind === "unauthorised" ? "ownership-unauthorised" : "ownership-error"}
                style={{ background: "rgba(239,68,68,0.12)", border: "1px solid #ef4444", borderRadius: 8, padding: "10px 12px", display: "flex", gap: 8, alignItems: "flex-start" }}
              >
                <AlertTriangle size={14} style={{ color: "#ef4444", flexShrink: 0, marginTop: 2 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ color: "#ef4444", fontSize: 10, fontFamily: "monospace", letterSpacing: 1.5, fontWeight: 900, textTransform: "uppercase", marginBottom: 4 }}>
                    {ownershipError.kind === "unauthorised" ? "Not authorised" : "Ownership list unavailable"}
                  </div>
                  <div style={{ color: "#fca5a5", fontSize: 11, lineHeight: 1.5 }}>
                    {ownershipError.kind === "unauthorised"
                      ? "This list is only readable while you are signed in. Sign in again to see your owned and subscribed strategies."
                      : ownershipError.message}
                  </div>
                </div>
                <Button variant="outline" size="xs" icon={RefreshCw} onClick={reloadOwnership} data-testid="ownership-retry">
                  Retry
                </Button>
              </div>
            )}

            {/* Empty (Requirement 12.8) — reached only when the read completed */}
            {!ownershipLoading && !ownershipError && ownershipEntries && ownershipEntries.length === 0 && (
              <div data-testid="ownership-empty" style={{ color: "#64748b", fontSize: 11, fontFamily: "monospace", padding: "8px 2px" }}>
                You own no active strategies and hold no marketplace subscriptions.
              </div>
            )}

            {!ownershipLoading && !ownershipError && ownershipEntries && ownershipEntries.length > 0 && (
              <>
                {ownershipMeta?.running_paper_sessions_available === false && (
                  <div data-testid="ownership-sessions-unavailable" style={{ color: "#fbbf24", fontSize: 10, fontFamily: "monospace", marginBottom: 10 }}>
                    The running paper-session count could not be read, so it is not shown. It is
                    unavailable, not zero.
                  </div>
                )}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 10 }}>
                  {ownershipEntries.map((entry) => {
                    // Every one of these is read, not derived.
                    const actions = Array.isArray(entry.allowed_actions) ? entry.allowed_actions : [];
                    const nonEntitling = entry.entitling === false;
                    const reason = reasonText(entry.unavailable_reason);
                    const reasonId = `ownership-reason-${entry.entry_id}`;
                    const panels = disclosed[entry.entry_id] || {};
                    const actionState = subscriptionAction[entry.entry_id] || {};
                    const listing = entry.listing || null;
                    const performance = listing?.performance_summary || {};
                    const risk = listing?.risk_metrics || {};
                    const figures = [
                      ["Total return %", performance.total_return_pct],
                      ["Win rate %", performance.win_rate_pct],
                      ["Profit factor", performance.profit_factor],
                      ["Trades", performance.total_trades],
                      ["Sharpe", risk.sharpe_ratio],
                      ["Max drawdown %", risk.max_drawdown_pct],
                    ].filter(([, value]) => value !== null && value !== undefined);
                    // The three execution actions the server did NOT return for this entry.
                    // They are rendered as programmatically disabled controls with a text
                    // reason — never as clickable buttons — which is how Requirement 12.5's
                    // "every execution action disabled" is visible without the page offering
                    // anything `allowed_actions` withheld: these carry no click handler.
                    const disabledExecution = nonEntitling
                      ? EXECUTION_ACTIONS.filter((action) => !actions.includes(action))
                      : [];

                    return (
                      <Card
                        key={entry.entry_id}
                        className="p-4 bg-[#080a0e] border-[#1e293b]"
                        data-testid={`ownership-entry-${entry.entry_id}`}
                      >
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 8 }}>
                          <span style={{ color: "#f8fafc", fontWeight: 900, fontSize: 12, minWidth: 0, overflowWrap: "anywhere" }}>
                            {entryName(entry) ?? "Name not reported"}
                          </span>
                          {/* The label itself, exactly as the server returned it. */}
                          <Tag2 c={entry.ownership === "SUBSCRIBED" ? "purple" : "cyan"}>
                            <span data-testid={`ownership-label-${entry.entry_id}`}>{entry.ownership}</span>
                          </Tag2>
                        </div>

                        {/* Descriptive metadata, rendered as text children only. */}
                        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                          {(listing?.symbol ?? entry.symbol) && <Tag2 c="cyan">{listing?.symbol ?? entry.symbol}</Tag2>}
                          {entry.timeframe && <Tag2 c="gold">{entry.timeframe}</Tag2>}
                          {Array.isArray(listing?.supported_timeframes) &&
                            listing.supported_timeframes.map((tf) => (
                              <Tag2 key={`tf-${entry.entry_id}-${tf}`} c="gold">{tf}</Tag2>
                            ))}
                          {entry.status && <Tag2 c="gray">{entry.status}</Tag2>}
                        </div>

                        {/* The Subscription_State, the period expiry and the renewal state
                            (Requirement 12.6). A value the server did not carry reads
                            "not reported" rather than being defaulted. */}
                        {entry.subscription && (
                          <dl
                            data-testid={`ownership-subscription-${entry.entry_id}`}
                            style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 8px", margin: "0 0 8px", fontSize: 10, fontFamily: "monospace" }}
                          >
                            <dt style={{ color: "#64748b" }}>Subscription</dt>
                            <dd style={{ color: "#e2e8f0", margin: 0 }}>{entry.subscription.state ?? "not reported"}</dd>
                            <dt style={{ color: "#64748b" }}>Expires</dt>
                            <dd style={{ color: "#e2e8f0", margin: 0 }}>
                              {formatInstant(entry.subscription.period_expiry) ?? "not reported"}
                            </dd>
                            <dt style={{ color: "#64748b" }}>Renewal</dt>
                            <dd style={{ color: "#e2e8f0", margin: 0 }}>{entry.subscription.renewal_state ?? "not reported"}</dd>
                          </dl>
                        )}

                        {Object.prototype.hasOwnProperty.call(entry, "running_paper_sessions") && (
                          <div style={{ color: "#64748b", fontSize: 10, fontFamily: "monospace", marginBottom: 8 }}>
                            Running paper sessions: {entry.running_paper_sessions}
                          </div>
                        )}

                        {/* The explicit expired / unavailable-strategy state (Requirements
                            12.5, 12.8). Which of the two it is comes from the server's own
                            `unavailable_reason` code. */}
                        {nonEntitling && (
                          <div
                            role="status"
                            data-testid={
                              entry.unavailable_reason === "MARKETPLACE_STRATEGY_UNAVAILABLE"
                                ? `ownership-unavailable-strategy-${entry.entry_id}`
                                : `ownership-expired-${entry.entry_id}`
                            }
                            style={{ background: "rgba(251,191,36,0.12)", border: "1px solid #fbbf24", borderRadius: 6, padding: "6px 8px", marginBottom: 8 }}
                          >
                            <div style={{ color: "#fbbf24", fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 1.2, textTransform: "uppercase", marginBottom: 3 }}>
                              {entry.unavailable_reason === "MARKETPLACE_STRATEGY_UNAVAILABLE"
                                ? "Strategy unavailable"
                                : "Subscription does not entitle"}
                            </div>
                            <div id={reasonId} style={{ color: "#fde68a", fontSize: 10, lineHeight: 1.5 }}>
                              {reason ?? "The server reported this entry as non-entitling."}{" "}
                              Every execution action is disabled until it entitles again; renewal
                              is offered below.
                            </div>
                          </div>
                        )}

                        {/* The affordances. One button per member of `allowed_actions` the page
                            can act on — nothing is added to this list and no condition widens
                            it, so an action the server withheld is never constructed. */}
                        <div
                          data-testid={`ownership-actions-${entry.entry_id}`}
                          style={{ display: "flex", gap: 4, flexWrap: "wrap" }}
                        >
                          {actions.map((action) => {
                            const descriptor = ACTION_CATALOG[action];
                            if (!descriptor) return null;
                            if (descriptor.available && !descriptor.available(entry)) return null;
                            if (descriptor.to && !descriptor.to(entry)) return null;
                            const busy = Boolean(descriptor.call && actionState.busy);
                            return (
                              <Button
                                key={`${entry.entry_id}-${action}`}
                                variant={descriptor.call === "renew" ? "success" : "ghost"}
                                size="xs"
                                icon={descriptor.icon}
                                disabled={busy}
                                data-action={action}
                                data-testid={`ownership-action-${entry.entry_id}-${action}`}
                                onClick={() => runOwnershipAction(entry, descriptor)}
                              >
                                {busy ? "Working..." : descriptor.label}
                              </Button>
                            );
                          })}

                          {disabledExecution.map((action) => (
                            <Button
                              key={`${entry.entry_id}-disabled-${action}`}
                              variant="ghost"
                              size="xs"
                              icon={ACTION_CATALOG[action].icon}
                              disabled
                              aria-disabled="true"
                              aria-describedby={reasonId}
                              title={reason ?? undefined}
                              data-action={action}
                              data-testid={`ownership-disabled-${entry.entry_id}-${action}`}
                            >
                              {ACTION_CATALOG[action].label}
                            </Button>
                          ))}
                        </div>

                        {/* `view_performance` — figures already in this response, never
                            recomputed and never zero-filled. */}
                        {panels.performance && (
                          <div data-testid={`ownership-performance-${entry.entry_id}`} style={{ marginTop: 8, borderTop: "1px solid #1e293b", paddingTop: 8 }}>
                            {figures.length === 0 ? (
                              <div style={{ color: "#64748b", fontSize: 10, fontFamily: "monospace" }}>
                                This response carries no performance figures for this listing.
                              </div>
                            ) : (
                              <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 8px", margin: 0, fontSize: 10, fontFamily: "monospace" }}>
                                {figures.map(([label, value]) => (
                                  <React.Fragment key={`${entry.entry_id}-fig-${label}`}>
                                    <dt style={{ color: "#64748b" }}>{label}</dt>
                                    <dd style={{ color: "#e2e8f0", margin: 0 }}>{String(value)}</dd>
                                  </React.Fragment>
                                ))}
                              </dl>
                            )}
                          </div>
                        )}

                        {/* `view_subscription` — the same server triple, plus the Listing price
                            exactly as returned (`price_display` when the server rendered one,
                            otherwise the integer minor units and their currency). */}
                        {panels.subscription && entry.subscription && (
                          <div data-testid={`ownership-subscription-panel-${entry.entry_id}`} style={{ marginTop: 8, borderTop: "1px solid #1e293b", paddingTop: 8 }}>
                            <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 8px", margin: 0, fontSize: 10, fontFamily: "monospace" }}>
                              <dt style={{ color: "#64748b" }}>State</dt>
                              <dd style={{ color: "#e2e8f0", margin: 0 }}>{entry.subscription.state ?? "not reported"}</dd>
                              <dt style={{ color: "#64748b" }}>Period expiry</dt>
                              <dd style={{ color: "#e2e8f0", margin: 0 }}>{formatInstant(entry.subscription.period_expiry) ?? "not reported"}</dd>
                              <dt style={{ color: "#64748b" }}>Renewal</dt>
                              <dd style={{ color: "#e2e8f0", margin: 0 }}>{entry.subscription.renewal_state ?? "not reported"}</dd>
                              {listing?.price_display !== undefined && (
                                <>
                                  <dt style={{ color: "#64748b" }}>Price</dt>
                                  <dd style={{ color: "#e2e8f0", margin: 0 }}>
                                    {listing.price_display} {listing.currency ?? ""}
                                  </dd>
                                </>
                              )}
                              {listing?.price_display === undefined && listing?.price_minor !== undefined && listing?.price_minor !== null && (
                                <>
                                  <dt style={{ color: "#64748b" }}>Price</dt>
                                  <dd style={{ color: "#e2e8f0", margin: 0 }}>
                                    {listing.price_minor} {listing.currency ?? ""} minor units
                                  </dd>
                                </>
                              )}
                            </dl>
                          </div>
                        )}

                        {(actionState.message || actionState.error) && (
                          <div
                            role={actionState.error ? "alert" : "status"}
                            data-testid={`ownership-action-result-${entry.entry_id}`}
                            style={{ marginTop: 8, fontSize: 10, fontFamily: "monospace", color: actionState.error ? "#fca5a5" : "#94a3b8", lineHeight: 1.5 }}
                          >
                            {actionState.error || actionState.message}
                          </div>
                        )}
                      </Card>
                    );
                  })}
                </div>
              </>
            )}
          </section>

      {/* ══════════════════════════════════════════════════════════════════════════════
          §7.2's table (task 17.1). Requirements 4.1, 4.4, 4.5, 11.2, 11.4, 11.5, 14.5.
          ══════════════════════════════════════════════════════════════════════════════ */}
      <Panel
        title="Your strategies"
        state={listState}
        loading={{ kind: "skeleton-table", rows: 6, columns: columns.length }}
        empty={NO_STRATEGIES_STATE}
        error={{ error: listError, context: "strategies", onRetry: reloadStrategies }}
      >
        <div className="flex min-w-0 flex-col gap-3">
          <FilterBar
            filters={[{
              id: "status",
              label: labelFor("status"),
              kind: "segmented",
              options: STATUS_FILTER_OPTIONS,
            }]}
            values={{ status: filterStatus }}
            onChange={handleFilterChange}
            search={search}
            onSearchChange={handleSearchChange}
            searchLabel="Search name or market"
            searchPlaceholder={SEARCH_PLACEHOLDER}
            resultCount={resultCount}
            totalCount={totalCount}
            countNoun={COUNT_NOUN}
            actions={hasActiveFilters ? (
              <CommandButton intent="ghost" onClick={clearFilters}>
                Clear filters
              </CommandButton>
            ) : null}
          />

          {/* A control that is not offered is explained rather than simply absent —
              Requirement 19.3's rule, applied to a filter instead of a figure. */}
          {WITHHELD_FILTERS.length > 0 ? (
            <ul data-testid="withheld-filters" className="flex flex-col gap-1 text-micro text-content-secondary">
              {WITHHELD_FILTERS.map((withheld) => (
                <li key={withheld.id}>
                  <span className="font-medium text-content-primary">{`${withheld.label}: `}</span>
                  {withheld.reason}
                </li>
              ))}
            </ul>
          ) : null}

          {emptyVariant === "no-match" ? (
            <EmptyState
              {...NO_STRATEGIES_STATE}
              variant="no-match"
              headline="No strategies match these filters"
              body={`You have ${totalCount} ${COUNT_NOUN}, and none of them matches the `
                + "current status filter and search. Widen them to see the rows again."}
              clearFiltersAction={{ label: "Clear filters", onClick: clearFilters }}
            />
          ) : (
            <DataTable
              columns={columns}
              rows={visibleStrategies}
              getRowId={(row) => row.id}
              rowHref={(row) => `/app/strategies/${row.id}`}
              totalCount={resultCount}
              page={page}
              pageSize={PAGE_SIZE}
              onPageChange={setPage}
              sort={sort}
              onSortChange={setSort}
              stickyHeader
              caption="Your strategies, with status, deployment and activity per row"
            />
          )}
        </div>
      </Panel>

      {/* Deployment Modal */}
      {deployModalStrategy && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(1,6,8,0.85)", backdropFilter: "blur(6px)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, padding: 16 }}>
          <div style={{ background: "#0c1017", border: `1px solid #1e293b`, borderRadius: 14, width: "100%", maxWidth: 520, padding: 24, boxShadow: "0 20px 50px rgba(0,0,0,0.6)" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16, borderBottom: `1px solid #1e293b`, paddingBottom: 12 }}>
              <div>
                <div style={{ color: "#00d4ff", fontSize: 10, fontFamily: "monospace", letterSpacing: 2, fontWeight: 900, textTransform: "uppercase" }}>DEPLOYMENT ORCHESTRATION</div>
                <div style={{ color: "#f8fafc", fontSize: 16, fontWeight: 900 }}>{deployModalStrategy.name}</div>
              </div>
              {/* The version this modal would deploy, not a hardcoded label: the deploy is
                  addressed to `/versions/{version}/deploy`, so naming a different one here
                  would describe a deployment that is not the one about to be bound. It was
                  `text={`v${strategy.version || "2.0"}`}`, which rendered nothing at all —
                  `Tag2` takes its label as children, not as a `text` prop — and would have
                  read "v2.0" for every strategy if it had. */}
              <Tag2 c="cyan">v{deployVersionLabel || "unknown"}</Tag2>
            </div>

            {(deployError || preflight.error) && (
              <div data-testid="deploy-error" style={{ background: "rgba(255,46,84,0.1)", border: `1px solid #ef4444`, borderRadius: 8, padding: "8px 12px", color: "#ef4444", fontSize: 11, fontFamily: "monospace", marginBottom: 16 }}>
                Deployment Error: {deployError || preflight.error?.message}
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
              <div>
                <label style={{ color: "#94a3b8", fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 1, textTransform: "uppercase", display: "block", marginBottom: 6 }}>
                  {deployConfig.environment === "paper" ? "Paper Trading Account" : "Connected Exchange Account"}
                </label>
                {deployConfig.environment === "paper" ? (
                  <div style={{ background: "#080a0e", border: `1px solid rgba(0,212,255,0.4)`, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", color: "#00d4ff" }}>
                    ★ VyomQuant Virtual Paper Account [$100,000]
                  </div>
                ) : exchangesLoading ? (
                  <div style={{ color: "#64748b", fontSize: 10, fontFamily: "monospace", padding: "8px 10px" }}>Loading accounts...</div>
                ) : connectedExchanges.length === 0 ? (
                  <div style={{ color: "#ef4444", fontSize: 10, fontFamily: "monospace", padding: "8px 10px", background: `rgba(239,68,68,0.12)`, borderRadius: 8, border: `1px solid rgba(239,68,68,0.3)` }}>
                    No exchange accounts connected.{" "}
                    <button onClick={() => { setDeployModalStrategy(null); navigate('/app/exchange'); }} style={{ color: "#00d4ff", background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'monospace', fontSize: 10 }}>Add one →</button>
                  </div>
                ) : (
                  <select
                    value={selectedAccount?.id || ""}
                    onChange={(e) => {
                      const acct = connectedExchanges.find(a => a.id === e.target.value);
                      setSelectedAccount(acct || null);
                      if (acct) setDeployConfig(prev => ({ ...prev, exchange: acct.exchange_id }));
                    }}
                    style={{ width: "100%", background: "#080a0e", border: `1px solid ${selectedAccount ? "#10b981" : "#1e293b"}`, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", color: "#f8fafc", outline: "none" }}
                  >
                    <option value="">-- Select Account --</option>
                    {connectedExchanges.map(acct => (
                      <option key={acct.id} value={acct.id}>
                        {acct.exchange_id?.toUpperCase()} — {acct.name || acct.masked_key || acct.id.slice(0, 8)} [{acct.status}]
                      </option>
                    ))}
                  </select>
                )}
                {deployConfig.environment !== "paper" && selectedAccount && (
                  <div style={{ marginTop: 4, fontSize: 9, fontFamily: "monospace", color: selectedAccount.status === 'connected' || selectedAccount.status === 'active' ? "#10b981" : "#ef4444" }}>
                    Status: {selectedAccount.status} · Health: {selectedAccount.health || 'unknown'}
                  </div>
                )}
              </div>

              <div>
                <label style={{ color: "#94a3b8", fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 1, textTransform: "uppercase", display: "block", marginBottom: 6 }}>Execution Mode</label>
                <select
                  value={deployConfig.environment}
                  onChange={(e) => setDeployConfig(prev => ({ ...prev, environment: e.target.value }))}
                  style={{ width: "100%", background: "#080a0e", border: `1px solid #1e293b`, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", color: "#f8fafc", outline: "none" }}
                >
                  <option value="paper">Paper Simulation (Virtual Execution)</option>
                  <option value="live">Live Execution (Master Executor)</option>
                </select>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
              <div>
                <label style={{ color: "#94a3b8", fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 1, textTransform: "uppercase", display: "block", marginBottom: 6 }}>Capital ($)</label>
                <input
                  type="number"
                  value={deployConfig.capital}
                  onChange={(e) => setDeployConfig(prev => ({ ...prev, capital: e.target.value }))}
                  style={{ width: "100%", background: "#080a0e", border: `1px solid #1e293b`, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", color: "#f8fafc", outline: "none" }}
                />
              </div>

              <div>
                <label style={{ color: "#94a3b8", fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 1, textTransform: "uppercase", display: "block", marginBottom: 6 }}>Trade Size %</label>
                <input
                  type="number"
                  value={deployConfig.tradeSizePct}
                  onChange={(e) => setDeployConfig(prev => ({ ...prev, tradeSizePct: e.target.value }))}
                  style={{ width: "100%", background: "#080a0e", border: `1px solid #1e293b`, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", color: "#f8fafc", outline: "none" }}
                />
              </div>
            </div>

            {/* Task 18.1 — the pre-deployment summary (Requirements 13.3, 13.4, 13.5, 13.6).
                Every verdict below is the preflight response's own, one row per condition
                the Deployment_Gate reported, refreshed by the two-second poll while this
                modal stays open. What stood here before was four hardcoded rows drawn from
                this modal's local form state: a strategy-status bullet, an
                account-selected bullet, an account-health bullet reading a status string
                the accounts endpoint spells differently, and a capital bullet. None of them
                had seen the gate, none could report a balance, a permission, a market or a
                risk limit, and all four went green while the deployment was in fact
                refusable — the "pending" case (a condition that could not be evaluated at
                all) had no representation whatsoever. The `summary` prop keeps the
                contextual half of Requirement 13.3 — which deployment these verdicts are
                about — as stated facts, with no verdict colouring of its own. */}
            <DeployPreflightPanel
              {...preflight}
              summary={[
                ["Strategy", deployModalStrategy.name],
                ["Version", `v${deployVersionLabel || "unknown"}`],
                ["Mode", deployRequest.mode || "not selected"],
                [
                  "Account",
                  deployConfig.environment === "paper"
                    ? "VyomQuant virtual paper account"
                    : selectedAccount
                      ? `${selectedAccount.exchange_id?.toUpperCase() || "exchange"} — ${selectedAccount.name || selectedAccount.masked_key || selectedAccount.id}`
                      : "not selected",
                ],
                ["Asset", deployModalStrategy.pair || "unknown"],
                ["Timeframe", deployModalStrategy.tf || "unknown"],
                ["Capital", `$${Number(deployConfig.capital || 0).toLocaleString()}`],
                [
                  "Max order notional",
                  deployRequest.body.execution_config?.max_order_notional !== undefined
                    ? `$${Number(deployRequest.body.execution_config.max_order_notional).toLocaleString()}`
                    : "not set",
                ],
              ]}
            />

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <Button variant="ghost" size="sm" onClick={() => setDeployModalStrategy(null)}>Cancel</Button>
              {/* Requirements 13.4/13.5: disabled while any mandatory validation has not
                  passed, enabled automatically as soon as they all have — both decided by
                  the preflight poll, which is the same gate the deploy itself runs. The
                  local account-status and capital checks this replaced were a second,
                  divergent gate: they read a status string the accounts endpoint spells
                  differently, and they could not see a balance, a permission or a market
                  the server refuses on. `Number(capital) > 0` is retained because it is the
                  form's own validity, not a duplicate of a server condition. */}
              <Button
                variant="success"
                size="sm"
                icon={Play}
                onClick={handleConfirmDeploy}
                disabled={
                  !!isProcessing[deployModalStrategy.id] ||
                  Number(deployConfig.capital) <= 0 ||
                  !preflight.deployable
                }
              >
                {isProcessing[deployModalStrategy.id] ? "Deploying..." : "Confirm & Deploy"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════════════════
          Task 10.3 — the two confirmations that replaced `window.confirm` and
          `window.prompt` (design.md §1.8, §8.4; Requirements 15.1, 15.2, 18.3, 19.4).

          Both render through `ds/ConfirmDialog`, so both get the focus trap, `Escape`,
          initial focus on **cancel**, `role="dialog"` / `aria-modal` / `aria-labelledby`
          and the single-overlay claim (Requirement 18.3, 17.3) that neither native dialog
          could be given. Only one of the two can be open: each button clears nothing and
          sets its own state, and the two states are only ever set from separate handlers.
          ══════════════════════════════════════════════════════════════════════════════════ */}

      {/* ── Archive. `intent="destructive"`, and NO acknowledgement checkbox ──────────────
          §8.4's inventory: "Delete / archive strategy … Acknowledgement: No — reversible
          via archive". Task 5.1 turned this route into a soft archive, so the row and every
          version, backtest, deployment and signal behind it survive. The checkbox is
          reserved for the irreversible and the live-funds cases in the same table (deploy
          to live, cancel all orders, kill switch); spending it on a reversible action is
          how a trader learns to tick one without reading it, which is precisely what makes
          it worthless on the deploy that matters. */}
      <ConfirmDialog
        open={archiveDialog !== null}
        onCancel={() => setArchiveDialog(null)}
        onConfirm={handleConfirmArchive}
        title="Archive strategy"
        intent="destructive"
        description={
          "Archiving removes this strategy from your library list. Nothing is deleted: its "
          + "versions, backtests, deployments and signals are all kept and stay inspectable "
          + "for history and audit. Archiving is refused while any of its deployments is "
          + "still deploying, running or paused."
        }
        review={[
          { label: "Strategy", value: archiveDialog?.name ?? null },
          { label: "Identifier", value: archiveDialog?.id ?? null },
          { label: "Current status", value: archiveDialog?.status ?? null },
          { label: "Version", value: archiveDialog?.version ?? null },
          { label: "Environment", value: archiveDialog?.environment ?? null },
          { label: "Removed from", value: "Your strategy library list" },
          { label: "Kept", value: "Versions, backtests, deployments, signals" },
        ]}
        confirmLabel="Archive strategy"
        cancelLabel="Keep it in the list"
      />

      {/* ── Rename. A labelled `ds/Field` with inline validation (Requirements 15.1, 15.2)
          `window.prompt` could carry neither. The field's label is visible and associated
          by `htmlFor`; the message names the field and the reason; and the reason is
          computed by `strategyNameRefusal`, which is the same function
          `handleConfirmRename` refuses on — so what the trader is told and what the page
          enforces cannot drift apart. */}
      <ConfirmDialog
        open={renameDialog !== null}
        onCancel={() => setRenameDialog(null)}
        onConfirm={handleConfirmRename}
        title="Rename strategy"
        intent="neutral"
        description={
          "Renaming changes the strategy's display name and nothing else. Its versions, "
          + "backtests, deployments and signals are all left as they are."
        }
        review={[
          { label: "Strategy", value: renameDialog?.currentName ?? null },
          { label: "Identifier", value: renameDialog?.id ?? null },
          // The name that will actually be stored: the server trims before it writes, so
          // this is the trimmed form even though the untrimmed string is what is sent. Blank
          // renders the not-available marker rather than an empty row.
          { label: "New name", value: (renameDialog?.value ?? "").trim() || null },
        ]}
        confirmLabel="Rename strategy"
        cancelLabel="Cancel"
        busy={renameDialog !== null && !!isProcessing[renameDialog.id]}
        busyLabel="Renaming…"
        error={renameDialog?.error ?? null}
        errorContext="strategies"
      >
        {renameDialog === null ? null : (
          <>
            <Field
              id="strategy-rename-name"
              label="New strategy name"
              required
              value={renameDialog.value}
              placeholder="e.g. RSI reversion — BTC 15m"
              hint={
                `Between ${STRATEGY_NAME_MIN_LENGTH} and ${STRATEGY_NAME_MAX_LENGTH} `
                + "characters once leading and trailing whitespace is removed. Names do not "
                + "have to be unique."
              }
              // Both halves of Requirement 15.2 come from one value, so an error treatment
              // with no message is not a state this page can reach.
              invalid={renameRefusal !== null}
              error={renameRefusal ?? undefined}
              submitted={renameDialog.submitted}
              onChange={(event) => {
                const next = event.target.value;
                setRenameDialog((prev) => (prev ? { ...prev, value: next } : prev));
              }}
            />
            {/* Why the rename cannot proceed, stated outside the field's blur/submit gate as
                well as inside it. `role="status"` rather than `alert`: it is the standing
                condition of the form, not an event, and it sits beside the control it is
                about. */}
            {renameRefusal === null ? null : (
              <p
                role="status"
                data-testid="rename-blocked-reason"
                className="text-micro text-content-secondary"
                style={{ margin: 0 }}
              >
                {`Renaming is not possible yet: ${renameRefusal}`}
              </p>
            )}
          </>
        )}
      </ConfirmDialog>
    </div>
  );
}
