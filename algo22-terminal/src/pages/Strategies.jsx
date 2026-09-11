import React, { useState, useEffect, useMemo, useCallback } from "react";
import { useNavigate, useLocation, useSearchParams } from "react-router-dom";
import {
  Filter, Plus, Layers, Radio, TrendingUp, Target, Edit2,
  BarChart2, Pause, Play, Trash2, PlusCircle, Copy, Settings,
  Activity, Zap, Globe, Server, Clock, Shield, RefreshCw, AlertTriangle
} from "lucide-react";
import { endpoints, api } from "../api";
import { CONFIG } from "../config";
import {
  C, Tag2, StatusDot, ProgressBar
} from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import StrategyBuilder from "./StrategyBuilder";
import { computeStrategyHealth } from "../lib/strategyHealth";
import {
  archiveConfirmMessage,
  describeArchiveFailure,
  describeBlockingDeployment,
} from "../lib/strategyArchive";
import { deploymentRequest } from "../lib/deployPreflight";
import { useDeployPreflight } from "../hooks/useDeployPreflight";
import DeployPreflightPanel from "../components/DeployPreflightPanel";

// ── Normaliser helpers — module scope so action handlers can reference them ──
const _toNumber = (v, fallback = 0) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
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

const normalizeStrategies = (rows = []) =>
  (Array.isArray(rows) ? rows : []).map((row, i) => ({
    id: row.id ?? row.strategy_id ?? i + 1,
    name: row.name ?? row.strategy_name ?? `Strategy #${i + 1}`,
    pair: row.pair ?? row.symbol ?? "N/A",
    status: _normalizeStatus(row.status),
    errorMessage: row.error_message || row.error || row.reason || null,
    pnl: _toNumber(row.pnl ?? row.pnl_percent ?? row.return_pct, 0),
    wr: _toNumber(row.wr ?? row.win_rate ?? row.winRate, 0),
    dd: _toNumber(row.dd ?? row.max_dd ?? row.maxDrawdown, 0),
    tf: row.tf ?? row.timeframe ?? "N/A",
    type: row.type ?? row.strategy_type ?? "Custom",
    current_version: row.current_version ?? row.version ?? "1.0",
    environment: row.environment ?? "paper",
    created_at: row.created_at,
    updated_at: row.updated_at,
    is_running: row.is_running ?? false,
    worker_region: row.worker_region,
    exchange_status: row.exchange_status,
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

export default function Strategies() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [view, setView] = useState("library");
  const [strategies, setStrategies] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState({});
  // Task 16.3: the archive endpoint's refusal, kept so a 409 can be rendered as the
  // specific error it is — each blocking deployment by identifier and state (Requirement
  // 3.1) — instead of being logged to the console and lost (Requirement 2.10).
  const [archiveError, setArchiveError] = useState(null);
  const [editingStrategy, setEditingStrategy] = useState(null);
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterStatus, setFilterStatus] = useState("all");
  const [filterEnvironment, setFilterEnvironment] = useState(() => searchParams.get("environment") || "all");
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

  useEffect(() => {
    const controller = new AbortController();

    const loadStrategies = async () => {
      try {
        setIsLoading(true);
        console.log("📊 API CALL: GET /api/strategies");
        const payload = await endpoints.strategies.list();
        console.log("📊 API RESPONSE:", payload);
        const rows = Array.isArray(payload) ? payload : payload?.data || payload?.strategies || [];
        if (Array.isArray(rows)) setStrategies(normalizeStrategies(rows));
      } catch (err) {
        console.error("📊 API ERROR: Failed to load strategies:", err.message);
      } finally {
        setIsLoading(false);
      }
    };

    loadStrategies();
    return () => controller.abort();
  }, []);

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

  // Sync URL search params on change
  useEffect(() => {
    const stratId = searchParams.get("strategy_id");
    const env = searchParams.get("environment");
    if (stratId) setFocusedStrategyId(stratId);
    if (env) setFilterEnvironment(env);
  }, [searchParams]);

  // Handle resume from StrategyBuilder
  useEffect(() => {
    if (!resumeBuilderStrategy) return;
    setEditingStrategy(resumeBuilderStrategy);
    setView("builder");
  }, [resumeBuilderStrategy]);

  // Memoised aggregates – only recompute when strategies or filter change
  const totalStrategies = useMemo(() => Array.isArray(strategies) ? strategies.length : 0, [strategies]);
  const runningStrategies = useMemo(() => Array.isArray(strategies) ? strategies.filter((s) => s.status === "running").length : 0, [strategies]);
  const totalPnl = useMemo(() => Array.isArray(strategies) ? strategies.reduce((sum, s) => sum + (Number.isFinite(Number(s.pnl)) ? Number(s.pnl) : 0), 0) : 0, [strategies]);
  const avgWinRate = useMemo(() => totalStrategies
    ? strategies.reduce((sum, s) => sum + (Number.isFinite(Number(s.wr)) ? Number(s.wr) : 0), 0) / totalStrategies
    : 0, [strategies, totalStrategies]);
  const visibleStrategies = useMemo(() => {
    if (!Array.isArray(strategies)) return [];
    return strategies.filter((s) => {
      const statusMatch = filterStatus === "all" || s.status === filterStatus;
      const envMatch = filterEnvironment === "all" || s.environment === filterEnvironment;
      return statusMatch && envMatch;
    });
  }, [strategies, filterStatus, filterEnvironment]);
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
   */
  const handleArchiveStrategy = async (id, name) => {
    if (isProcessing[id]) return;
    if (!window.confirm(archiveConfirmMessage(name))) return;
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

  const handleRenameStrategy = async (id, currentName) => {
    if (isProcessing[id]) return;
    const newName = window.prompt("Enter new strategy name:", currentName);
    if (!newName || newName.trim() === "" || newName === currentName) return;
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
      const data = await endpoints.strategies.rename(id, newName);
      console.log("📊 RENAME RESPONSE:", data);
      // Reload strategies
      const payload = await endpoints.strategies.list();
      const rows = Array.isArray(payload) ? payload : payload?.data || payload?.strategies || [];
      if (Array.isArray(rows)) setStrategies(normalizeStrategies(rows));
    } catch (err) {
      console.error("📊 RENAME ERROR:", err.message);
    } finally {
      setProcessingFor(id, false);
    }
  };

  if (view === "builder") return <StrategyBuilder onBack={() => setView("library")} strategy={editingStrategy} onBacktest={(payload) => navigate("/app/backtest", { state: { strategy: payload } })} />;

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1, background: "#080a0e", color: "#e2e8f0" }}>
      {isLoading && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: C.t3, fontFamily: "monospace" }}>
          Loading strategies...
        </div>
      )}
      {!isLoading && (
        <>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
            <div>
              <h1 style={{ color: "#f8fafc", fontWeight: 900, fontSize: 20, letterSpacing: -0.5, margin: 0 }}>Strategy Library</h1>
              <p style={{ color: "#64748b", fontSize: 10, fontFamily: "monospace", marginTop: 3 }}>Manage, backtest, and deploy your algorithmic strategies</p>
            </div>
            <div style={{ display: "flex", gap: 8, position: "relative" }}>
              <Button variant="outline" size="sm" icon={Filter} onClick={() => setFilterOpen(v => !v)}>Filter</Button>
              {filterOpen && (
                <div style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 20, background: "#0c1017", border: `1px solid #1e293b`, borderRadius: 8, padding: 6, minWidth: 180 }}>
                  <div style={{ marginBottom: 8, borderBottom: `1px solid #1e293b`, paddingBottom: 4 }}>
                    <span style={{ color: "#64748b", fontSize: 9, fontFamily: "monospace" }}>STATUS</span>
                  </div>
                  {[
                    { id: "all", label: "All" },
                    { id: "running", label: "Running" },
                    { id: "paused", label: "Paused" },
                    { id: "draft", label: "Draft" },
                    { id: "backtesting", label: "Backtesting" },
                    { id: "stopped", label: "Stopped" },
                    { id: "failed", label: "Failed" },
                  ].map(opt => (
                    <button
                      key={opt.id}
                      onClick={() => {
                        setFilterStatus(opt.id);
                        setFilterOpen(false);
                      }}
                      style={{ width: "100%", textAlign: "left", background: filterStatus === opt.id ? "rgba(0,212,255,0.15)" : "transparent", color: filterStatus === opt.id ? "#00d4ff" : "#94a3b8", border: `1px solid ${filterStatus === opt.id ? "rgba(0,212,255,0.3)" : "transparent"}`, borderRadius: 6, padding: "5px 8px", fontSize: 10, fontFamily: "monospace", cursor: "pointer", marginBottom: 4 }}
                    >
                      {opt.label}
                    </button>
                  ))}
                  <div style={{ marginTop: 8, marginBottom: 8, borderBottom: `1px solid #1e293b`, paddingBottom: 4 }}>
                    <span style={{ color: "#64748b", fontSize: 9, fontFamily: "monospace" }}>ENVIRONMENT</span>
                  </div>
                  {[
                    { id: "all", label: "All" },
                    { id: "paper", label: "Paper" },
                    { id: "live", label: "Live" },
                  ].map(opt => (
                    <button
                      key={`env-${opt.id}`}
                      onClick={() => {
                        setFilterEnvironment(opt.id);
                        setFilterOpen(false);
                      }}
                      style={{ width: "100%", textAlign: "left", background: filterEnvironment === opt.id ? "rgba(0,212,255,0.15)" : "transparent", color: filterEnvironment === opt.id ? "#00d4ff" : "#94a3b8", border: `1px solid ${filterEnvironment === opt.id ? "rgba(0,212,255,0.3)" : "transparent"}`, borderRadius: 6, padding: "5px 8px", fontSize: 10, fontFamily: "monospace", cursor: "pointer", marginBottom: 4 }}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              )}
              <Button variant="primary" size="sm" icon={Plus} onClick={() => setView("builder")}>New Strategy</Button>
            </div>
          </div>

          {/* Stats row */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10, marginBottom: 16 }}>
            {[
              { l: "Total Strategies", v: String(totalStrategies), I: Layers, c: "#00d4ff" },
              { l: "Running", v: String(runningStrategies), I: Radio, c: "#10b981" },
              { l: "Total P&L", v: `${totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)}%`, I: TrendingUp, c: totalPnl >= 0 ? "#10b981" : "#ef4444" },
              { l: "Avg Win Rate", v: `${avgWinRate.toFixed(1)}%`, I: Target, c: "#8b5cf6" },
            ].map(s => (
              <Card key={s.l} className="p-4 bg-[#0c1017] border-[#1e293b]">
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                  <span style={{ color: "#64748b", fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>{s.l}</span>
                  <s.I size={12} style={{ color: s.c }} />
                </div>
                {/* Counts, a P&L percentage and a win rate: mono, like the equivalent
                    figures on Portfolio, Trade History and Security Logs. Their labels
                    above already are. Not a temporary fallback — this is the permanent
                    treatment for a numeric cell (design.md §6.6). */}
                <div className="font-mono" style={{ color: "#f8fafc", fontSize: 20, fontWeight: 900 }}>{s.v}</div>
              </Card>
            ))}
          </div>

          {/* Archive refusal (task 16.3). Requirement 3.1 makes the server identify every
              blocking deployment by identifier and state; Requirement 2.10 makes the page
              say those deployments must be stopped first. Both are rendered from the
              response itself — the message is the server's own wording, and the list below
              it is the `blocking_deployments` it named. */}
          {archiveError && (
            <div
              role="alert"
              data-testid="archive-error"
              style={{ background: "rgba(239,68,68,0.12)", border: "1px solid #ef4444", borderRadius: 8, padding: "10px 12px", marginBottom: 16, display: "flex", gap: 8, alignItems: "flex-start" }}
            >
              <AlertTriangle size={14} style={{ color: "#ef4444", flexShrink: 0, marginTop: 2 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ color: "#ef4444", fontSize: 10, fontFamily: "monospace", letterSpacing: 1.5, fontWeight: 900, textTransform: "uppercase", marginBottom: 4 }}>
                  {archiveError.blocked ? "ARCHIVE BLOCKED" : "ARCHIVE FAILED"}
                  {archiveError.strategyName ? ` — ${archiveError.strategyName}` : ""}
                </div>
                <div style={{ color: "#fca5a5", fontSize: 11, lineHeight: 1.5 }}>{archiveError.message}</div>
                {archiveError.blockingDeployments.length > 0 && (
                  <ul data-testid="archive-blocking-deployments" style={{ margin: "8px 0 0", paddingLeft: 18, color: "#fca5a5", fontSize: 10, fontFamily: "monospace", lineHeight: 1.7 }}>
                    {archiveError.blockingDeployments.map((d, i) => (
                      <li key={d.deploymentId ?? `${d.source || "own"}-${i}`}>
                        {describeBlockingDeployment(d)}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <button
                onClick={() => setArchiveError(null)}
                aria-label="Dismiss archive error"
                style={{ background: "transparent", border: "1px solid rgba(239,68,68,0.4)", color: "#ef4444", borderRadius: 4, padding: "2px 8px", fontSize: 9, fontFamily: "monospace", cursor: "pointer", fontWeight: 700, flexShrink: 0 }}
              >
                Dismiss
              </button>
            </div>
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

          {/* Strategy Cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12 }}>
            {Array.isArray(visibleStrategies) && visibleStrategies.map(s => {
              const isTarget = focusedStrategyId && String(s.id) === String(focusedStrategyId);
              const isFailed = s.status === "failed" || s.health === "error";

              return (
                <Card
                  key={s.id}
                  className={`p-4 transition-all cursor-pointer bg-[#0c1017] ${isTarget ? 'border-[#00d4ff] ring-1 ring-[#00d4ff]' : isFailed ? 'border-[#ef4444]' : 'border-[#1e293b] hover:border-cyan-500/20'}`}
                  onClick={() => navigate(`/app/strategies/${s.id}`)}
                >
                  {/* Target Focus Banner */}
                  {isTarget && (
                    <div style={{ background: "rgba(0,212,255,0.15)", border: "1px solid #00d4ff", borderRadius: 4, padding: "2px 8px", fontSize: 9, fontFamily: "monospace", color: "#00d4ff", fontWeight: 800, marginBottom: 8, textAlign: "center" }}>
                      ★ FOCUSED TARGET STRATEGY
                    </div>
                  )}

                  {/* Header: Name, Status, Version */}
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <StatusDot status={s.status} />
                      <span style={{ color: "#f8fafc", fontWeight: 900, fontSize: 12 }}>{s.name}</span>
                    </div>
                    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                      <Tag2 c="gray" style={{ fontSize: 9 }}>v{s.current_version || "1.0"}</Tag2>
                      <Tag2 c={s.status === "running" ? "green" : s.status === "backtesting" ? "cyan" : s.status === "paused" ? "orange" : s.status === "draft" ? "gray" : "red"}>
                        {s.status}
                      </Tag2>
                    </div>
                  </div>

                  {/* Failure Alert Banner */}
                  {isFailed && (
                    <div style={{ background: "rgba(239,68,68,0.12)", border: "1px solid #ef4444", borderRadius: 6, padding: "6px 8px", marginBottom: 10, fontSize: 10, fontFamily: "monospace", color: "#ef4444", display: "flex", alignItems: "center", gap: 6 }}>
                      <AlertTriangle size={14} />
                      <span style={{ flex: 1 }}>{s.errorMessage || "Strategy execution halted due to error."}</span>
                      <button
                        onClick={(e) => { e.stopPropagation(); navigate(`/app/signal-trace?strategy_id=${s.id}`); }}
                        style={{ background: "#ef4444", border: "none", color: "#fff", borderRadius: 4, padding: "2px 6px", fontSize: 9, cursor: "pointer", fontWeight: 700 }}
                      >
                        Trace
                      </button>
                    </div>
                  )}

                  {/* Meta Tags: Exchange, Pair, Timeframe, Environment */}
                  <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
                    <Tag2 c="cyan">{s.pair}</Tag2>
                    <Tag2 c="purple">{s.type}</Tag2>
                    <Tag2 c="gold">{s.tf}</Tag2>
                    <Tag2 c={s.environment === "live" ? "red" : "green"}>{s.environment || "paper"}</Tag2>
                    {s.worker_region && <Tag2 c="blue"><Globe size={10} style={{ marginRight: 2 }} />{s.worker_region}</Tag2>}
                  </div>

                  {/* Health and Worker Status */}
                  <div style={{ display: "flex", gap: 6, marginBottom: 10, fontSize: 9, fontFamily: "monospace", color: "#64748b" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                      <Activity size={10} />
                      {/* Always a computed value, so no `|| "healthy"` default: that
                          fallback was a second fabrication of the same kind. */}
                      <span>Health: {s.health}</span>
                    </div>
                    {s.is_running && (
                      <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                        <Server size={10} />
                        <span>Worker: Active</span>
                      </div>
                    )}
                    {s.exchange_status && (
                      <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                        <Shield size={10} />
                        <span>Exchange: {s.exchange_status}</span>
                      </div>
                    )}
                  </div>

                  {/* Performance Metrics */}
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 6, marginBottom: 10 }}>
                    {[
                      { l: "P&L", v: `${s.pnl >= 0 ? "+" : ""}${s.pnl}%`, c: s.pnl >= 0 ? "#10b981" : "#ef4444" },
                      { l: "Win Rate", v: `${s.wr}%`, c: "#00d4ff" },
                      { l: "Max DD", v: `${s.dd}%`, c: "#ef4444" },
                    ].map(m => (
                      <div key={m.l} style={{ background: "#080a0e", borderRadius: 6, padding: "6px 8px", textAlign: "center" }}>
                        <div style={{ color: "#64748b", fontSize: 8, fontFamily: "monospace", letterSpacing: 2, marginBottom: 2 }}>{m.l}</div>
                        <div style={{ color: m.c, fontSize: 12, fontWeight: 900, fontFamily: "monospace" }}>{m.v}</div>
                      </div>
                    ))}
                  </div>

                  {/* Timeline */}
                  <div style={{ display: "flex", gap: 12, marginBottom: 10, fontSize: 9, fontFamily: "monospace", color: "#64748b" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                      <Clock size={10} />
                      <span>Created: {s.created_at ? new Date(s.created_at).toLocaleDateString() : "N/A"}</span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                      <RefreshCw size={10} />
                      <span>Updated: {s.updated_at ? new Date(s.updated_at).toLocaleDateString() : "N/A"}</span>
                    </div>
                  </div>

                  <ProgressBar v={s.wr} max={100} color={s.pnl >= 0 ? "#10b981" : "#ef4444"} h={3} />

                  {/* Action Buttons */}
                  <div style={{ display: "flex", gap: 4, marginTop: 10, flexWrap: "wrap" }}>
                    <Button variant="ghost" size="xs" icon={Edit2} onClick={e => { e.stopPropagation(); setEditingStrategy(s); setView("builder"); }} disabled={!!isProcessing[s.id]}>Edit</Button>
                    <Button variant="ghost" size="xs" icon={Copy} onClick={e => { e.stopPropagation(); handleCloneStrategy(s.id); }} disabled={!!isProcessing[s.id]}>Clone</Button>
                    <Button variant="ghost" size="xs" icon={Settings} onClick={e => { e.stopPropagation(); handleRenameStrategy(s.id, s.name); }} disabled={!!isProcessing[s.id]}>Rename</Button>
                    <Button variant="ghost" size="xs" icon={BarChart2} onClick={e => { e.stopPropagation(); navigate(`/app/backtest?strategy_id=${s.id}`, { state: { strategy: s } }); }} disabled={!!isProcessing[s.id]}>Backtest</Button>
                    <Button variant="ghost" size="xs" icon={Activity} onClick={e => { e.stopPropagation(); navigate(`/app/signal-trace?strategy_id=${s.id}`); }} disabled={!!isProcessing[s.id]}>Trace</Button>
                    {s.status === "running"
                      ? <Button variant="ghost" size="xs" icon={Pause} onClick={e => { e.stopPropagation(); handlePauseStrategy(s.id); }} disabled={!!isProcessing[s.id]}>Pause</Button>
                      : <Button variant="success" size="xs" icon={Play} onClick={e => { e.stopPropagation(); handleOpenDeployModal(s); }} disabled={!!isProcessing[s.id]}>Deploy</Button>}
                    <Button variant="danger" size="xs" icon={Trash2} cls="ml-auto" title="Archive strategy" aria-label={`Archive ${s.name}`} onClick={e => { e.stopPropagation(); handleArchiveStrategy(s.id, s.name); }} disabled={!!isProcessing[s.id]} />
                  </div>
                </Card>
              );
            })}
            {/* Add new card */}
            <div style={{ background: "#0c1017", border: `2px dashed #1e293b`, borderRadius: 12, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8, padding: 32, cursor: "pointer", minHeight: 220, transition: "all 0.2s" }}
              onClick={() => setView("builder")} className="hover:border-cyan-500/30 hover:bg-cyan-500/3">
              <PlusCircle size={24} style={{ color: "#64748b" }} />
              <span style={{ color: "#64748b", fontSize: 11, fontFamily: "monospace" }}>Create New Strategy</span>
            </div>
          </div>
        </>
      )}

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
    </div>
  );
}
